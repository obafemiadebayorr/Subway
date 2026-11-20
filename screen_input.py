"""Remote input injection for an own-station `control` grant.

Backends are injectable (tests pass a stub). Coordinates arrive normalised to [0, 1]
and are clamped, then mapped onto the active virtual-display bounds (x, y, w, h).
Held modifier keys are tracked per session so stop/panic/unlink can release them.
"""
import shutil
import subprocess
import sys
import threading

MODIFIERS = ("shift", "ctrl", "control", "alt", "option", "meta", "cmd", "super", "win")
_LOCK = threading.Lock()
_SESSIONS: dict = {}  # session_id -> {"peer": str, "held": set, "bounds": tuple|None}
_BACKEND = None
_BOUNDS = None  # (x, y, w, h) override for the active vdisplay


class Backend:
    """Interface. Implementations perform real OS injection."""

    def bounds(self):
        return (0, 0, 1920, 1080)

    def mouse(self, action, x, y, button="left"):
        raise NotImplementedError

    def key(self, key, down):
        raise NotImplementedError


class XdotoolBackend(Backend):
    def _run(self, *a):
        if shutil.which("xdotool"):
            subprocess.run(["xdotool", *a], check=False, timeout=5)

    def mouse(self, action, x, y, button="left"):
        b = {"left": "1", "middle": "2", "right": "3"}.get(button, "1")
        self._run("mousemove", str(int(x)), str(int(y)))
        if action == "down":
            self._run("mousedown", b)
        elif action == "up":
            self._run("mouseup", b)

    def key(self, key, down):
        self._run("keydown" if down else "keyup", str(key))


class MacBackend(Backend):
    def mouse(self, action, x, y, button="left"):
        try:
            import Quartz as Q
        except Exception:
            return
        btn = {"left": Q.kCGMouseButtonLeft, "right": Q.kCGMouseButtonRight}.get(button, Q.kCGMouseButtonLeft)
        kinds = {"move": Q.kCGEventMouseMoved, "down": Q.kCGEventLeftMouseDown if btn == Q.kCGMouseButtonLeft else Q.kCGEventRightMouseDown,
                 "up": Q.kCGEventLeftMouseUp if btn == Q.kCGMouseButtonLeft else Q.kCGEventRightMouseUp}
        ev = Q.CGEventCreateMouseEvent(None, kinds.get(action, Q.kCGEventMouseMoved), (x, y), btn)
        Q.CGEventPost(Q.kCGHIDEventTap, ev)

    def key(self, key, down):
        try:
            import Quartz as Q
            code = int(key)
        except Exception:
            return
        Q.CGEventPost(Q.kCGHIDEventTap, Q.CGEventCreateKeyboardEvent(None, code, bool(down)))


class WindowsBackend(Backend):
    def mouse(self, action, x, y, button="left"):
        try:
            import ctypes
            u = ctypes.windll.user32
        except Exception:
            return
        u.SetCursorPos(int(x), int(y))
        flags = {("down", "left"): 0x2, ("up", "left"): 0x4, ("down", "right"): 0x8, ("up", "right"): 0x10}
        f = flags.get((action, button))
        if f:
            u.mouse_event(f, 0, 0, 0, 0)  # SendInput wrapper

    def key(self, key, down):
        try:
            import ctypes
            ctypes.windll.user32.keybd_event(int(key), 0, 0 if down else 2, 0)
        except Exception:
            return


def default_backend():
    if sys.platform == "darwin":
        return MacBackend()
    if sys.platform.startswith("win"):
        return WindowsBackend()
    return XdotoolBackend()


def set_backend(backend, bounds=None):
    """Inject a backend (tests) and optional vdisplay bounds; returns the previous pair."""
    global _BACKEND, _BOUNDS
    prev = (_BACKEND, _BOUNDS)
    _BACKEND, _BOUNDS = backend, bounds
    return prev


def _backend():
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = default_backend()
    return _BACKEND


def clamp01(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:  # NaN
        return 0.0
    return 0.0 if f < 0.0 else 1.0 if f > 1.0 else f


def map_point(nx, ny, bounds=None):
    x, y, w, h = bounds or _BOUNDS or _backend().bounds()
    return x + clamp01(nx) * max(w - 1, 0), y + clamp01(ny) * max(h - 1, 0)


def start_control(session_id, peer=""):
    with _LOCK:
        _SESSIONS.setdefault(str(session_id), {"peer": str(peer), "held": set()})
    return {"ok": True, "session_id": str(session_id)}


def is_active(session_id):
    with _LOCK:
        return str(session_id) in _SESSIONS


def held(session_id):
    with _LOCK:
        s = _SESSIONS.get(str(session_id))
        return sorted(s["held"]) if s else []


def inject(session_id, event):
    """event: {kind: move|down|up|key, x, y, button, key, down}. Returns {ok} or {error}."""
    sid = str(session_id)
    with _LOCK:
        s = _SESSIONS.get(sid)
    if s is None:
        return {"error": "no active control session", "status": 409}
    kind = str((event or {}).get("kind") or "")
    be = _backend()
    if kind in ("move", "down", "up"):
        x, y = map_point(event.get("x"), event.get("y"))
        be.mouse(kind, x, y, str(event.get("button") or "left"))
    elif kind == "key":
        key = str(event.get("key") or "")[:40]
        if not key:
            return {"error": "key required", "status": 400}
        down = bool(event.get("down", True))
        if key.lower() in MODIFIERS:
            with _LOCK:
                (s["held"].add if down else s["held"].discard)(key)
        be.key(key, down)
    else:
        return {"error": "unknown input kind", "status": 400}
    return {"ok": True}


def stop_control(session_id):
    """Panic/stop/unlink: key-up every held modifier and end the session."""
    with _LOCK:
        s = _SESSIONS.pop(str(session_id), None)
    if s is None:
        return {"ok": True, "released": []}
    released = sorted(s["held"])
    be = _backend()
    for k in released:
        try:
            be.key(k, False)
        except Exception:
            pass
    return {"ok": True, "released": released}


def stop_all(peer=None):
    with _LOCK:
        ids = [i for i, s in _SESSIONS.items() if peer is None or s["peer"] == peer]
    out = []
    for i in ids:
        out += stop_control(i)["released"]
    return {"ok": True, "released": out}
