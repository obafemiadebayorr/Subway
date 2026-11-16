"""Thread-graph edges recorded where they happen: reviewed, briefed, handed_off, waited_on, merged.

Every function is best effort and never raises: recording an edge must not change the action it records.
"""
from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, Optional

import server as _s

_SAFE_THREAD = re.compile(r"^[A-Za-z0-9_.:@/-]{1,200}$")


def root_of(node_id: str) -> str:
    """The thread root of a worker id, a chat id for a chat, '' for nothing."""
    node_id = str(node_id or "")
    if not node_id:
        return ""
    try:
        return str(_s._thread_root_for_caller(node_id) or "")
    except Exception:
        return node_id


def thread_for(from_id: str, to_id: str) -> str:
    """The thread an edge belongs to: the root of the first worker end, else the from end."""
    for cand in (from_id, to_id):
        if cand and _s.WORKER_ID_RE.match(str(cand)):
            root = root_of(str(cand))
            if root:
                return root
    return str(from_id or to_id or "")


def edge(kind: str, from_id: str, to_id: str, data: Optional[Dict[str, Any]] = None, thread: str = "") -> str:
    """Append one edge record. Returns the thread id it was written to, '' when nothing was written."""
    try:
        if not from_id:
            return ""
        thread = thread or thread_for(from_id, to_id)
        if not thread:
            return ""
        _s._thread_record(thread, kind, str(from_id), peer=str(to_id or ""), data=data)
        return thread
    except Exception:
        return ""


def trailer(worker_id: str) -> str:
    """'\n\nSubway-Thread: <root>' for a git commit message, '' when the root is unknown or not message-safe."""
    root = root_of(worker_id) if worker_id else ""
    if not root or not _SAFE_THREAD.match(root):
        return ""
    return f"\n\nSubway-Thread: {root}"


def head_commit(repo: str) -> str:
    try:
        return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=30).stdout.strip()
    except Exception:
        return ""


def merge_commit_for(repo: str, worker_id: str) -> str:
    """The newest merge commit made for this worker (its message names the worker), else ''."""
    try:
        out = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%H", "--fixed-strings",
                              f"--grep=(worker {worker_id}:"], capture_output=True, text=True, timeout=30).stdout.strip()
        return out
    except Exception:
        return ""


def merged(worker_id: str, sha: str, role: str = "", mark: bool = True) -> None:
    """Record worker -> thread root as `merged` and, when mark, set merged + merge_commit on the worker record."""
    try:
        edge("merged", worker_id, root_of(worker_id), {"commit": sha, "role": role})
        if mark and sha:
            with _s._state_lock("workers"):
                meta = _s._load_worker(worker_id)
                if meta:
                    meta["merged"] = True
                    meta["merge_commit"] = sha
                    _s._save_worker(meta)
    except Exception:
        pass
