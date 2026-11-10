from __future__ import annotations

import base64
import concurrent.futures
import contextlib
import contextvars
import datetime as _dt
import difflib
import glob
import hashlib
import hmac
import http.server
import json
import math
import os
import re
import secrets
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
try:
    import ctypes
except ImportError:
    ctypes = None  # type: ignore[assignment]
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

import terminals
import native_terminals
import threads

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

import server as _s

from .routing import estimate_complexity

def _headless_permission_denial(log_text: str) -> Optional[str]:
    """The permission name refused, if the log shows a headless-mode denial the worker could not recover from
    (e.g. Antigravity's auto_safe headless mode: 'a tool required the "command" permission that headless mode
    cannot prompt for'). None otherwise."""
    m = _s._HEADLESS_DENIAL_RE.search(log_text or "")
    return m.group(1) if m else None

def _settle_worker_result(result: Dict[str, Any], meta: Dict[str, Any], no_changes: bool, reply: str) -> None:
    if result.get("status") != "completed" or not no_changes:
        return
    # The reply is the report (result["response"]); reason only says how the run ended.
    note = str(result.get("reason") or "").strip()
    worker_id = meta.get("id")
    has_children = bool(worker_id and any(w.get("parent_id") == worker_id for w in _s._all_workers()))
    if "subagents" in (meta.get("capabilities") or []) or has_children or not _s._worker_expected_to_edit(meta):
        return
    result["status"] = "failed"
    result["reason"] = f"No changes made. {note}".strip()

def _determine_outcome(meta: Dict[str, Any], result: Dict[str, Any]) -> str:
    if "outcome" in result:
        return result["outcome"]
    if "outcome" in meta:
        return meta["outcome"]
    status = result.get("status") or meta.get("status", "")
    reason = str(result.get("reason") or meta.get("reason") or "")
    if status == "budget_stopped" or "budget" in reason.lower():
        return "budget_stopped"
    if "no files changed" in reason.lower() or "no changes made" in reason.lower():
        return "no_change"
    if status == "completed" or "patch applied" in reason.lower():
        return "landed"
    return "failed"

def _record_read_guard_hit(meta: Dict[str, Any]) -> None:
    stats_file = os.path.join(_s.SUBWAY_DIR, "model_stats.json")
    with _s._state_lock("model_stats"):
        stats = []
        if os.path.exists(stats_file):
            with contextlib.suppress(Exception):
                with open(stats_file, "r", encoding="utf-8") as f:
                    stats = json.load(f)
        if not isinstance(stats, list):
            stats = []
        wid = meta.get("id")
        found = False
        for s in stats:
            if wid and s.get("worker_id") == wid:
                s["read_guard_hits"] = int(s.get("read_guard_hits") or 0) + 1
                found = True
                break
        if not found:
            entry = {
                "worker_id": wid,
                "provider": str(meta.get("provider") or ""),
                "model": str(meta.get("model") or ""),
                "role": meta.get("role") or "",
                "read_guard_hits": 1,
                "timestamp": time.time(),
            }
            stats.append(entry)
        _s._write_json_atomic(stats_file, stats)

def _record_worker_stat(meta: Dict[str, Any], result: Dict[str, Any]) -> None:
    provider = meta.get("provider") or ""
    model = meta.get("model") or ""
    if not provider and not model:
        return
    role = meta.get("role") or ""
    prompt = meta.get("prompt") or ""
    c = _s.estimate_complexity(prompt, role=role)
    tier = c.get("tier", "medium")
    jt = _s._job_type(tier, role)
    outcome = _s._determine_outcome(meta, result)
    entry = {
        "provider": provider,
        "model": model,
        "job_type": jt,
        "tier": tier,
        "role": role,
        "outcome": outcome,
        "timestamp": time.time(),
    }
    if meta.get("read_guard_hits"):
        entry["read_guard_hits"] = int(meta["read_guard_hits"])
    if meta.get("id"):
        entry["worker_id"] = meta["id"]
    stats_file = os.path.join(_s.SUBWAY_DIR, "model_stats.json")
    with _s._state_lock("model_stats"):
        stats = []
        if os.path.exists(stats_file):
            with contextlib.suppress(Exception):
                with open(stats_file, "r", encoding="utf-8") as f:
                    stats = json.load(f)
        if not isinstance(stats, list):
            stats = []
        wid = meta.get("id")
        updated = False
        if wid:
            for s in stats:
                if s.get("worker_id") == wid and "outcome" not in s:
                    s.update(entry)
                    updated = True
                    break
        if not updated:
            stats.append(entry)
        _s._write_json_atomic(stats_file, stats)
    # Adaptive weights update (Item 20 §3.1.C & Flow 4)
    record_worker_outcome(meta, result)

def load_routing_weights() -> Dict[str, Any]:
    weights_file = os.path.join(_s.SUBWAY_DIR, "routing_weights.json")
    if os.path.exists(weights_file):
        with contextlib.suppress(Exception):
            with open(weights_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if "edges" not in data or not isinstance(data["edges"], dict):
                    data["edges"] = {}
                return data
    return {"version": 1, "updated_at": time.time(), "edges": {}}

def record_worker_outcome(meta: Dict[str, Any], result: Dict[str, Any]) -> None:
    provider = meta.get("provider") or ""
    model = meta.get("model") or ""
    if not provider and not model:
        return
    role = (meta.get("role") or "").strip()
    prompt = meta.get("prompt") or ""
    c = _s.estimate_complexity(prompt, role=role)
    tier = (meta.get("tier") or c.get("tier", "medium")).lower()

    handoff_from = meta.get("handoff_from") or {}
    from_role = str(handoff_from.get("role") or meta.get("from_role") or "*").strip().lower()
    to_role = role.lower() if role else "worker"
    role_category = to_role
    complexity = tier

    outcome = _s._determine_outcome(meta, result)
    verdict = str(result.get("verdict") or meta.get("verdict") or "").strip().lower()

    # Base outcome reward (Flow 4 step 2)
    if outcome == "landed":
        reward = 0.7
    elif outcome == "no_change":
        reward = 0.2
    else:
        reward = 0.0

    if "approve" in verdict:
        reward += 0.3
    elif "reject" in verdict:
        reward *= 0.2

    # Efficiency bonus/penalty if usage/duration present
    duration_s = result.get("duration_s") or meta.get("duration_s")
    if duration_s is None:
        with contextlib.suppress(Exception):
            s_at = meta.get("started_at")
            c_at = meta.get("completed_at") or result.get("completed_at")
            if s_at and c_at:
                duration_s = round((_dt.datetime.fromisoformat(c_at) - _dt.datetime.fromisoformat(s_at)).total_seconds(), 1)
    if duration_s is not None:
        try:
            duration_s = float(duration_s)
        except (ValueError, TypeError):
            duration_s = None

    tokens = None
    usage = result.get("usage") or meta.get("usage")
    if isinstance(usage, dict):
        with contextlib.suppress(Exception):
            tokens = float(usage.get("input", 0) + usage.get("output", 0) + usage.get("thinking", 0))
    if tokens is None:
        with contextlib.suppress(Exception):
            raw_tok = meta.get("tokens_used") or result.get("tokens_used") or meta.get("tokens") or result.get("tokens")
            if raw_tok is not None:
                tokens = float(raw_tok)

    reward = max(0.0, min(1.0, reward))

    weights_file = os.path.join(_s.SUBWAY_DIR, "routing_weights.json")
    with _s._state_lock("model_stats"):
        data = load_routing_weights()
        edges = data.get("edges", {})
        if not isinstance(edges, dict):
            edges = {}
            data["edges"] = edges

        key_slash = f"{role_category}/{complexity}/{provider}:{model}"
        key_arrow = f"{from_role}->{to_role}|{provider}/{model}" if from_role and from_role != "*" else ""

        existing = edges.get(key_slash) or (edges.get(key_arrow) if key_arrow else None) or {}
        old_runs = int(existing.get("runs") or existing.get("run_count") or 0)
        old_ema_reward = float(existing.get("ema_reward") if existing.get("ema_reward") is not None else existing.get("reward", 0.5))
        old_successes = int(existing.get("successes") or 0)
        old_approved = int(existing.get("approved") or 0)
        old_rejected = int(existing.get("rejected") or 0)
        old_ema_tokens = existing.get("ema_tokens")
        old_ema_dur = existing.get("ema_duration_s")

        alpha = 0.25
        if old_runs == 0:
            new_ema_reward = round(reward, 4)
        else:
            new_ema_reward = round(alpha * reward + (1.0 - alpha) * old_ema_reward, 4)

        new_weight = round(max(0.25, min(1.75, 0.5 + new_ema_reward)), 2)
        new_runs = old_runs + 1
        new_successes = old_successes + (1 if outcome == "landed" else 0)
        new_approved = old_approved + (1 if "approve" in verdict else 0)
        new_rejected = old_rejected + (1 if "reject" in verdict else 0)

        new_ema_tokens = round(alpha * tokens + (1.0 - alpha) * float(old_ema_tokens), 1) if (tokens is not None and old_ema_tokens is not None) else (tokens if tokens is not None else old_ema_tokens)
        new_ema_dur = round(alpha * duration_s + (1.0 - alpha) * float(old_ema_dur), 1) if (duration_s is not None and old_ema_dur is not None) else (duration_s if duration_s is not None else old_ema_dur)

        win_rate = int(round((new_successes / new_runs) * 100)) if new_runs > 0 else 0
        rev_tot = new_approved + new_rejected
        app_rate = int(round((new_approved / rev_tot) * 100)) if rev_tot > 0 else 0

        rev_text = f", {new_approved}/{rev_tot} review approved ({app_rate}% approval rate)" if rev_tot > 0 else (f", {new_approved} review approved" if new_approved > 0 else "")
        eff_parts = []
        if new_ema_tokens is not None:
            eff_parts.append(f"avg {new_ema_tokens/1000.0:.1f}k tok")
        if new_ema_dur is not None:
            eff_parts.append(f"{new_ema_dur:.1f}s")
        eff_text = f", { ' / '.join(eff_parts) }" if eff_parts else ""

        last_explanation = f"weight {new_weight:.2f} from {new_runs} runs ({new_successes}/{new_runs} landed ({win_rate}% win rate){rev_text}{eff_text})"

        now = time.time()
        edge_entry = {
            "from_role": from_role,
            "to_role": to_role,
            "role": to_role,
            "role_category": role_category,
            "complexity": complexity,
            "tier": complexity,
            "provider": provider,
            "model": model,
            "runs": new_runs,
            "run_count": new_runs,
            "successes": new_successes,
            "approved": new_approved,
            "rejected": new_rejected,
            "ema_reward": new_ema_reward,
            "reward": new_ema_reward,
            "ema_tokens": new_ema_tokens,
            "ema_duration_s": new_ema_dur,
            "weight": new_weight,
            "last_used_at": now,
            "updated_at": now,
            "last_explanation": last_explanation,
        }

        edges[key_slash] = edge_entry
        if key_arrow:
            edges[key_arrow] = edge_entry
        data["updated_at"] = now
        _s._write_json_atomic(weights_file, data)

def _matches_job_type(record: Dict[str, Any], tier: str, role: str) -> bool:
    rec_jt = record.get("job_type")
    jt = _s._job_type(tier, role)
    if rec_jt and rec_jt == jt:
        return True
    if record.get("tier") == tier and (record.get("role") or "").lower() == (role or "").lower():
        return True
    if rec_jt and (rec_jt == f"{tier}:{role}" or rec_jt == f"{tier}_{role}" or rec_jt == tier):
        return True
    return False

def _update_worker_status(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Settle a running worker from its supervisor's exit record, the only reliable result."""
    if meta.get("status") not in ("running", "stopping"):
        return meta
    wid = meta["id"]
    result: Optional[Dict[str, Any]] = None
    with contextlib.suppress(OSError, ValueError):
        with open(_s._exit_file(wid), "r", encoding="utf-8") as f:
            result = json.load(f)
    if result is None:
        if meta.get("pid") and _s._pid_alive(int(meta["pid"])):
            return meta
        reason = "supervisor disappeared before reporting"
        with contextlib.suppress(Exception):
            tail = _s._log_tail(_s._supervisor_log_file(wid), 12)
            if tail and str(tail).strip():
                reason += f". Supervisor stderr: {_s.one_line(str(tail).strip()[-600:], 600)}"
        result = {"status": "failed", "exit_code": None, "reason": reason}

    should_record = False
    with _s._state_lock("workers"):
        current = _s._load_worker(wid) or meta
        if current.get("status") in ("running", "stopping"):
            current.update({
                "status": result.get("status", "failed"),
                "exit_code": result.get("exit_code"),
                "completed_at": _dt.datetime.now().astimezone().isoformat(),
                # report is the agent's own final message, whatever the outcome; reason is the
                # supervisor's note on how the run ended. A log is never stored as a report.
                "report": str(result.get("response") or "").strip(),
                "reason": str(result.get("reason") or "").strip(),
            })
            _s._save_worker(current)
            should_record = True
    if should_record and result is not None:
        # Activity ledger: the record just reached a terminal status with its
        # final usage; count it once (record_finished is idempotent per id).
        _s._record_activity_finished(current)
        status = str(current.get("status") or result.get("status") or "failed")
        message = f"Worker {wid} ({current.get('role') or 'worker'}) ended: {status}."
        # Roadmap 38a2: a state change on the live stream, so a phone can follow work
        # without polling /api/status. Non-blocking; a missing HUD is not an error.
        with contextlib.suppress(Exception):
            import sse_stream
            sse_stream.publish("worker", {
                "id": wid, "status": status, "role": current.get("role"),
                "provider": current.get("provider"), "exit_code": result.get("exit_code"),
                "track": current.get("track"), "thread": current.get("thread"),
            })
        # Tasks extension: a chat that spawned this worker holds the task, so it learns the
        # worker ended by polling or by this notification, without Subway waking the chat.
        # Terminal states are emitted once; mcp_tasks drops repeats.
        with contextlib.suppress(Exception):
            import mcp_tasks
            mcp_tasks.notify_status(wid)
        reason = result.get("reason") or current.get("reason")
        if reason:
            message += f" {reason}"
        iteration_id = current.get("iteration_id") or meta.get("iteration_id")
        if not iteration_id:
            if ((status in ("failed", "stopped", "timed_out", "budget_stopped") or current.get("priority") == "urgent")
                    and not result.get("rerouted_to") and not result.get("stall_alerted")):
                _s.notify_desktop_event(f"worker_ended|{wid}|{status}", "Subway", message[:500], route="workers")
            target_chat = current.get("launched_by") or current.get("thread") or current.get("thread_root")
            if target_chat:
                try:
                    from wake_driver import WakeEvent, wake_chat
                    wake_ev = WakeEvent(
                        kind=f"worker_{status}",
                        worker_id=wid,
                        chat_id=str(target_chat),
                        text=message[:500],
                        data={"status": status, "role": current.get("role"), "exit_code": result.get("exit_code")}
                    )
                    wake_chat(str(target_chat), wake_ev)
                except Exception as e:
                    print(f"[wake] {status} for {target_chat} failed: {e!r}", file=sys.stderr)
        _s._record_worker_stat(current, result)
    return current

def _all_workers(include_archive: bool = False) -> List[Dict[str, Any]]:
    _s._ensure_subway_dir()
    paths = glob.glob(os.path.join(_s.WORKERS_DIR, "worker_*.json"))
    if include_archive:
        paths += glob.glob(os.path.join(_s._archive_dir(), "worker_*.json"))
    records = []
    for fp in paths:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (ValueError, OSError):
            continue
        if isinstance(meta, dict) and _s.WORKER_ID_RE.match(str(meta.get("id", ""))):
            meta = _s._update_worker_status(meta)
            meta = _s._normalise_copilot_worker_record(meta)
            meta["archived"] = os.path.dirname(fp) != _s.WORKERS_DIR
            records.append(meta)
    records.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return records


TERMINAL_WORKER_STATUSES = ("completed", "failed", "stopped", "timed_out", "budget_stopped", "cancelled", "rejected")


def wait_for_worker(worker_id: str, timeout_seconds: float = 600.0, poll_seconds: float = 1.0) -> Dict[str, Any]:
    """Block until a worker reaches a terminal status, then return its record.

    This is the native subagent shape: one call that returns the worker's own report,
    instead of spawning it and hoping a later wake reaches some window. The caller's
    transport must be able to serve other traffic meanwhile, since the worker's own
    permission answers arrive through the same channel.
    """
    deadline = time.time() + max(0.0, float(timeout_seconds or 0))
    meta: Dict[str, Any] = {}
    while True:
        meta = _s._load_worker(worker_id) or meta
        if not meta:
            return {"id": worker_id, "status": "unknown", "report": "", "wait_timed_out": False}
        with contextlib.suppress(Exception):
            _update_worker_status(meta)   # settle from the supervisor's exit record
        meta = _s._load_worker(worker_id) or meta
        if str(meta.get("status") or "") in TERMINAL_WORKER_STATUSES:
            out = dict(meta)
            out["wait_timed_out"] = False
            return out
        if time.time() >= deadline:
            out = dict(meta)
            out["wait_timed_out"] = True
            return out
        time.sleep(poll_seconds)
