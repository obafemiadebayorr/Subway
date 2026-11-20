"""k3: Outbound payload tripwire & secret-leak guard for collaboration sessions."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple


class OutboundLeakBlocked(ValueError):
    """Raised when an outbound session payload matches a secret or tripwire."""


def _collab_module():
    try:
        from hud_server import collab_api
        return collab_api
    except Exception:  # pragma: no cover - package import fallback
        import collab_api  # type: ignore
        return collab_api


def _audit_path() -> str:
    env_path = os.environ.get("SUBWAY_COLLAB_AUDIT_FILE", "").strip()
    if env_path:
        return os.path.expanduser(env_path)
    collab_api = _collab_module()
    return os.path.join(os.path.dirname(collab_api._collab_file()), "collab_audit.jsonl")


def log_audit(
    event: str,
    session_id: str = "",
    channel: str = "",
    match_label: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Append an audit entry without ever recording secret or tripwire values."""
    path = _audit_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rec: Dict[str, Any] = {
        "at": time.time(),
        "event": str(event or ""),
        "session_id": str(session_id or ""),
        "channel": str(channel or ""),
        "match_label": str(match_label or ""),
    }
    if extra:
        rec["extra"] = dict(extra)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def register_tripwire(session_id: str, value: str, label: str = "") -> Dict[str, Any]:
    """Plant a tripwire value inside the session's sealed payload."""
    val = str(value or "")
    if not val:
        raise ValueError("tripwire value must be non-empty")
    collab_api = _collab_module()
    with collab_api._LOCK:
        session = collab_api._load()
        if not session or session.get("id") != session_id:
            raise ValueError("session not found")
        payload = collab_api.open_session_payload(session)
        if not isinstance(payload, dict):
            payload = {}
        tripwires = payload.get("tripwires")
        if not isinstance(tripwires, list):
            tripwires = []
            payload["tripwires"] = tripwires
        lbl = str(label or f"tripwire:{len(tripwires) + 1}")
        tripwires.append({"label": lbl, "value": val})
        collab_api.seal_session_payload(session, payload)
        collab_api._save(session)
    return {"ok": True, "label": lbl}


def collect_forbidden(session: Optional[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """Return (label, value) pairs for session secrets and registered tripwires."""
    if not isinstance(session, dict):
        return []
    collab_api = _collab_module()
    try:
        payload = collab_api.open_session_payload(session)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return []
    out: List[Tuple[str, str]] = []
    handles = payload.get("handles") or {}
    if isinstance(handles, dict):
        for name, entry in handles.items():
            if isinstance(entry, dict) and entry.get("value"):
                out.append((f"secret:{name}", str(entry["value"])))
    tripwires = payload.get("tripwires") or []
    if isinstance(tripwires, list):
        for i, entry in enumerate(tripwires):
            if isinstance(entry, dict) and entry.get("value"):
                out.append((str(entry.get("label") or f"tripwire:{i}"), str(entry["value"])))
            elif isinstance(entry, str) and entry:
                out.append((f"tripwire:{i}", entry))
    return out


def _iter_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k == "secrets_blob":
                continue
            if isinstance(k, str):
                yield k
            yield from _iter_strings(v)
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            yield from _iter_strings(item)


def scan_payload(payload: Any, forbidden: Iterable[Tuple[str, str]]) -> List[str]:
    """Return labels of any forbidden values present in payload (never returns values)."""
    pairs = [(lbl, val) for lbl, val in forbidden if val]
    if not pairs:
        return []
    hits: List[str] = []
    seen = set()
    for text in _iter_strings(payload):
        for lbl, val in pairs:
            if val in text and lbl not in seen:
                seen.add(lbl)
                hits.append(lbl)
    return hits


def check_outbound(
    session: Optional[Dict[str, Any]],
    payload: Any,
    *,
    channel: str = "collab",
    extra_forbidden: Optional[Iterable[Tuple[str, str]]] = None,
    on_match_end: bool = True,
) -> None:
    """Block outbound payload if it leaks a session secret or tripwire, and revoke on match."""
    collab_api = _collab_module()
    active = session if isinstance(session, dict) else collab_api._load()
    forbidden = collect_forbidden(active)
    if extra_forbidden:
        forbidden.extend((str(l), str(v)) for l, v in extra_forbidden if v)
    hits = scan_payload(payload, forbidden)
    if not hits:
        return
    sid = str((active or {}).get("id") or "")
    log_audit("outbound_leak_blocked", session_id=sid, channel=channel, match_label=hits[0],
              extra={"matches": hits})
    if on_match_end and isinstance(active, dict):
        collab_api.revoke_and_end(active, reason=f"outbound_guard:{hits[0]}")
    raise OutboundLeakBlocked(f"outbound payload blocked on {channel}: matched {hits[0]}")
