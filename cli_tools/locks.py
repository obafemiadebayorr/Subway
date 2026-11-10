from __future__ import annotations

import datetime as _dt
import hashlib
import os
import subprocess
import time
from typing import Any, Dict

import server as _s
def _canon_path(filepath: str, project_dir: str = "") -> str:
    """One key per real file, so relative, absolute and symlinked paths can't dodge
    each other, and two repos with a README.md don't collide."""
    p = os.path.expanduser(filepath.strip())
    if not os.path.isabs(p):
        p = os.path.join(project_dir or os.getcwd(), p)
    return os.path.realpath(p)


def _file_sha(path: str) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _git_head(path: str) -> str:
    """HEAD of the repo that owns this file, not of wherever the server happens to run."""
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    try:
        return subprocess.check_output(["git", "-C", folder, "rev-parse", "--short", "HEAD"],
                                       stderr=subprocess.DEVNULL, timeout=2).decode("utf-8").strip()
    except Exception:
        return ""


def _clean_locks(locks_data: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    active = {}
    signoffs = locks_data.setdefault("signoffs", {})
    locks_data.setdefault("authors", {})
    for path, info in locks_data.get("locks", {}).items():
        if info.get("status") == "APPROVED" and info.get("signoff"):  # Older state format.
            signoffs.setdefault(path, info["signoff"])
        if info.get("expires_at", 0) > now:
            active[path] = info
    locks_data["locks"] = active
    return locks_data


def load_locks() -> Dict[str, Any]:
    _s._ensure_subway_dir()
    data = _s._load_json("locks.json")
    if "locks" not in data:
        data["locks"] = {}
    return _s._clean_locks(data)


def save_locks(data: Dict[str, Any]) -> None:
    _s._save_json("locks.json", data)


def t_claim_file(filepath: str, holder: str = "", duration_seconds: int = 300, purpose: str = "",
                 baseline_commit: str = "", role: str = "implementer", project_dir: str = "") -> str:
    """Acquire a temporary workspace lease on a file to prevent multi-agent collision and regressions."""
    explicit_holder = bool(holder)
    if not filepath:
        return "Error: filepath is required."
    path = _s._canon_path(filepath, project_dir)

    det = _s._detected_chat()
    is_unverified = False
    if det is not None:
        det_id = det["id"]
        if holder:
            if not _s._id_matches(holder, det_id):
                return f"Error: caller is '{det_id}', cannot claim file as '{holder}'."
            else:
                me_id = det_id
        else:
            holder = _s._short_id(det_id)
            me_id = det_id
    else:
        me_id = _s._self_id()
        holder = holder or _s._short_id(me_id)
        is_unverified = True

    role = (role or "implementer").lower()

    try:
        duration = max(10, min(int(duration_seconds), 3600))
    except (TypeError, ValueError):
        duration = 300

    head = _s._git_head(path)
    override = ""
    worker = _s._load_worker(me_id)
    parent = worker.get("launched_by") if worker else None
    with _s._state_lock("locks"):
        now = time.time()
        data = _s.load_locks()
        locks = data["locks"]
        existing = locks.get(path)

        # 1. Collision Check against active leases
        if existing and existing.get("holder") != holder:
            rem = int(existing["expires_at"] - now)
            purp_str = f" for '{existing.get('purpose')}'" if existing.get("purpose") else ""
            return (f"[COLLISION WARNING] '{path}' is already leased to {existing.get('holder')}{purp_str}. "
                    f"Lease expires in {rem}s. Do NOT overwrite or clobber until released or expired.")

        symbol_conflicts = []
        prefix = f"{path}::"
        for k, v in locks.items():
            if k.startswith(prefix):
                sym_holder = v.get("holder")
                if sym_holder != holder and not ((not explicit_holder or sym_holder == holder) and v.get("holder_id") and _s._id_matches(v.get("holder_id"), me_id)):
                    sym_name = v.get("symbol") or k[len(prefix):]
                    symbol_conflicts.append(f"'{sym_name}' (held by {sym_holder})")
        if symbol_conflicts:
            return (f"[COLLISION WARNING] '{path}' cannot be claimed: active symbol leases exist: "
                    + ", ".join(symbol_conflicts) + ".")

        # 2. Regression Shield: an approved file needs an acknowledged baseline to change.
        signoff = data["signoffs"].get(path)
        if signoff and signoff.get("verdict") == "APPROVED":
            changed = bool(signoff.get("content_sha")) and _s._file_sha(path) != signoff["content_sha"]
            if role in ("coordinator", "refactorer"):
                override = (f"[SHIELD OVERRIDE] {holder} claimed approved file '{path}' as {role}. "
                            f"Approved by {signoff.get('reviewer')}.")
            elif baseline_commit != signoff.get("commit_sha"):
                drift = " The file has changed since that approval." if changed else ""
                return (f"[REGRESSION SHIELD] '{path}' was previously verified and APPROVED by "
                        f"{signoff.get('reviewer')}.{drift} Blind re-editing is prevented. Read the sign-off with "
                        f"list_locks and review that version, then claim with baseline_commit set to the approved "
                        f"commit, or get your user's authorization for the coordinator or refactorer role.")

        expires_at = now + duration
        baseline = baseline_commit or head
        locks[path] = {
            "holder": holder,
            "holder_id": me_id,
            "acquired_at": now,
            "expires_at": expires_at,
            "duration": duration,
            "purpose": purpose or "Active modification",
            "baseline_commit": baseline,
            "role": role,
            "status": "LEASED",
        }
        if parent:
            locks[path]["parent"] = parent
        if is_unverified:
            locks[path]["unverified"] = True
        data["authors"][path] = {"holder": holder, "holder_id": me_id}
        if parent:
            data["authors"][path]["parent"] = parent
        if is_unverified:
            data["authors"][path]["unverified"] = True
        _s.save_locks(data)

    if override:
        _s._broadcast_track_internal(override, track="main", files=[path], station_id=holder,
                                  parent=parent, is_unverified=is_unverified)
    exp_iso = _dt.datetime.fromtimestamp(expires_at).astimezone().strftime("%H:%M:%S")
    base_note = f" (baseline: {baseline})" if baseline else ""
    unv_note = " [unverified]" if is_unverified else ""
    return (f"[LEASE ACQUIRED] '{path}' claimed by {holder}{unv_note} (role: {role}){base_note} until {exp_iso} ({duration}s). "
            f"Purpose: {purpose or 'Active modification'}.")


def t_signoff_file(filepath: str, verdict: str = "approved", notes: str = "", reviewer: str = "",
                   project_dir: str = "") -> str:
    """Sign off on a completed file modification to lock in quality and prevent regressions."""
    if not filepath:
        return "Error: filepath is required."
    path = _s._canon_path(filepath, project_dir)

    det = _s._detected_chat()
    is_unverified = False
    if det is not None:
        det_id = det["id"]
        if reviewer:
            if not _s._id_matches(reviewer, det_id):
                return f"Error: caller is '{det_id}', cannot sign off as '{reviewer}'."
            else:
                me_id = det_id
        else:
            reviewer = _s._short_id(det_id)
            me_id = det_id
    else:
        me_id = _s._self_id()
        reviewer = reviewer or _s._short_id(me_id)
        is_unverified = True

    is_approved = verdict.strip().lower() in ("approved", "accept", "pass", "good")
    status_str = "APPROVED" if is_approved else "NEEDS_WORK"
    commit_sha = _s._git_head(path) or "uncommitted"
    worker = _s._load_worker(me_id)
    parent = worker.get("launched_by") if worker else None

    with _s._state_lock("locks"):
        data = _s.load_locks()
        author = data["authors"].get(path) or data["locks"].get(path) or {}
        if is_approved and (_s._id_matches(me_id, author.get("holder_id")) or _s._id_matches(reviewer, author.get("holder"))):
            return (f"[SIGNOFF REFUSED] '{path}' was last claimed by {author.get('holder')}. "
                    f"Approval has to come from a different agent than the one that changed the file.")
        data["signoffs"][path] = {
            "reviewer": reviewer,
            "verdict": status_str,
            "notes": notes or ("Quality verified, no regressions" if is_approved else "Rework required"),
            "commit_sha": commit_sha,
            "content_sha": _s._file_sha(path),
            "timestamp": time.time(),
            "time_iso": _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if parent:
            data["signoffs"][path]["parent"] = parent
        if is_unverified:
            data["signoffs"][path]["unverified"] = True
        _s.save_locks(data)

    final_notes = data["signoffs"][path]["notes"]
    _s._broadcast_track_internal(f"[SIGNOFF: {status_str}] '{path}' evaluated by {reviewer} at commit {commit_sha}. Notes: {final_notes}",
                              track="main", files=[path], decisions=[f"Sign-off {status_str} by {reviewer}"],
                              station_id=reviewer, parent=parent, is_unverified=is_unverified)
    unv_note = " [unverified]" if is_unverified else ""
    if is_approved:
        return f"[SIGNOFF: APPROVED] '{path}' marked approved by {reviewer}{unv_note} (commit: {commit_sha}). Regression Shield active."
    return f"[SIGNOFF: NEEDS_WORK] '{path}' flagged for rework by {reviewer}{unv_note}. Notes: {final_notes}"


def t_release_file(filepath: str, holder: str = "", project_dir: str = "", force: bool = False,
                   symbol: str = "") -> str:
    """Release a workspace file lease."""
    if not filepath:
        return "Error: filepath is required."
    path = _s._canon_path(filepath, project_dir)
    lock_key = f"{path}::{symbol}" if symbol else path
    target_desc = f"Symbol '{symbol}' in '{path}'" if symbol else f"File '{path}'"
    with _s._state_lock("locks"):
        data = _s.load_locks()
        current = data["locks"].get(lock_key)
        if not current:
            return f"{target_desc} was not currently leased."

        lock_holder = current.get("holder", "")
        lock_holder_id = current.get("holder_id", "")

        det = _s._detected_chat()
        if det is not None:
            det_id = det["id"]
            owns_lock = _s._id_matches(det_id, lock_holder_id) or _s._id_matches(det_id, lock_holder)
            worker_id = lock_holder_id if _s.WORKER_ID_RE.match(lock_holder_id or "") else lock_holder
            worker = _s._load_worker(worker_id) if _s.WORKER_ID_RE.match(worker_id or "") else None
            if worker:
                worker = _s._update_worker_status(worker)
            worker_ended = bool(worker and worker.get("status") and
                                worker.get("status") not in _s.ACTIVE_WORKER_STATES)
            launcher_cleanup = bool(worker_ended and _s._id_matches(worker.get("launched_by"), det_id))

            if holder and not _s._id_matches(holder, det_id):
                requested_worker = bool(launcher_cleanup and _s._id_matches(holder, worker_id))
                if not requested_worker:
                    return f"Error: caller is '{det_id}', cannot release as '{holder}'."
            if not owns_lock and launcher_cleanup:
                owns_lock = True

            if not owns_lock:
                return f"Error: {target_desc.lower()} is held by '{lock_holder}', caller '{det_id}' cannot release another chat's lock."
            released_for = worker_id if launcher_cleanup and not _s._id_matches(det_id, worker_id) else ""
        else:
            me_id = _s._self_id()
            caller_holder = holder or _s._short_id(me_id)
            same_named = current.get("holder") == caller_holder and _s._id_matches(me_id, current.get("holder_id") or "")
            if current.get("holder_id") and not same_named and not _s._id_matches(caller_holder, current.get("holder_id")):
                return f"Error: {target_desc.lower()} is held by '{lock_holder}'. Lease not released."
            if not force and current.get("holder") != caller_holder and current.get("holder_id") != me_id:
                return f"Warning: {target_desc.lower()} is held by {current.get('holder')}, not {caller_holder}. Lease not released."

        del data["locks"][lock_key]
        _s.save_locks(data)
    release_note = f" released by {det_id} for ended worker {released_for}" if det is not None and released_for else ""
    disp = f"Symbol '{symbol}' in '{path}'" if symbol else f"'{path}'"
    return f"[RELEASED] {disp} is now free for modification by any agent.{release_note}"


def t_list_locks() -> str:
    """List active leases and sign-offs across all agents."""
    data = _s.load_locks()
    locks, signoffs = data["locks"], data["signoffs"]
    if not locks and not signoffs:
        return "No active file leases. All files are currently free for modification."
    now = time.time()
    lines = ["# Active Workspace File Leases (Workspace Guard)", ""]
    for path, info in sorted(locks.items(), key=lambda x: x[1].get("expires_at", 0)):
        rem = max(0, int(info.get("expires_at", 0) - now))
        unv = " [unverified]" if info.get("unverified") else ""
        lines.append(f"- `[LOCKED] {path}` — held by **{info.get('holder')}**{unv} "
                     f"(role: {info.get('role')}, {rem}s remaining) :: {info.get('purpose')}")
    for path, s in sorted(signoffs.items()):
        drift = " :: file changed since sign-off" if s.get("content_sha") and _s._file_sha(path) != s["content_sha"] else ""
        unv = " [unverified]" if s.get("unverified") else ""
        lines.append(f"- `[{s.get('verdict')}] {path}` — by **{s.get('reviewer')}**{unv} (commit: {s.get('commit_sha')}){drift}")
    return "\n".join(lines)
    return "\n".join(lines)
