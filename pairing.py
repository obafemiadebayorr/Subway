"""Device pairing: how a phone gets a token without a human typing one.

The HUD token is a master key, and typing it on a phone is both tedious and a bad habit.
Pairing replaces it with a consent step on the machine that owns the work:

    phone  POST /api/pair/request   {device, platform}      -> pending, shows a 6-char code
    Mac    shows "Obafemi's iPhone wants to connect"        -> Approve / Deny
    phone  GET  /api/pair/status?request_id=...             -> approved + a device token
    phone  stores the token in the Keychain

Only an explicit approval mints a token, the token is scoped to that device, and it can be
revoked later without touching the master. Nothing here is reachable without passing the
HUD's host gate, so a hostile origin cannot even ask.

Storage is two JSON files under SUBWAY_DIR: pairing.json (requests) and devices.json
(minted tokens, stored as SHA-256, never in clear).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import time
from typing import Any, Dict, List, Optional

REQUEST_TTL = 600.0        # a pending request stops being valid after 10 minutes
TOKEN_TTL = 600.0          # how long an approved request keeps handing the token to the poller
MAX_PENDING = 8


def _base(base_dir: Optional[str] = None) -> str:
    if base_dir:
        return str(base_dir)
    import server as _s  # late import: server imports this module

    return str(_s.SUBWAY_DIR)


def _path(name: str, base_dir: Optional[str] = None) -> str:
    return os.path.join(_base(base_dir), name)


def _load(name: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
    try:
        with open(_path(name, base_dir), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(name: str, data: Dict[str, Any], base_dir: Optional[str] = None) -> None:
    path = _path(name, base_dir)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".pair-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1: read aloud, typed once
    return "".join(secrets.choice(alphabet) for _ in range(6))


def _requests(base_dir: Optional[str] = None) -> Dict[str, Any]:
    data = _load("pairing.json", base_dir)
    reqs = data.get("requests")
    return reqs if isinstance(reqs, dict) else {}


def _drop_expired(reqs: Dict[str, Any], now: float) -> None:
    for rid in [r for r, v in reqs.items() if now - float(v.get("created_at", 0)) > REQUEST_TTL]:
        reqs.pop(rid, None)


def create_request(device: str, platform: str = "", ip: str = "", note: str = "",
                   base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Register a pending request. Does not grant anything by itself."""
    now = time.time()
    reqs = _requests(base_dir)
    _drop_expired(reqs, now)
    pending = [r for r in reqs.values() if r.get("status") == "pending"]
    if len(pending) >= MAX_PENDING:
        return {"error": "Too many pending pairing requests. Approve or deny one on the Mac first."}
    rid = "pair_" + secrets.token_hex(6)
    req = {
        "request_id": rid,
        "device": (device or "Unknown device")[:80],
        "platform": (platform or "")[:40],
        "ip": (ip or "")[:64],
        "note": (note or "")[:200],
        "code": _code(),
        "status": "pending",
        "created_at": now,
        "expires_at": now + REQUEST_TTL,
    }
    reqs[rid] = req
    _save("pairing.json", {"requests": reqs}, base_dir)
    _notify(req)
    return {k: req[k] for k in ("request_id", "code", "device", "created_at", "expires_at")}


def dialog_script(req: Dict[str, Any], give_up_s: int = 600) -> str:
    """An AppleScript dialog that stays until answered.

    A notification banner is gone in five seconds; a pairing request that nobody saw is a
    request that silently expires. This blocks the screen (modally) for up to ``give_up_s``
    and returns "Approve" or "Deny" on stdout.
    """
    device = str(req.get("device", "A device")).replace('"', "'")
    platform = str(req.get("platform", "") or "unknown").replace('"', "'")
    code = str(req.get("code", ""))
    text = (f"Subway: a device wants to connect\\n\\n{device} ({platform})\\n"
            f"Code {code}\\n\\nOnly approve if this is your device.")
    return (
        'display dialog "' + text + '" '
        'buttons {"Deny", "Approve"} default button "Approve" cancel button "Deny" '
        'with title "Subway pairing" with icon caution '
        f'giving up after {int(give_up_s)}'
    )


def _notify(req: Dict[str, Any]) -> None:
    """Ask the person on the machine that owns the work. Never silently."""
    try:
        import server as _s

        _s.notify_desktop_event(
            key="pair:" + str(req["request_id"]),
            title="Subway: a device wants to connect",
            message=f"{req['device']} · code {req['code']} · approve in the HUD or run: subway pair",
            route="settings",
        )
    except Exception:
        pass
    _spawn_dialog(req)


def _spawn_dialog(req: Dict[str, Any]) -> None:
    """Fire the modal dialog in its own process so the HTTP request is not held open."""
    import shutil
    import subprocess
    import sys

    if sys.platform != "darwin" or not shutil.which("osascript"):
        return
    if os.environ.get("SUBWAY_PAIR_NO_DIALOG"):
        return  # used by tests and headless runs, where a modal dialog would block a human's screen
    try:
        subprocess.Popen(
            [sys.executable, "-m", "pairing_prompt", str(req["request_id"]), str(req["code"])],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def get_request(request_id: str, base_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return _requests(base_dir).get(str(request_id))


def status(request_id: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Where the phone polls. Hands the token over only after an explicit approval."""
    req = get_request(request_id, base_dir)
    if not req:
        return {"status": "unknown"}
    now = time.time()
    if req.get("status") == "pending" and now - float(req.get("created_at", 0)) > REQUEST_TTL:
        return {"status": "expired"}
    out: Dict[str, Any] = {"status": req.get("status", "unknown"), "device": req.get("device", "")}
    if req.get("status") == "approved":
        if now - float(req.get("approved_at", 0)) <= TOKEN_TTL:
            out["token"] = req.get("token")
            out["device_id"] = req.get("device_id")
        else:
            out["status"] = "expired"
    if req.get("status") == "denied":
        out["reason"] = req.get("reason", "")
    return out


def approve(request_id: str, scope: str = "device", base_dir: Optional[str] = None) -> Dict[str, Any]:
    reqs = _requests(base_dir)
    req = reqs.get(str(request_id))
    if not req:
        return {"error": "Unknown pairing request"}
    if req.get("status") != "pending":
        return {"error": f"Request is already {req.get('status')}"}
    token = secrets.token_urlsafe(24)
    device_id = "dev_" + secrets.token_hex(5)
    devices = _load("devices.json", base_dir).get("devices")
    devices = devices if isinstance(devices, list) else []
    devices.append({
        "device_id": device_id,
        "name": req.get("device", "Device"),
        "platform": req.get("platform", ""),
        "scope": scope,
        "token_sha256": _hash(token),
        "created_at": time.time(),
        "last_seen": time.time(),
        "revoked": False,
    })
    _save("devices.json", {"devices": devices}, base_dir)
    req.update({"status": "approved", "approved_at": time.time(), "token": token, "device_id": device_id})
    reqs[str(request_id)] = req
    _save("pairing.json", {"requests": reqs}, base_dir)
    return {"ok": True, "device_id": device_id, "token": token, "device": req.get("device", "")}


def deny(request_id: str, reason: str = "", base_dir: Optional[str] = None) -> Dict[str, Any]:
    reqs = _requests(base_dir)
    req = reqs.get(str(request_id))
    if not req:
        return {"error": "Unknown pairing request"}
    req.update({"status": "denied", "reason": (reason or "Declined on the Mac")[:200], "denied_at": time.time()})
    req.pop("token", None)
    reqs[str(request_id)] = req
    _save("pairing.json", {"requests": reqs}, base_dir)
    return {"ok": True, "device": req.get("device", "")}


def list_requests(include_done: bool = False, base_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    now = time.time()
    out = []
    for req in _requests(base_dir).values():
        if not include_done and req.get("status") != "pending":
            continue
        item = {k: v for k, v in req.items() if k != "token"}
        item["age_s"] = round(now - float(req.get("created_at", now)), 1)
        out.append(item)
    out.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return out


def list_devices(base_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    devices = _load("devices.json", base_dir).get("devices")
    devices = devices if isinstance(devices, list) else []
    return [{k: v for k, v in d.items() if k != "token_sha256"} for d in devices]


def revoke(device_id: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
    devices = _load("devices.json", base_dir).get("devices")
    devices = devices if isinstance(devices, list) else []
    hit = False
    for d in devices:
        if d.get("device_id") == device_id:
            d["revoked"] = True
            d["revoked_at"] = time.time()
            hit = True
    if not hit:
        return {"error": "Unknown device"}
    _save("devices.json", {"devices": devices}, base_dir)
    return {"ok": True, "device_id": device_id}


def is_device_token(token: str, base_dir: Optional[str] = None) -> bool:
    """Constant-time check of a paired device token; revoked devices stop working at once."""
    if not token:
        return False
    want = _hash(token)
    devices = _load("devices.json", base_dir).get("devices")
    devices = devices if isinstance(devices, list) else []
    found = False
    for d in devices:
        if d.get("revoked"):
            continue
        if secrets.compare_digest(str(d.get("token_sha256", "")), want):
            found = True
    return found
