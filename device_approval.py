"""M6 T1 - short-lived device approval codes.

When a linked station first connects it exchanges a one-time 8-character code: the peer asks for
one, the principal reads it out loud and approves (or denies) it, and only then is the peer
allowed to use the station's other routes. Requests live in <SUBWAY_DIR>/device_approvals.json
and expire ten minutes after they were created. Wrong, expired and denied codes can never be
approved.
"""
from __future__ import annotations

import json
import os
import secrets
import string
import threading
import time
from typing import Any, Dict, List, Optional

import server as _s

FILE = "device_approvals.json"
EXPIRY_S = 600
_CODE_CHARS = string.ascii_uppercase + string.digits
_LOCK = threading.Lock()


def _path() -> str:
    # Read SUBWAY_DIR at call time so tests can repoint it without reimporting.
    return os.path.join(_s.SUBWAY_DIR, FILE)


def _now(now: Optional[float]) -> float:
    return time.time() if now is None else float(now)


def _load() -> Dict[str, Any]:
    p = _path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}
    return {}


def _save(data: Dict[str, Any]) -> None:
    _s._ensure_subway_dir()
    _s._write_json_atomic(_path(), data)


def _expired(rec: Dict[str, Any], now: float) -> bool:
    return (now - float(rec.get("ts", 0.0))) >= EXPIRY_S


def _key(code: Any) -> str:
    key = str(code or "").strip().upper()
    if len(key) != 8 or any(c not in _CODE_CHARS for c in key):
        raise ValueError("invalid approval code")
    return key


def _audit(action: str, code: str, peer: str = "") -> None:
    target: Dict[str, str] = {"code": str(code)[:16]}
    if peer:
        target["peer"] = str(peer)[:120]
    try:
        import audit
        audit.record({"kind": "mac", "id": "mac", "name": "This Mac"}, action, target)
    except Exception:
        pass  # auditing must never break the approval handshake


def request(peer_id: str, *, now: Optional[float] = None) -> str:
    """Create a pending approval request for ``peer_id`` and return its 8-character code."""
    ts = _now(now)
    pid = str(peer_id or "")
    code = "".join(secrets.choice(_CODE_CHARS) for _ in range(8))
    with _LOCK:
        data = _load()
        data[code] = {"code": code, "peer": pid, "ts": ts, "status": "pending"}
        _save(data)
    _audit("device.approval.request", code, pid)
    return code


def approve(code: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Flip a pending, unexpired, non-denied code to ``approved``.

    Raises KeyError for an unknown code and ValueError for a malformed, expired or denied one.
    """
    key, ts = _key(code), _now(now)
    with _LOCK:
        data = _load()
        rec = data.get(key)
        if rec is None:
            raise KeyError("unknown approval code")
        if _expired(rec, ts):
            raise ValueError("approval code expired")
        if rec.get("status") != "pending":
            raise ValueError("approval code is " + str(rec.get("status")))
        rec["status"] = "approved"
        _save(data)
        out = dict(rec)
    _audit("device.approval.approve", key, str(out.get("peer") or ""))
    return out


def deny(code: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Mark a code ``denied``; approve() refuses it until it expires."""
    key, ts = _key(code), _now(now)
    with _LOCK:
        data = _load()
        rec = data.get(key)
        if rec is None:
            raise KeyError("unknown approval code")
        if _expired(rec, ts):
            raise ValueError("approval code expired")
        rec["status"] = "denied"
        _save(data)
        out = dict(rec)
    _audit("device.approval.deny", key, str(out.get("peer") or ""))
    return out


def list_requests(*, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """All requests, newest first, with their expiry metadata."""
    ts = _now(now)
    with _LOCK:
        data = _load()
    out = []
    for rec in data.values():
        item = dict(rec)
        item["expires_at"] = float(rec.get("ts", 0.0)) + EXPIRY_S
        item["expired"] = _expired(rec, ts)
        out.append(item)
    return sorted(out, key=lambda r: float(r.get("ts", 0.0)), reverse=True)


def is_peer_approved(peer_id: str, *, now: Optional[float] = None) -> bool:
    """True when the peer holds at least one approved, unexpired code."""
    ts, pid = _now(now), str(peer_id or "")
    if not pid:
        return False
    with _LOCK:
        data = _load()
    return any(rec.get("peer") == pid and rec.get("status") == "approved" and not _expired(rec, ts)
               for rec in data.values())
