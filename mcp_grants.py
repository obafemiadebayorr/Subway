"""Capability token grants for Subway MCP sign-in.

Grants are issued in one process (spawner) and checked in another (HUD),
so they live on disk at <state_dir>/mcp_grants.json.

Security: never store raw cap tokens, only sha256(cap_token) hex keys.
"""

from __future__ import annotations

import win_lock
import hashlib
import json
import os
import secrets
import tempfile
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional


def _state_dir() -> str:
    return os.path.expanduser("~/.subway")


def _grants_path() -> str:
    return os.path.join(_state_dir(), "mcp_grants.json")


def _lock_path() -> str:
    return os.path.join(_state_dir(), "mcp_grants.lock")


@contextmanager
def _locked():
    state_dir = _state_dir()
    os.makedirs(state_dir, exist_ok=True)
    lock_path = _lock_path()
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        win_lock.lock(lock_file.fileno())
        try:
            yield
        finally:
            win_lock.unlock(lock_file.fileno())


def _load_unlocked() -> Dict[str, Dict[str, Any]]:
    path = _grants_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            # Ensure values are dicts; tolerate but drop malformed entries.
            return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}
        return {}
    except Exception:
        return {}


def _save_unlocked(grants: Dict[str, Dict[str, Any]]) -> None:
    state_dir = _state_dir()
    os.makedirs(state_dir, exist_ok=True)
    path = _grants_path()
    fd, tmp_path = tempfile.mkstemp(prefix="mcp_grants.", suffix=".tmp", dir=state_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(grants, f, separators=(",", ":"), sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass


def _hash_token(cap_token: str) -> str:
    return hashlib.sha256(cap_token.encode("utf-8")).hexdigest()


def _now(now: Any) -> float:
    return float(now() if callable(now) else now)


def _validate_issue_inputs(worker_id: str, server: str, ttl: Any) -> None:
    for name, value in (("worker_id", worker_id), ("server", server)):
        if not isinstance(value, str) or not value or len(value) > 200:
            raise ValueError(f"{name} must be a non-empty str of length <= 200")
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl < 1 or ttl > 86400:
        raise ValueError("ttl must be between 1 and 86400")


def _is_well_formed_token(cap_token: Any) -> bool:
    if not isinstance(cap_token, str) or not cap_token.startswith("cap_"):
        return False
    # "cap_" + token_urlsafe(32) => 32 bytes -> 43 base64url chars without padding
    return len(cap_token) == 4 + 43


def issue_grant(
    worker_id: str,
    server: str,
    project: str = "",
    ttl: float = 3600.0,
    now: Callable[[], float] | float = time.time,
) -> str:
    _validate_issue_inputs(worker_id, server, ttl)
    if project is None:
        project = ""
    if not isinstance(project, str):
        project = str(project)
    cap_token = "cap_" + secrets.token_urlsafe(32)
    key = _hash_token(cap_token)
    ts = _now(now)
    grant = {
        "worker_id": worker_id,
        "server": server,
        "project": project,
        "issued_at": ts,
        "expires_at": ts + float(ttl),
    }
    with _locked():
        grants = _load_unlocked()
        grants[key] = grant
        _save_unlocked(grants)
    return cap_token


def check_grant(
    cap_token: str,
    server: str,
    now: Callable[[], float] | float = time.time,
) -> Optional[Dict[str, Any]]:
    if not _is_well_formed_token(cap_token):
        return None
    key = _hash_token(cap_token)
    ts = _now(now)
    with _locked():
        grants = _load_unlocked()
        grant = grants.get(key)
        if grant is None:
            return None
        # Remove if expired regardless of server match.
        if float(grant.get("expires_at", 0)) <= ts:
            grants.pop(key, None)
            _save_unlocked(grants)
            return None
        if grant.get("server") != server:
            return None
        result = dict(grant)
    # Return a copy without token/hash (grant never stores them, but be explicit).
    result.pop("cap_token", None)
    result.pop("token_hash", None)
    return result


def revoke_grant(cap_token: Any) -> bool:
    if not _is_well_formed_token(cap_token):
        return False
    key = _hash_token(cap_token)
    with _locked():
        grants = _load_unlocked()
        if key not in grants:
            return False
        grants.pop(key, None)
        _save_unlocked(grants)
        return True


def revoke_grants_for(server: str) -> int:
    with _locked():
        grants = _load_unlocked()
        to_remove = [k for k, v in grants.items() if v.get("server") == server]
        for k in to_remove:
            grants.pop(k, None)
        if to_remove:
            _save_unlocked(grants)
        return len(to_remove)


def revoke_grants_for_worker(worker_id: str) -> int:
    with _locked():
        grants = _load_unlocked()
        to_remove = [k for k, v in grants.items() if v.get("worker_id") == worker_id]
        for k in to_remove:
            grants.pop(k, None)
        if to_remove:
            _save_unlocked(grants)
        return len(to_remove)


def prune_expired(now: Callable[[], float] | float = time.time) -> int:
    ts = _now(now)
    with _locked():
        grants = _load_unlocked()
        to_remove = [k for k, v in grants.items() if float(v.get("expires_at", 0)) <= ts]
        for k in to_remove:
            grants.pop(k, None)
        if to_remove:
            _save_unlocked(grants)
        return len(to_remove)


def active_grants() -> List[Dict[str, Any]]:
    ts = _now(time.time)
    with _locked():
        grants = _load_unlocked()
        # Prune expired opportunistically? active_grants should show active ones.
        changed = False
        active: List[Dict[str, Any]] = []
        for k, v in list(grants.items()):
            if float(v.get("expires_at", 0)) <= ts:
                grants.pop(k, None)
                changed = True
                continue
            active.append(
                {
                    "worker_id": v.get("worker_id"),
                    "server": v.get("server"),
                    "project": v.get("project", ""),
                    "issued_at": v.get("issued_at"),
                    "expires_at": v.get("expires_at"),
                }
            )
        if changed:
            _save_unlocked(grants)
        return active
