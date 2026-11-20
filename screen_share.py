"""Screen sharing: still snapshots of this machine for a linked station with the screen grant."""
import base64
import json
import os
import secrets
import shutil
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


def _linux_snapshot(path: str, max_w: int) -> dict:
    """Linux still capture: Wayland compositors via grim, X11 via scrot/maim/gnome-screenshot.

    Several tools only write PNG, so PIL (if present) converts to JPEG; otherwise the
    PNG is returned as-is with its own mime so viewers still render it.
    """
    png = path[:-4] + ".png"
    candidates = [
        ["grim", "-o", png],                       # wlroots compositors (Sway, Hyprland)
        ["scrot", "-z", png],                      # X11
        ["maim", png],                              # X11
        ["gnome-screenshot", "-f", png],            # GNOME
    ]
    for cmd in candidates:
        if not shutil.which(cmd[0]):
            continue
        code, _o, err = _run(cmd)
        if code == 0 and os.path.isfile(png) and os.path.getsize(png) > 0:
            mime = "image/png"
            try:
                from PIL import Image
                img = Image.open(png)
                img.thumbnail((max_w, max_w))
                w, h = img.size
                img.convert("RGB").save(path, format="JPEG", quality=70)
                mime = "image/jpeg"
            except ImportError:
                with open(png, "rb") as fh:
                    data = base64.b64encode(fh.read()).decode()
                os.remove(png)
                try:
                    import struct
                    with open(png, "rb") as fh:
                        head = fh.read(33)
                    w = h = 0
                    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
                        w, h = struct.unpack(">II", head[16:24])
                except Exception:
                    w = h = 0
                return {"image_b64": data, "w": w, "h": h, "ts": time.time(), "mime": mime}
            os.remove(png)
            with open(path, "rb") as fh:
                data = base64.b64encode(fh.read()).decode()
            return {"image_b64": data, "w": w, "h": h, "ts": time.time(), "mime": mime}
    missing = ", ".join(c[0] for c in candidates)
    return {"error": f"Linux screen capture needs one of: {missing} (or screenshot permission on Wayland). None were found.", "status": 503}


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
        if sys.platform.startswith("linux"):
            return _linux_snapshot(path, max_w)
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
KINDS = ("offer", "answer", "ice", "bye", "fallback")
_OVER = ("expired", "ended", "declined")


def _now() -> float:
    return time.time()


_LEASED: set = set()


def _lease_for(sid: str) -> None:
    """With no physical display, hold a 1440x900 vdisplay for this session."""
    try:
        import vdisplay
        res = vdisplay.acquire_lease("screen:" + sid, 1440, 900)
        if res.get("leased"):
            _LEASED.add(sid)
        else:
            vdisplay.release_lease("screen:" + sid)
    except Exception:
        pass


def _unlease(sid: str) -> None:
    if sid in _LEASED:
        _LEASED.discard(sid)
        try:
            import vdisplay
            vdisplay.release_lease("screen:" + sid)
        except Exception:
            pass


def _expire() -> None:
    now = _now()
    for s in list(_SESSIONS.values()):
        if s["state"] in _OVER:
            _unlease(s["id"])
        if s["state"] == "pending" and now - s["created"] > PENDING_TTL:
            s["state"] = "expired"
            _unlease(s["id"])
        elif s["state"] == "live" and now - s["touched"] > IDLE_TTL:
            s["state"] = "ended"
            _unlease(s["id"])
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
            _lease_for(sid)
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
            _unlease(sid)
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
        if s["state"] in _OVER:
            _unlease(s["id"])
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


# --- Headless capture (native helper -> framed JPEG pipe), low-BW and stills fallback ---
import hashlib

MAX_FRAME_BYTES = 256 * 1024
MIN_STILLS_INTERVAL = 2.0
CAPTURE_MODES = {
    "default": {"w": 1440, "h": 900, "fps": 30},
    "lowbw": {"w": 1280, "h": 720, "fps": 8},      # dirty-JPEG delta mode, 5-10 fps
    "stills": {"w": 1280, "h": 720, "fps": 0.5},   # poll >= 2s
}


def capture_mode(name: str = "default") -> dict:
    m = dict(CAPTURE_MODES.get(name) or CAPTURE_MODES["default"])
    m["mode"] = name if name in CAPTURE_MODES else "default"
    if m["mode"] == "lowbw":
        m["fps"] = max(5, min(m["fps"], 10))
    return m


def stills_interval(requested=None) -> float:
    try:
        return max(MIN_STILLS_INTERVAL, float(requested if requested is not None else MIN_STILLS_INTERVAL))
    except (TypeError, ValueError):
        return MIN_STILLS_INTERVAL


def frame_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_frame(stream, max_bytes: int = MAX_FRAME_BYTES):
    """Read one 4-byte big-endian length-prefixed frame. None on EOF; ValueError if over the cap."""
    head = stream.read(4)
    if not head or len(head) < 4:
        return None
    n = int.from_bytes(head, "big")
    if n > max_bytes:
        # drain so the stream stays aligned, then refuse
        left = n
        while left > 0:
            chunk = stream.read(min(left, 65536))
            if not chunk:
                break
            left -= len(chunk)
        raise ValueError("frame too large")
    data = stream.read(n)
    if len(data) < n:
        return None
    return data


class HeadlessCaptureSession:
    """Reads framed output from a native helper process. Nothing runs until start() is called."""

    def __init__(self, cmd, mode: str = "default", popen=None, max_bytes: int = MAX_FRAME_BYTES):
        self.cmd = list(cmd)
        self.mode = capture_mode(mode)
        self.max_bytes = min(int(max_bytes), MAX_FRAME_BYTES)
        self._popen = popen or subprocess.Popen
        self._proc = None
        self._last_hash = None
        self.dropped = 0

    def start(self):
        if self._proc is None:
            self._proc = self._popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return self

    def next_frame(self) -> dict:
        """One frame dict, {"not_modified": True} if unchanged, or {"error": ...}."""
        if self._proc is None:
            return {"error": "capture not started", "status": 409}
        while True:
            try:
                data = read_frame(self._proc.stdout, self.max_bytes)
            except ValueError:
                self.dropped += 1
                return {"error": "frame too large", "status": 413}
            if data is None:
                return {"error": "capture helper ended", "status": 503}
            h = frame_hash(data)
            if h == self._last_hash:
                return {"not_modified": True, "hash": h}
            self._last_hash = h
            return {"image_b64": base64.b64encode(data).decode(), "bytes": len(data), "hash": h,
                    "ts": time.time(), "mime": "image/jpeg", "mode": self.mode["mode"]}

    def stop(self):
        p, self._proc = self._proc, None
        if p is not None:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass


_STILL_HASH = {}


def still_frame(sid="", max_w: int = 1280, last_hash=None) -> dict:
    """Stills fallback: a snapshot, or {"not_modified": True} when identical to last_hash / the last still."""
    snap = snapshot(max_w)
    if snap.get("error"):
        return snap
    h = frame_hash(base64.b64decode(snap["image_b64"]))
    prev = last_hash if last_hash is not None else _STILL_HASH.get(sid)
    _STILL_HASH[sid] = h
    if prev == h:
        return {"not_modified": True, "hash": h, "ts": snap["ts"]}
    snap["hash"] = h
    return snap


def webrtc_failed(sid, reason: str = "timeout") -> dict:
    """WebRTC timed out or failed: tell the viewer to fall back to stills (polled >= 2s)."""
    res = signal(sid, "sharer", "fallback", {"fallback": "stills", "reason": str(reason)[:80]})
    if res.get("error"):
        return res
    return {"ok": True, "fallback": "stills", "interval": MIN_STILLS_INTERVAL, "reason": reason}
