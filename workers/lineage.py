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

MAX_LINEAGE_DEPTH = 3


def _lineage_depth(worker_meta: Optional[Dict[str, Any]]) -> int:
    if not worker_meta:
        return 0
    if "lineage_depth" in worker_meta:
        try:
            return int(worker_meta["lineage_depth"])
        except (TypeError, ValueError):
            pass
    depth = 0
    curr = worker_meta
    visited = set()
    while curr and curr.get("parent_id"):
        pid = str(curr.get("parent_id"))
        if pid in visited:
            break
        visited.add(pid)
        depth += 1
        curr = _s._load_worker(pid)
    return depth

def _lineage_error(parent: Optional[Dict[str, Any]]) -> Optional[str]:
    if not parent:
        return None
    caps = parent.get("capabilities") or []
    if "subagents" not in caps:
        return f"Error: worker '{parent.get('id')}' lacks 'subagents' capability to spawn sub-workers."
    depth = _lineage_depth(parent)
    if depth >= MAX_LINEAGE_DEPTH:
        return f"Error: maximum sub-worker lineage depth ({MAX_LINEAGE_DEPTH}) reached."
    return None

def _lineage_parent() -> Optional[Dict[str, Any]]:
    """The running worker this call comes from, when a worker starts a sub-worker. None for chats."""
    parent_id = os.environ.get("SUBWAY_WORKER_ID") or ""
    provided_secret = os.environ.get("SUBWAY_WORKER_SECRET") or ""
    if not _s.WORKER_ID_RE.match(parent_id):
        return None
    parent = _s._load_worker(parent_id)
    if not parent or parent.get("status") not in ("launching", "running"):
        return None
    expected_secret = parent.get("secret") or ""
    if not expected_secret or not hmac.compare_digest(provided_secret, expected_secret):
        return None
    return parent

def _inherit_from_parent(parent: Optional[Dict[str, Any]], requested_mode: str, selected_mode: str) -> Tuple[str, str]:
    """A sub-worker takes its parent's permission mode by default, and can never be broader than the parent."""
    if not parent:
        return requested_mode, selected_mode
    parent_mode = str(parent.get("permission_mode") or "")
    if parent_mode in _s._PERMISSION_RANK:
        if not requested_mode or _s._PERMISSION_RANK.get(requested_mode, 0) > _s._PERMISSION_RANK[parent_mode]:
            requested_mode = parent_mode
    if selected_mode == "god" and parent.get("mode") != "god":
        selected_mode = "standard"
    return requested_mode, selected_mode

@contextlib.contextmanager
def _thread_parent(parent_id: str, kind: str, data: Optional[Dict[str, Any]] = None) -> Iterator[None]:
    """Mark the next t_spin_worker call in this scope as an edge from `parent_id` of the given kind."""
    token = _s._THREAD_PARENT.set((parent_id, kind, data))
    try:
        yield
    finally:
        _s._THREAD_PARENT.reset(token)

def _thread_record(thread_id: str, kind: str, node: str, peer: str = "", data: Optional[Dict[str, Any]] = None) -> None:
    """Append one thread record with secrets redacted. Never breaks or changes the action it records."""
    if not thread_id:
        return
    try:
        def redact(value: Any, depth: int = 0) -> Any:
            if depth > 8:
                return value
            if isinstance(value, str):
                return terminals.redact_text(value)
            if isinstance(value, dict):
                return {k: redact(v, depth + 1) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [redact(v, depth + 1) for v in value]
            return value
        d = dict(data or {})
        for k in ("requested_by", "paid_by"):
            if k in d and d[k] is not None:
                d[k] = str(d[k])
        threads.record(_s.SUBWAY_DIR, thread_id, kind, node, peer=peer, data=redact(d))
    except Exception:
        pass

def _thread_finished(worker_id: str, meta: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Record a worker's finish once per run, using the freshest stored meta. Never breaks the caller."""
    try:
        finished_meta = _s._load_worker(worker_id) or meta or {}
        finished_at = _dt.datetime.now().astimezone()
        duration_s = None
        started_at = str(finished_meta.get("started_at") or "")
        if started_at:
            with contextlib.suppress(ValueError, TypeError):
                duration_s = round((finished_at - _dt.datetime.fromisoformat(started_at)).total_seconds(), 1)
        _s._thread_record(finished_meta.get("thread_root") or _s._thread_root(finished_meta), "finished", worker_id, data={
            "status": (result or {}).get("status"), "exit_code": (result or {}).get("exit_code"),
            "reason": _s.one_line(str((result or {}).get("reason") or ""), 300),
            "rerouted_to": (result or {}).get("rerouted_to"),
            "usage": dict(finished_meta.get("usage") or {}),
            "model_used": finished_meta.get("model_used"),
            "created_at": finished_meta.get("created_at"),
            "completed_at": finished_at.isoformat(), "duration_s": duration_s})
    except Exception:
        pass
    try:
        _s.sync_caffeinate()  # stay-awake follows running workers
    except Exception:
        pass

def _thread_root(meta: Dict[str, Any], _depth: int = 0) -> str:
    """The initiator this worker belongs to: its stored root, else the first parent's root, else itself."""
    try:
        if not isinstance(meta, dict):
            return str(meta or "")
        try:
            from thread_state import get_thread_settings
            iid = str(get_thread_settings(str(meta.get("id") or "")).get("initiator_id") or "")
            if iid:
                return iid
        except Exception:
            pass
        stored = str(meta.get("thread_root") or "")
        if stored:
            return stored
        seen = {str(meta.get("id") or "")}
        if _depth < 10:
            for key in ("parent_id", "triggered_by", "rerouted_from"):
                pid = str(meta.get(key) or "")
                if pid and _s.WORKER_ID_RE.match(pid) and pid not in seen:
                    parent = _s._load_worker(pid)
                    if parent:
                        seen.add(pid)
                        root = _s._thread_root(parent, _depth + 1)
                        if root:
                            return root
        launched_by = str(meta.get("launched_by") or "")
        if launched_by and launched_by != "hud" and not _s.WORKER_ID_RE.match(launched_by):
            return launched_by
        return str(meta.get("id") or "")
    except Exception:
        return ""

def _thread_root_for_caller(caller_id: str) -> str:
    """A chat roots its own thread; a worker caller resolves to its thread's root."""
    caller_id = str(caller_id or "")
    if caller_id and _s.WORKER_ID_RE.match(caller_id):
        worker = _s._load_worker(caller_id)
        if worker:
            return _s._thread_root(worker)
    return caller_id

def _settings_snapshot(meta: Dict[str, Any]) -> Dict[str, Any]:
    """What a worker runs with, never its prompt or command (secrets and size stay out of the log)."""
    snapshot: Dict[str, Any] = {}
    for key in ("role", "provider", "model", "effort", "mode", "permission_mode", "capabilities",
                "timeout_seconds", "isolate", "private", "priority", "track", "review", "project_dir", "swarm_id"):
        if key in meta:
            snapshot[key] = meta.get(key)
    budget = meta.get("budget")
    if isinstance(budget, dict):
        snapshot["budget"] = {k: v for k, v in budget.items() if k != "preflight"}
    snapshot["route"] = _s.one_line(str(meta.get("route") or ""), 300)
    snapshot["steps"] = len(meta.get("then") or [])
    return snapshot

