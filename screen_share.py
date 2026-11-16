"""Screen sharing: still snapshots of this machine for a linked station with the screen grant."""
import base64
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time

MAX_W = 1600
DENIED = ("macOS refused the screenshot: give Screen Recording to the app running Subway "
          "(System Settings → Privacy & Security → Screen Recording).")


def _run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", str(e)


def snapshot(max_w: int = MAX_W) -> dict:
    """One JPEG of the main display, at most max_w wide. Nothing is kept on disk."""
    try:
        max_w = max(320, min(int(max_w or MAX_W), MAX_W))
    except (TypeError, ValueError):
        max_w = MAX_W
    fd, path = tempfile.mkstemp(prefix="subway-screen-", suffix=".jpg")
    os.close(fd)
    try:
        if sys.platform == "win32":
            try:
                from PIL import ImageGrab
            except ImportError:
                return {"error": "Screen capture on Windows requires Pillow (pip install Pillow).", "status": 503}
            img = ImageGrab.grab(all_screens=True)
            img.thumbnail((max_w, max_w))
            w, h = img.size
            img.convert("RGB").save(path, format="JPEG", quality=70)
            with open(path, "rb") as fh:
                data = base64.b64encode(fh.read()).decode()
            return {"image_b64": data, "w": w, "h": h, "ts": time.time(), "mime": "image/jpeg"}
        code, _o, err = _run(["screencapture", "-x", "-t", "jpg", path])
        if code != 0 or not os.path.isfile(path) or os.path.getsize(path) == 0:
            return {"error": DENIED + (" " + err[:160] if err else ""), "status": 503}
        _run(["sips", "-Z", str(max_w), "-s", "formatOptions", "70", path])
        _c, out, _e = _run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path])
        w = h = 0
        for line in out.splitlines():
            if "pixelWidth" in line:
                w = int(line.split()[-1])
            if "pixelHeight" in line:
                h = int(line.split()[-1])
        with open(path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode()
        return {"image_b64": data, "w": w, "h": h, "ts": time.time(), "mime": "image/jpeg"}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ---- Live share sessions (WebRTC signalling relay; no media passes through here) ----
_LOCK = threading.Lock()
_SESSIONS: dict = {}
PENDING_TTL, IDLE_TTL, MAX_SIGNAL = 120, 90, 24000
KINDS = ("offer", "answer", "ice", "bye")
_OVER = ("expired", "ended", "declined")


def _now() -> float:
    return time.time()


def _expire() -> None:
    now = _now()
    for s in list(_SESSIONS.values()):
        if s["state"] == "pending" and now - s["created"] > PENDING_TTL:
            s["state"] = "expired"
        elif s["state"] == "live" and now - s["touched"] > IDLE_TTL:
            s["state"] = "ended"
        if s["state"] in _OVER and now - s["touched"] > 600:
            _SESSIONS.pop(s["id"], None)


def _public(s: dict) -> dict:
    return {k: s[k] for k in ("id", "viewer_peer", "viewer_name", "state", "created", "control")}


def _get(sid, viewer_peer=None):
    s = _SESSIONS.get(str(sid or ""))
    if not s or (viewer_peer is not None and s["viewer_peer"] != viewer_peer):
        return None
    return s


def _emit(event_type: str, data: dict) -> None:
    try:
        import sse_stream
        sse_stream.publish(event_type, data)
    except Exception:
        pass


def request(viewer_peer: str, viewer_name: str = "") -> dict:
    """A linked station asks to watch. Nothing is captured until the sharer accepts in their HUD."""
    pub = None
    with _LOCK:
        _expire()
        for s in _SESSIONS.values():
            if s["viewer_peer"] == viewer_peer and s["state"] in ("pending", "live"):
                pub = _public(s)
                break
        else:
            sid = "scr_" + secrets.token_hex(6)
            s = {"id": sid, "viewer_peer": str(viewer_peer), "viewer_name": str(viewer_name)[:60], "state": "pending",
                 "created": _now(), "touched": _now(), "to_sharer": [], "to_viewer": [], "control": False}
            _SESSIONS[sid] = s
            pub = _public(s)
    _emit("screen_request", pub)
    return pub


def signal(sid, side: str, kind: str, payload, viewer_peer=None) -> dict:
    """Queue one signalling message for the other side. side is who sends: "viewer" or "sharer"."""
    if kind not in KINDS:
        return {"error": "unknown signal kind", "status": 400}
    if len(json.dumps(payload)) > MAX_SIGNAL:
        return {"error": "signal too large", "status": 413}
    with _LOCK:
        _expire()
        s = _get(sid, viewer_peer)
        if not s:
            return {"error": "no such share", "status": 404}
        if s["state"] in _OVER:
            return {"error": "share ended", "status": 410}
        box = s["to_viewer"] if side == "sharer" else s["to_sharer"]
        box.append({"kind": kind, "payload": payload})
        del box[:-64]
        s["touched"] = _now()
        if kind == "bye":
            s["state"] = "ended"
        res = {"ok": True, "state": s["state"]}
    _emit("screen_signal", {"id": sid, "kind": kind, "side": side})
    return res


def take(sid, side: str, viewer_peer=None) -> dict:
    """Session state plus every message waiting for `side`, which are removed once read."""
    with _LOCK:
        _expire()
        s = _get(sid, viewer_peer)
        if not s:
            return {"error": "no such share", "status": 404}
        box = s["to_viewer"] if side == "viewer" else s["to_sharer"]
        msgs = list(box)
        box[:] = []
        s["touched"] = _now()
        return dict(_public(s), messages=msgs)


def answer(sid, action: str) -> dict:
    """The sharer accepts, declines or stops a share."""
    states = {"accept": "live", "decline": "declined", "stop": "ended"}
    if action not in states:
        return {"error": "unknown action", "status": 400}
    with _LOCK:
        _expire()
        s = _get(sid)
        if not s:
            return {"error": "no such share", "status": 404}
        if action == "accept" and s["state"] != "pending":
            return {"error": "share is not waiting", "status": 409}
        s["state"] = states[action]
        s["touched"] = _now()
        if action == "stop":
            s["to_viewer"].append({"kind": "bye", "payload": {}})
        pub = _public(s)
    _emit("screen_request", pub)
    return pub


def allow_control(sid, on) -> dict:
    """The sharer allows or revokes remote control for one live share."""
    with _LOCK:
        _expire()
        s = _get(sid)
        if not s:
            return {"error": "no such session", "status": 404}
        if s["state"] not in ("accepted", "live"):
            return {"error": "session is not live", "status": 409}
        s["control"] = bool(on)
        s["touched"] = _now()
        return {"ok": True, "control": bool(on)}


def input_allowed(sid) -> tuple:
    """(ok, reason, viewer_peer): remote input only while the share is live and control was allowed."""
    with _LOCK:
        _expire()
        s = _get(sid)
        if not s:
            return False, "no such session", ""
        if s["state"] != "live":
            return False, "session is not live", s["viewer_peer"]
        if not s.get("control"):
            return False, "control not allowed for this session", s["viewer_peer"]
        return True, "", s["viewer_peer"]


def active() -> list:
    """Shares the sharer's HUD must show: requests waiting for an answer, and live views (for the banner)."""
    with _LOCK:
        _expire()
        return [_public(s) for s in _SESSIONS.values() if s["state"] in ("pending", "live")]
