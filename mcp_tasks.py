"""MCP Tasks surface for Subway workers.

Antigravity speaks the Tasks extension natively (``tasks/get``, ``tasks/result``,
``tasks/list``, ``tasks/cancel`` plus ``notifications/tasks/status``), so a worker can be
shown and polled in the chat that spawned it. That is the native spawn-and-feedback path:
the chat gets a task it can render and read, instead of Subway trying to wake a chat later.

Statuses are the protocol's, not Subway's: ``working``, ``input_required``, ``completed``,
``failed``, ``cancelled``. The last three are terminal and never transition again.

Kept dependency-light and import-safe: ``server`` is imported inside the functions so this
module can be imported from ``cli_tools`` without a cycle.
"""
from __future__ import annotations
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

TASK_TTL_MS = 86400000
POLL_INTERVAL_MS = 3000
TASK_PREFIX = "task_"
TERMINAL_STATUSES = ("completed", "failed", "cancelled")

# Worker status -> protocol status. Anything unknown stays "working" rather than guessing
# a terminal state, because a wrong terminal silently hides a live worker.
STATUS_MAP = {
    "queued": "working",
    "pending_approval": "working",
    "launching": "working",
    "running": "working",
    "stopping": "working",
    "awaiting_permission": "input_required",
    "needs_input": "input_required",
    "input_required": "input_required",
    "permission_requested": "input_required",
    "completed": "completed",
    "failed": "failed",
    "budget_stopped": "failed",
    "stopped": "failed",
    "timed_out": "failed",
    "rejected": "cancelled",
    "cancelled": "cancelled",
}

_lock = threading.Lock()
_created: Dict[str, str] = {}      # taskId -> ISO first-seen time
_terminal: Dict[str, str] = {}     # taskId -> terminal status, once reached
_forced: Dict[str, str] = {}       # taskId -> status set by tasks/cancel
_sender = None


def set_sender(fn) -> None:
    """Give the module a way to push notifications/tasks/status through the MCP transport."""
    global _sender
    _sender = fn


def worker_id_for(task_id: str) -> str:
    tid = str(task_id or "").strip()
    return tid[len(TASK_PREFIX):] if tid.startswith(TASK_PREFIX) else tid


def task_id_for(worker_id: str) -> str:
    wid = str(worker_id or "").strip()
    return wid if wid.startswith(TASK_PREFIX) else f"{TASK_PREFIX}{wid}"


def _iso(ts: Any) -> str:
    """ISO-8601 UTC. Accepts epoch seconds or an already-ISO string."""
    if isinstance(ts, str) and ts.strip():
        return ts.strip()
    try:
        value = float(ts)
    except (TypeError, ValueError):
        value = time.time()
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _load_worker(worker_id: str) -> Optional[Dict[str, Any]]:
    try:
        import server as _s
        meta = _s._load_worker(worker_id)
    except (ValueError, OSError):
        # ValueError is _worker_file rejecting an id that is not worker_<6 hex>.
        # Narrow on purpose: a refactor bug should surface, not read as "no such task".
        return None
    return meta if isinstance(meta, dict) else None


def map_status(worker_status: Any) -> str:
    return STATUS_MAP.get(str(worker_status or "").strip().lower(), "working")


def _created_at(task_id: str, meta: Dict[str, Any]) -> str:
    with _lock:
        known = _created.get(task_id)
        if known:
            return known
        stamp = _iso(meta.get("created_at") or meta.get("started_at") or meta.get("spawned_at"))
        _created[task_id] = stamp
        return stamp


def task_object(worker_id: str, meta: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The protocol task object for a worker, or None when that worker does not exist."""
    meta = meta if isinstance(meta, dict) else _load_worker(worker_id)
    if not meta:
        return None
    task_id = task_id_for(worker_id)
    status = map_status(meta.get("status"))
    with _lock:
        if task_id in _forced:
            status = _forced[task_id]
        elif task_id in _terminal:
            status = _terminal[task_id]
    obj: Dict[str, Any] = {
        "taskId": task_id,
        "status": status,
        "ttl": TASK_TTL_MS,
        "createdAt": _created_at(task_id, meta),
        "lastUpdatedAt": _iso(meta.get("updated_at") or meta.get("finished_at") or time.time()),
        "pollInterval": POLL_INTERVAL_MS,
    }
    message = str(meta.get("reason") or meta.get("last_note") or "").strip()
    if message:
        obj["statusMessage"] = message[:300]
    return obj


def task_object_for_id(task_id: str) -> Optional[Dict[str, Any]]:
    wid = worker_id_for(task_id)
    return task_object(wid) if wid else None


def list_tasks(limit: int = 50) -> List[Dict[str, Any]]:
    """Known workers as tasks. Reads records, not worker_listing, which returns prose."""
    try:
        import server as _s
        metas = _s._all_workers(include_archive=True) or []
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for meta in metas[:limit]:
        if isinstance(meta, dict) and meta.get("id"):
            obj = task_object(str(meta["id"]), meta)
            if obj:
                out.append(obj)
    return out


def result_text(worker_id: str) -> str:
    """The worker's own report, falling back to its log tail."""
    meta = _load_worker(worker_id) or {}
    report = meta.get("report") or meta.get("worker_report")
    if isinstance(report, str) and report.strip():
        return report
    path = meta.get("log_file")
    if path and os.path.exists(path):
        try:
            import server as _s
            return _s._log_tail(path, 200)
        except Exception:
            pass
    return f"Worker {worker_id} has not reported yet."


def task_result(task_id: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": result_text(worker_id_for(task_id))}]}


def cancel(task_id: str) -> Optional[Dict[str, Any]]:
    """Mark cancelled (terminal) and detach from the worker; the worker keeps running."""
    wid = worker_id_for(task_id)
    if not wid:
        return None
    task_id = task_id_for(wid)
    with _lock:
        _forced[task_id] = "cancelled"
        _terminal[task_id] = "cancelled"
    return task_object_for_id(task_id)


def notify_status(worker_id: str) -> bool:
    """Push the current task state to the client. Terminal states never change again."""
    obj = task_object(worker_id)
    if not obj or _sender is None:
        return False
    task_id = obj["taskId"]
    with _lock:
        already = _terminal.get(task_id)
        if already:
            return False
        if obj["status"] in TERMINAL_STATUSES:
            _terminal[task_id] = obj["status"]
    try:
        _sender({"jsonrpc": "2.0", "method": "notifications/tasks/status", "params": obj})
        return True
    except Exception:
        return False


def reset_for_tests() -> None:
    with _lock:
        _created.clear()
        _terminal.clear()
        _forced.clear()
