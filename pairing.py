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
import hmac
import json
import os
import secrets
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

REQUEST_TTL = 600.0        # a pending request stops being valid after 10 minutes
TOKEN_TTL = 600.0          # how long an approved request keeps handing the token to the poller
MAX_PENDING = 8
MAX_PENDING_PER_IP = 2     # one address cannot fill the whole queue
IP_RATE_LIMIT = 5          # requests per address per IP_RATE_WINDOW
IP_RATE_WINDOW = 600.0

_ip_hits: Dict[str, List[float]] = {}


def _loopback(ip: str) -> bool:
    try:
        import ipaddress
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def _ip_rate_ok(ip: str, now: float) -> bool:
    """Sliding window per address, in memory: a restart forgets it, which only loosens the limit."""
    for k in [k for k, v in _ip_hits.items() if not v or v[-1] <= now - IP_RATE_WINDOW]:
        del _ip_hits[k]
    hits = [t for t in _ip_hits.get(ip, []) if t > now - IP_RATE_WINDOW]
    if len(hits) >= IP_RATE_LIMIT:
        _ip_hits[ip] = hits
        return False
    hits.append(now)
    _ip_hits[ip] = hits
    return True
STATUS_RATE_LIMIT = 120      # status polls per address per STATUS_RATE_WINDOW
STATUS_RATE_WINDOW = 60.0
STATUS_MISS_LIMIT = 20       # unknown request ids per address per STATUS_MISS_WINDOW
STATUS_MISS_WINDOW = 600.0

_status_hits: Dict[str, List[float]] = {}
_status_misses: Dict[str, List[float]] = {}


def status_rate_ok(ip: str, now: Optional[float] = None) -> bool:
    """Sliding window per address for status polls; loopback is exempt."""
    if _loopback(ip):
        return True
    now = time.time() if now is None else now
    miss = [t for t in _status_misses.get(ip, []) if t > now - STATUS_MISS_WINDOW]
    if len(miss) >= STATUS_MISS_LIMIT:
        return False
    for k in [k for k, v in _status_hits.items() if not v or v[-1] <= now - STATUS_RATE_WINDOW]:
        del _status_hits[k]
    hits = [t for t in _status_hits.get(ip, []) if t > now - STATUS_RATE_WINDOW]
    if len(hits) >= STATUS_RATE_LIMIT:
        _status_hits[ip] = hits
        return False
    hits.append(now)
    _status_hits[ip] = hits
    return True


def note_status_miss(ip: str, now: Optional[float] = None) -> None:
    """Count a poll for a request id we never issued, so id guessing gets locked out."""
    if _loopback(ip):
        return
    now = time.time() if now is None else now
    hits = [t for t in _status_misses.get(ip, []) if t > now - STATUS_MISS_WINDOW]
    hits.append(now)
    _status_misses[ip] = hits
TOUCH_EVERY = 60.0         # write last_seen at most once a minute per device

# What a paired device may do (38c). The master HUD token is always `full`; these only limit
# the tokens pairing mints, so a phone that is lost can be revoked without touching the key.
SCOPE_FULL = "full"        # everything, including pairing management and global settings
SCOPE_DEVICE = "device"    # every route except the admin set below
SCOPE_READ = "read"        # GET only
SCOPE_PEER = "peer"        # a linked station: checked against its grants (peers.py)
SCOPES = (SCOPE_FULL, SCOPE_DEVICE, SCOPE_READ, SCOPE_PEER)

# Routes a `device` token may not use: managing pairing, rewriting global config, reloading code.
DEVICE_DENIED = (
    "/api/pair/approve",
    "/api/pair/deny",
    "/api/pair/revoke",
    "/api/pair/rename",
    "/api/pair/list",
    "/api/settings",
    "/api/reload",
    "/api/clear_history",
    "/api/models",
    "/api/peer/prepare",
    "/api/peer/complete",
    "/api/remote_workers/approve",
    "/api/remote_workers/reject",
    "/api/peers/grants",
    "/api/peers/controller",
    "/api/peer/link",
)


def _normalise_scope(scope: Any) -> str:
    """Unknown scopes narrow to the safest known one rather than granting anything."""
    value = str(scope or "").strip().lower()
    return value if value in SCOPES else SCOPE_DEVICE


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
                   base_dir: Optional[str] = None, notify: bool = True) -> Dict[str, Any]:
    """Register a pending request. Does not grant anything by itself."""
    now = time.time()
    reqs = _requests(base_dir)
    _drop_expired(reqs, now)
    pending = [r for r in reqs.values() if r.get("status") == "pending"]
    if len(pending) >= MAX_PENDING:
        return {"error": "Too many pending pairing requests. Approve or deny one on the Mac first."}
    if ip and not _loopback(ip):
        if sum(1 for r in pending if r.get("ip") == ip) >= MAX_PENDING_PER_IP:
            return {"error": "This address already has pairing requests waiting. Approve or deny them on the Mac first."}
        if not _ip_rate_ok(ip, now):
            return {"error": "Too many pairing requests from this address. Wait a few minutes and try again."}
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
    if notify:
        _notify(req)
    return {k: req[k] for k in ("request_id", "code", "device", "created_at", "expires_at")}


def mint_qr_code(ttl_s: float = 300, base_dir: Optional[str] = None) -> Dict[str, Any]:
    """One-time pairing code for a QR. Only its sha256 is stored."""
    now = time.time()
    codes = _load("qr_codes.json", base_dir).get("codes")
    codes = codes if isinstance(codes, dict) else {}
    codes = {h: e for h, e in codes.items() if isinstance(e, (int, float)) and e > now}
    code = secrets.token_urlsafe(6)[:8]
    codes[_hash(code)] = now + ttl_s
    _save("qr_codes.json", {"codes": codes}, base_dir)
    return {"code": code, "expires_at": now + ttl_s}


def consume_qr_code(code: str, base_dir: Optional[str] = None) -> bool:
    """True once for a valid unexpired code; it is removed on use."""
    if not code or not isinstance(code, str):
        return False
    now = time.time()
    codes = _load("qr_codes.json", base_dir).get("codes")
    codes = codes if isinstance(codes, dict) else {}
    want = _hash(code)
    found = False
    for h, exp in list(codes.items()):
        ok = hmac.compare_digest(h, want)
        if not isinstance(exp, (int, float)) or exp <= now:
            codes.pop(h, None)
        elif ok:
            found = True
            codes.pop(h, None)
    _save("qr_codes.json", {"codes": codes}, base_dir)
    return found


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
        "scope": _normalise_scope(scope),
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


def mint_device(name: str, platform: str = "", scope: str = SCOPE_DEVICE, base_dir: Optional[str] = None) -> Dict[str, Any]:
    """A device record and its token without a request (used to prepare a station link)."""
    token = secrets.token_urlsafe(24)
    device_id = "dev_" + secrets.token_hex(5)
    devices = _load("devices.json", base_dir).get("devices")
    devices = devices if isinstance(devices, list) else []
    devices.append({"device_id": device_id, "name": str(name or "Station")[:60], "platform": platform,
                    "scope": _normalise_scope(scope), "token_sha256": _hash(token), "created_at": time.time(),
                    "last_seen": time.time(), "revoked": False})
    _save("devices.json", {"devices": devices}, base_dir)
    return {"device_id": device_id, "token": token}


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


def find_device(token: str, base_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The device record for a token, revoked or not, compared in constant time."""
    if not token:
        return None
    want = _hash(token)
    devices = _load("devices.json", base_dir).get("devices")
    devices = devices if isinstance(devices, list) else []
    hit: Optional[Dict[str, Any]] = None
    for d in devices:
        if secrets.compare_digest(str(d.get("token_sha256", "")), want):
            hit = d  # keep scanning: never leak position by timing
    return hit


def is_device_token(token: str, base_dir: Optional[str] = None) -> bool:
    """Constant-time check of a paired device token; revoked devices stop working at once."""
    device = find_device(token, base_dir)
    return bool(device) and not device.get("revoked")


def scope_for_token(token: str, base_dir: Optional[str] = None) -> Optional[str]:
    """The scope a device token carries, or None when the token is unknown or revoked."""
    device = find_device(token, base_dir)
    if not device or device.get("revoked"):
        return None
    return _normalise_scope(device.get("scope"))


def scope_allows(scope: str, path: str, method: str = "GET") -> Tuple[bool, str]:
    """Whether a scope may use this route. Returns (allowed, reason)."""
    scope = _normalise_scope(scope)
    route = str(path or "").split("?")[0]
    if scope == SCOPE_FULL:
        return True, ""
    if scope == SCOPE_PEER:
        return False, "linked stations are checked against their grants"  # handlers.py asks peers.allows instead
    if scope == SCOPE_READ and method.upper() not in ("GET", "HEAD", "OPTIONS"):
        return False, "this device is paired read-only"
    if scope == SCOPE_DEVICE and route in DEVICE_DENIED:
        return False, "a paired device cannot change pairing, global settings or reload the station"
    return True, ""


def platform_from_ua(ua: str) -> str:
    s = str(ua or "").lower()
    if "iphone" in s or "cfnetwork" in s and "darwin" in s and "mac" not in s:
        return "ios"
    if "ipad" in s:
        return "ipados"
    if "android" in s:
        return "android"
    return ""


def touch(token: str, base_dir: Optional[str] = None, platform: str = "") -> None:
    """Record activity for the device list, at most once per TOUCH_EVERY seconds."""
    device = find_device(token, base_dir)
    if not device:
        return
    now = time.time()
    fill = bool(platform) and not device.get("platform")
    if not fill and now - float(device.get("last_seen") or 0) < TOUCH_EVERY:
        return
    devices = _load("devices.json", base_dir).get("devices")
    devices = devices if isinstance(devices, list) else []
    for d in devices:
        if d.get("device_id") == device.get("device_id"):
            d["last_seen"] = now
            if fill:
                d["platform"] = str(platform)[:40]
    _save("devices.json", {"devices": devices}, base_dir)


def rename(device_id: str, name: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Give a device a human name, so the list is not six `iPhone`s."""
    devices = _load("devices.json", base_dir).get("devices")
    devices = devices if isinstance(devices, list) else []
    clean = (name or "").strip()[:80]
    if not clean:
        return {"error": "name is required"}
    for d in devices:
        if d.get("device_id") == device_id:
            d["name"] = clean
            _save("devices.json", {"devices": devices}, base_dir)
            return {"ok": True, "device_id": device_id, "name": clean}
    return {"error": "Unknown device"}
