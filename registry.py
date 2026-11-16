"""Multi-window registry and resolution helpers.

Extracted from server.py. Reads live server globals where needed for patching.
"""
from __future__ import annotations

import glob
import hmac
import json
import os
import re
import threading
import time
import urllib.parse
from typing import Any, Dict, Optional, Set, Tuple

from chat_readers import (
    _detect_self_uncached,
    _detected_chat,
    _id_matches,
    _newest,
    _project_names,
    _self_id,
    _short_id,
    all_chats,
    detect_self,
    resolve,
)

_SELF_TTL = 20.0
_orig_self_id = _self_id


# Worker session ids change slowly; a 5s TTL avoids re-reading every worker file per call.
_WORKER_IDS_TTL = 5.0
_WORKER_IDS_CACHE: Optional[Tuple[float, Set[str]]] = None
_WORKER_IDS_LOCK = threading.Lock()


def _worker_chat_session_ids() -> Set[str]:
    global _WORKER_IDS_CACHE
    now = time.monotonic()
    with _WORKER_IDS_LOCK:
        cached = _WORKER_IDS_CACHE
    if cached is not None and now - cached[0] < _WORKER_IDS_TTL:
        return set(cached[1])
    ids = _worker_chat_session_ids_uncached()
    with _WORKER_IDS_LOCK:
        _WORKER_IDS_CACHE = (time.monotonic(), set(ids))
    return ids


def _worker_chat_session_ids_uncached() -> Set[str]:
    import server
    from workers import _archive_dir
    ids: Set[str] = set()
    paths = glob.glob(os.path.join(server.WORKERS_DIR, "worker_*.json"))
    paths.extend(glob.glob(os.path.join(_archive_dir(), "worker_*.json")))
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                worker = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(worker, dict):
            continue
        for field in ("session_id", "copilot_session_id", "conversation", "thread"):
            session_id = worker.get(field)
            if session_id and str(session_id) not in ("", "None", "null"):
                ids.add(str(session_id))
        log_file = worker.get("log_file")
        if isinstance(log_file, str) and log_file and os.path.isfile(log_file):
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as lf:
                    first = lf.read(1024).split("\n", 1)[0]
                rec = json.loads(first)
                if isinstance(rec, dict) and rec.get("type") == "session" and isinstance(rec.get("id"), str):
                    ids.add(rec["id"])
            except Exception:
                pass
    launched_path = os.path.join(server.SUBWAY_DIR, "launched_sessions.json")
    if os.path.isfile(launched_path):
        try:
            with open(launched_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if item is not None:
                        ids.add(str(item))
            elif isinstance(data, dict):
                for key in data.keys():
                    if key is not None:
                        ids.add(str(key))
        except Exception:
            pass
    return ids


def _chat_is_in_worker_worktree(chat: Dict[str, Any]) -> bool:
    import server
    root = os.path.realpath(os.path.abspath(os.path.expanduser(
        os.path.join(server.SUBWAY_DIR, "worktrees")
    )))
    raw_paths = chat.get("_project_path") or []
    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]
    projects = chat.get("project")
    if isinstance(projects, str):
        raw_paths = list(raw_paths) + [part.strip() for part in projects.split(",") if part.strip()]
    for raw_path in raw_paths:
        if not isinstance(raw_path, str) or not raw_path:
            continue
        try:
            parsed = urllib.parse.urlparse(raw_path)
            path = urllib.parse.unquote(parsed.path) if parsed.scheme == "file" else raw_path
            candidate = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
            if os.path.commonpath((root, candidate)) == root and candidate != root:
                return True
        except (OSError, ValueError):
            continue
    return False


def _is_authorized_worker(worker_id: str, caller_id: str) -> Tuple[bool, Optional[str]]:
    """Returns (is_authorized, launcher_id) if worker_id is allowed for caller_id."""
    from workers import _load_worker
    if not worker_id:
        return False, None
    w = _load_worker(worker_id)
    launcher = w.get("launched_by") if w else None
    if _id_matches(worker_id, caller_id):
        return True, launcher
    if launcher and _id_matches(caller_id, launcher):
        return True, launcher
    if re.match(r"^worker_[0-9a-f]{6}$", worker_id):
        if os.environ.get("SUBWAY_WORKER_ID") == worker_id:
            w_sec = w.get("secret") if w else None
            if not w_sec or hmac.compare_digest(os.environ.get("SUBWAY_WORKER_SECRET", ""), w_sec):
                return True, launcher
    return False, None
