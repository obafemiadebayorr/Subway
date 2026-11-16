"""Landing queue: hold finished worker branches until the repo is quiet, then land them FIFO.

State lives in <state dir>/land_queue.json, written through the same dir helper that
server._load_json uses, and guarded by an flock on <state dir>/.land_queue.lock so two
Subway processes never interleave a read-modify-write. ``server`` is imported lazily inside
functions to avoid import cycles (workers.spawn imports this module lazily too).
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
from typing import Any, Dict, List, Optional

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

_STATE = "land_queue.json"


@contextlib.contextmanager
def _flock():
    import server as _s
    os.makedirs(_s.SUBWAY_DIR, exist_ok=True)
    fd = os.open(os.path.join(_s.SUBWAY_DIR, ".land_queue.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _norm(repo: str) -> str:
    try:
        return os.path.realpath(str(repo))
    except Exception:
        return str(repo)


def _entries(state: Any) -> List[Dict[str, Any]]:
    if not isinstance(state, dict):
        return []
    entries = state.get("entries")
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def _read() -> List[Dict[str, Any]]:
    import server as _s
    return [dict(e) for e in _entries(_s._load_json(_STATE))]


def _write(entries: List[Dict[str, Any]]) -> None:
    import server as _s
    _s._save_json(_STATE, {"entries": entries})


def enqueue(repo: str, branch: str, base: str, role: str, worker_id: str) -> bool:
    """Add a finished branch to the queue. Returns False (and changes nothing) if already queued."""
    with _flock():
        entries = _read()
        for e in entries:
            if e.get("branch") == branch and _norm(e.get("repo", "")) == _norm(repo):
                return False
        entries.append({"repo": repo, "branch": branch, "base": base, "role": role, "worker_id": worker_id})
        _write(entries)
        return True


def list_queue(repo: Optional[str] = None) -> List[Dict[str, Any]]:
    """All queued entries, oldest first. Pass repo to list only that repo (paths compared resolved)."""
    with _flock():
        entries = _read()
    if repo is None:
        return entries
    want = _norm(repo)
    return [e for e in entries if _norm(e.get("repo", "")) == want]


def remove(branch: str) -> int:
    """Drop every queue entry for branch. Returns the number of entries removed."""
    with _flock():
        entries = _read()
        kept = [e for e in entries if e.get("branch") != branch]
        removed = len(entries) - len(kept)
        if removed:
            _write(kept)
        return removed


def drain(repo: str) -> Dict[str, Any]:
    """Land queued branches for repo in FIFO order. Stops at the first failure, keeping that entry with "error" set."""
    import server as _s
    from workers.spawn import _merge_worker_branch

    landed: List[str] = []
    with _flock():
        entries = _read()
        want = _norm(repo)
        mine = [e for e in entries if _norm(e.get("repo", "")) == want]
        rest = [e for e in entries if _norm(e.get("repo", "")) != want]
        blocked: Optional[Dict[str, Any]] = None
        remaining: List[Dict[str, Any]] = []
        for e in mine:
            if blocked is not None:
                remaining.append(e)
                continue
            branch = e.get("branch", "")
            try:
                ok, note = _merge_worker_branch(repo, branch, e.get("base", ""), e.get("role") or "worker", e.get("worker_id") or "")
            except Exception as exc:  # a failed land keeps its entry; never wedges the queue
                ok, note = False, _s.one_line(str(exc), 160)
            if ok:
                landed.append(branch)
                continue
            e = dict(e)
            e["error"] = note
            blocked = e
            remaining.append(e)
        _write(rest + remaining)
    if blocked is not None:
        prefix = f"Landed {len(landed)} branch(es): {', '.join(landed)}. " if landed else ""
        note = f"{prefix}Blocked on '{blocked.get('branch')}': {blocked.get('error')}"
    elif landed:
        note = f"Landed {len(landed)} branch(es): {', '.join(landed)}."
    else:
        note = "No queued branches to land."
    return {"landed": landed, "blocked": blocked, "note": note}


def drain_if_quiet(repo: str, exclude_worker: Optional[str] = None) -> Dict[str, Any]:
    """Drain only when no other worker on repo is running or awaiting approval.

    exclude_worker ignores the caller's own meta (a worker finishing mid-collect would otherwise block itself).
    """
    import server as _s

    busy = 0
    want = _norm(repo)
    try:
        for path in glob.glob(os.path.join(_s.SUBWAY_DIR, "workers", "worker_*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                continue
            if not isinstance(meta, dict):
                continue
            if exclude_worker and str(meta.get("id") or "") == str(exclude_worker):
                continue
            project = meta.get("project_dir")
            if not project or _norm(str(project)) != want:
                continue
            if str(meta.get("status") or "") in ("running", "pending_approval"):
                busy += 1
    except Exception:
        busy = 0
    if busy:
        return {"landed": [], "blocked": None, "note": f"{busy} branch(es) waiting for running workers to finish"}
    return drain(repo)
