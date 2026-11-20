"""Session-scoped quota accounting against per-sender lending caps (j2).

Usage is stored in the session doc's `cap_usage` map (written atomically via
collab_api's 0600 tmp+replace helper). Caps themselves live in `caps` (j1).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

try:
    from hud_server import collab_api
except Exception:  # pragma: no cover
    import collab_api  # type: ignore


def _load_session(session_id: str) -> Optional[Dict[str, Any]]:
    session = collab_api._load()
    if session is None or str(session.get("id")) != str(session_id):
        return None
    return session


def record_usage(session_id: str, sender_id: str, tokens: float = 0,
                 provider: str = "", seconds: float = 0, allowance_pct: float = 0) -> Dict[str, Any]:
    with collab_api._LOCK:
        session = _load_session(session_id)
        if session is None:
            return {"ok": False, "error": "session not found"}
        usage = session.setdefault("cap_usage", {})
        entry = usage.setdefault(str(sender_id), {"tokens": 0.0, "seconds": 0.0, "allowance_pct": 0.0, "calls": 0})
        entry["tokens"] = float(entry.get("tokens") or 0) + float(tokens or 0)
        entry["seconds"] = float(entry.get("seconds") or 0) + float(seconds or 0)
        entry["allowance_pct"] = float(entry.get("allowance_pct") or 0) + float(allowance_pct or 0)
        entry["calls"] = int(entry.get("calls") or 0) + 1
        if provider:
            entry["last_provider"] = str(provider)[:64]
        entry["updated_at"] = time.time()
        session["revision"] = int(session.get("revision") or 1) + 1
        collab_api._save(session)
        return {"ok": True, "used": dict(entry)}


def remaining(session_id: str, sender_id: str) -> Dict[str, Any]:
    session = _load_session(session_id)
    if session is None:
        return {"ok": False, "error": "session not found"}
    cap = (session.get("caps") or {}).get(str(sender_id)) or {}
    used = (session.get("cap_usage") or {}).get(str(sender_id)) or {}
    out: Dict[str, Any] = {"ok": True, "cap": cap, "used": used, "remaining": {}, "stop": False}
    for k in ("tokens", "seconds", "allowance_pct"):
        if k in cap:
            rem = max(0.0, float(cap[k]) - float(used.get(k) or 0))
            out["remaining"][k] = rem
            if rem <= 0.0:
                out["stop"] = True
    return out


def check_or_stop(session_id: str, sender_id: str) -> bool:
    """True if the sender may proceed; False when at/over any cap."""
    rem = remaining(session_id, sender_id)
    if not rem.get("ok"):
        return False
    cap = rem.get("cap") or {}
    if not cap:
        return True  # no cap set: allowed
    return not rem.get("stop")
