"""Sealed session secret store (handles only cross the wire)."""

from __future__ import annotations

from typing import Any, Dict, List

from hud_server import collab_api


class SecretAccessError(Exception):
    pass


def _secrets_map(session: Dict[str, Any]) -> Dict[str, Any]:
    payload = collab_api.open_session_payload(session)
    if not isinstance(payload, dict):
        payload = {}
    secrets = payload.get("handles")
    if not isinstance(secrets, dict):
        secrets = {}
        payload["handles"] = secrets
    return payload


def _save(session: Dict[str, Any], payload: Dict[str, Any]) -> None:
    collab_api.seal_session_payload(session, payload)
    collab_api._save(session)


def set_secret(session_id: str, name: str, value: str, actor_device_id: str, share: bool = False) -> Dict[str, Any]:
    session = collab_api._load()
    if not session or session.get("id") != session_id:
        raise SecretAccessError("session not found")
    # Mode A: secrets API returns 403 unless owner passes explicit share: true per secret.
    # Owner is local initiator / master host device.
    owner = session.get("local_initiator") or session.get("host_device") or ""
    me = actor_device_id or collab_api._device_id()
    mode_a = str(session.get("billing_mode") or ("B" if session.get("mode_b") else "A")).strip().upper() != "B"
    if mode_a and name and not share:
        raise SecretAccessError("Mode A secret sharing requires explicit share: true")
    if mode_a and me != owner:
        raise SecretAccessError("Mode A secrets are owner-only")
    payload = _secrets_map(session)
    handles = payload["handles"]
    handles[name] = {"value": value, "shared": bool(share) or not mode_a}
    _save(session, payload)
    return {"ok": True, "name": name}


def get_secret_value(session_id: str, name: str) -> str:
    session = collab_api._load()
    if not session or session.get("id") != session_id:
        raise SecretAccessError("session not found")
    payload = collab_api.open_session_payload(session)
    val = ((payload.get("handles") or {}).get(name) or {}).get("value")
    if val is None:
        raise SecretAccessError(f"unknown secret: {name}")
    return str(val)


def list_handles(session_id: str) -> List[str]:
    session = collab_api._load()
    if not session or session.get("id") != session_id:
        raise SecretAccessError("session not found")
    payload = collab_api.open_session_payload(session)
    handles = payload.get("handles") or {}
    return sorted(handles.keys())
