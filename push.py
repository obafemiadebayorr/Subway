"""Remote pushes to paired phones through Expo's push service (layer 3).

Only two things push: a worker waiting on a permission, and a worker that finished, failed or was stopped (the
same rule as mobile/src/notifications/rules.ts). Bodies name the tool and the worker, never the command, prompt
or file contents, because the text passes through Expo and Apple.
"""
import json
import os
import threading
import time
import urllib.request
from typing import Any, Dict, List

import subway_home

EXPO_URL = "https://exp.host/--/api/v2/push/send"
FINAL = {"completed", "failed", "stopped", "budget_stopped"}
_lock = threading.Lock()
_seen: Dict[str, float] = {}


def _path() -> str:
    return os.path.join(subway_home.subway_dir(), "push_tokens.json")


def _load() -> Dict[str, Dict[str, Any]]:
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: Dict[str, Dict[str, Any]]) -> None:
    p = _path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def register(device_id: str, token: str, platform: str = "") -> bool:
    if not device_id or not isinstance(token, str) or not token.startswith(("ExponentPushToken[", "ExpoPushToken[")):
        return False
    with _lock:
        d = _load()
        d[device_id] = {"token": token[:200], "platform": str(platform)[:20], "ts": time.time()}
        _save(d)
    return True


def unregister(device_id: str) -> None:
    with _lock:
        d = _load()
        if d.pop(device_id, None) is not None:
            _save(d)


def message_for(event_type: str, data: Dict[str, Any]):
    """(dedupe_key, message) or None."""
    if event_type == "permission":
        rid = str(data.get("id") or data.get("request_id") or "")
        if not rid:
            return None
        who = data.get("worker_id") or "A worker"
        tool = str(data.get("tool") or "a tool")[:60]
        return rid, {"title": f"{who} needs permission", "body": f"Wants to use {tool}", "categoryId": "permission",
                     "data": {"kind": "permission", "requestId": rid, "workerId": data.get("worker_id") or ""}}
    if event_type == "worker":
        status = str(data.get("status") or "")
        wid = str(data.get("id") or data.get("worker_id") or "")
        if status == "pending_approval" and wid:
            return f"{wid}:pending", {"title": f"{str(data.get('role') or wid)[:60]} needs your approval",
                                      "body": "Approve or reject it.", "categoryId": "approval",
                                      "data": {"kind": "approval", "workerId": wid}}
        if status not in FINAL or not wid:
            return None
        verb = "finished" if status == "completed" else status.replace("_", " ")
        return f"{wid}:{status}", {"title": f"{str(data.get('role') or wid)[:60]} {verb}", "body": "Tap to see the report.",
                                    "categoryId": "worker", "data": {"kind": "worker", "workerId": wid}}
    if event_type == "worker_media":
        wid = str(data.get("id") or "")
        if not wid:
            return None
        base = wid.split("@", 1)[0]
        n = len(data.get("names") or [])
        bucket = int(time.time() // 120)
        return f"{base}:media:{bucket}", {"title": f"{wid} took {'a screenshot' if n == 1 else f'{n} screenshots'}",
                                         "body": "Tap to see it.", "categoryId": "worker",
                                         "data": {"kind": "worker", "workerId": base}}
    return None


def _live_tokens() -> List[str]:
    """Tokens of devices that are still paired; revoked devices are dropped here."""
    try:
        import pairing
        paired = {d.get("device_id") for d in pairing.list_devices()}
    except Exception:
        paired = None
    with _lock:
        d = _load()
        if paired is not None:
            stale = [k for k in d if k not in paired]
            for k in stale:
                d.pop(k)
            if stale:
                _save(d)
        return [v["token"] for v in d.values() if v.get("token")]


def _send(messages: List[Dict[str, Any]]) -> None:
    body = json.dumps(messages).encode("utf-8")
    req = urllib.request.Request(EXPO_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read().decode("utf-8") or "{}")
    except Exception:
        return
    gone = {m["to"] for m, t in zip(messages, res.get("data") or [])
            if isinstance(t, dict) and (t.get("details") or {}).get("error") == "DeviceNotRegistered"}
    if gone:
        with _lock:
            d = _load()
            for k in [k for k, v in d.items() if v.get("token") in gone]:
                d.pop(k)
            _save(d)


def on_event(event_type: str, data: Any) -> bool:
    """Called for every HUD event; pushes in a background thread. True when a push was queued."""
    if os.environ.get("SUBWAY_NO_NOTIFY") == "1" or not isinstance(data, dict):
        return False
    got = message_for(event_type, data)
    if not got:
        return False
    key, msg = got
    now = time.time()
    with _lock:
        if key in _seen:
            return False
        _seen[key] = now
        for k in [k for k, t in _seen.items() if now - t > 86400]:
            _seen.pop(k)
    tokens = _live_tokens()
    if not tokens:
        return False
    msgs = [dict(msg, to=t, sound="default", priority="high") for t in tokens]
    threading.Thread(target=_send, args=(msgs,), daemon=True).start()
    return True
