#!/usr/bin/env python3
"""Built-in desktop tools for Subway workers (roadmap 66 P8b): a tiny stdio MCP server, macOS only.

Started for workers that hold the "desktop" capability. Screenshots land in <subway dir>/hub_media/<worker id>/, so
a worker started from a linked station brings them back with its report. Needs Screen Recording (screenshots) and
Accessibility (clicks, typing) for the app that runs Subway; the tools say so when macOS refuses.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import time

PROTOCOL = "2024-11-05"
TOOLS = [
    {"name": "screenshot", "description": "Capture the whole screen. Returns the image and where it was saved.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "open", "description": "Open a URL, a file, or an app by name (e.g. 'Safari').",
     "inputSchema": {"type": "object", "required": ["target"], "properties": {"target": {"type": "string"}}}},
    {"name": "click", "description": "Click at screen point x,y (points, top-left origin). double=true for a double click.",
     "inputSchema": {"type": "object", "required": ["x", "y"], "properties": {
         "x": {"type": "integer"}, "y": {"type": "integer"}, "double": {"type": "boolean"}}}},
    {"name": "type_text", "description": "Type text into the focused app.",
     "inputSchema": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}},
    {"name": "key", "description": "Press a key with modifiers, e.g. 'cmd+l', 'return', 'cmd+shift+4', 'tab'.",
     "inputSchema": {"type": "object", "required": ["combo"], "properties": {"combo": {"type": "string"}}}},
    {"name": "list_apps", "description": "Names of the visible apps and the frontmost one.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "mirror_screenshot", "description": "Screenshot the iPhone through iPhone Mirroring. Coordinates for mirror_tap/mirror_swipe are pixels in this image.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "mirror_tap", "description": "Tap the iPhone at pixel x,y of the last mirror_screenshot.",
     "inputSchema": {"type": "object", "required": ["x", "y"], "properties": {"x": {"type": "number"}, "y": {"type": "number"}}}},
    {"name": "mirror_swipe", "description": "Swipe on the iPhone from x1,y1 to x2,y2 (pixels of the last mirror_screenshot), over ms milliseconds.",
     "inputSchema": {"type": "object", "required": ["x1", "y1", "x2", "y2"], "properties": {
         "x1": {"type": "number"}, "y1": {"type": "number"}, "x2": {"type": "number"}, "y2": {"type": "number"}, "ms": {"type": "integer"}}}},
    {"name": "mirror_scroll", "description": "Scroll the iPhone screen: dy > 0 shows what is above, dy < 0 what is below. Pixel units, e.g. -400.",
     "inputSchema": {"type": "object", "properties": {"dy": {"type": "integer"}, "dx": {"type": "integer"}, "focus": {"type": "boolean"}}}},
    {"name": "mirror_type", "description": "Type text into the focused field on the iPhone.",
     "inputSchema": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}},
    {"name": "mirror_key", "description": "Press a key on the iPhone, e.g. 'return', 'delete', 'cmd+1' (Home), 'cmd+2' (App Switcher), 'cmd+3' (Spotlight).",
     "inputSchema": {"type": "object", "required": ["combo"], "properties": {"combo": {"type": "string"}}}},
    {"name": "mirror_home", "description": "Go to the iPhone Home Screen.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "mirror_open_app", "description": "Open an iPhone app by name (e.g. 'WhatsApp') via Spotlight.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}},
]
KEY_CODES = {"return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "escape": 53, "esc": 53,
             "left": 123, "right": 124, "down": 125, "up": 126, "home": 115, "end": 119, "pageup": 116, "pagedown": 121}
MODS = {"cmd": "command down", "command": "command down", "shift": "shift down", "alt": "option down",
        "option": "option down", "ctrl": "control down", "control": "control down"}


def subway_dir() -> str:
    return os.environ.get("SUBWAY_DIR") or os.path.join(os.path.expanduser("~"), ".subway")


def worker_id() -> str:
    return os.environ.get("SUBWAY_WORKER_ID") or os.environ.get("SUBWAY_WORKER") or "desktop"


def _run(cmd, timeout=20):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def _osa(script: str):
    return _run(["osascript", "-e", script])


def _refused(err: str, what: str) -> str:
    low = err.lower()
    if "not allowed" in low or "assistive" in low or "1002" in low or "-25211" in low:
        return f"macOS refused: give Accessibility to the app running Subway (System Settings → Privacy & Security → Accessibility), then retry {what}."
    return f"{what} failed: {err[:200]}"


def _audit(tool: str, args: dict) -> None:
    try:
        line = json.dumps({"ts": time.time(), "worker": worker_id(), "tool": tool,
                           "args": {k: (v if k != "text" else f"<{len(str(v))} chars>") for k, v in (args or {}).items()}})
        with open(os.path.join(subway_dir(), "desktop_actions.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


MIRROR_APP = "iPhone Mirroring"
KEYMAP = {**{c: k for c, k in zip("asdfhgzxcv", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9])},
          "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
          "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40,
          "n": 45, "m": 46, **KEY_CODES}
_SHOT = {}
NOT_OPEN = ("iPhone Mirroring is not showing the phone. Keep this Mac unlocked and the iPhone locked and nearby, "
            "then open iPhone Mirroring once by hand if it asks to connect.")
NOT_TRUSTED = ("macOS blocks taps and typing: give Accessibility to the app running Subway (System Settings → Privacy & "
               "Security → Accessibility), then quit and reopen it.")


def _mirror_bin() -> str:
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "mirror", "subway_mirror.swift")
    dest = os.path.join(subway_dir(), "bin", "subway-mirror")
    if not os.path.isfile(dest) or os.path.getmtime(dest) < os.path.getmtime(src):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        code, _o, err = _run(["swiftc", "-O", "-o", dest, src], timeout=300)
        if code != 0:
            raise RuntimeError("could not build the mirror helper: " + err[:300])
    return dest


def _mirror(*args) -> dict:
    code, out, err = _run([_mirror_bin(), *[str(x) for x in args]], timeout=30)
    try:
        return json.loads(out.splitlines()[-1]) if out else {"error": err or f"exit {code}"}
    except ValueError:
        return {"error": out[:200] or err[:200]}


def _mirror_window(open_if_missing: bool = True):
    w = _mirror("window")
    if not w.get("id") and open_if_missing:
        _run(["open", "-a", MIRROR_APP])
        time.sleep(3)
        w = _mirror("window")
    return w if w.get("id") else None


_PREV: dict = {"pid": 0}
_HOLD = {"n": 0}


def _front() -> None:
    """Bring iPhone Mirroring forward (it only takes input when frontmost), remembering who had focus."""
    if not _PREV["pid"]:
        r = _mirror("frontpid")
        if r.get("pid") and r.get("name") != MIRROR_APP:
            _PREV["pid"] = int(r["pid"])
    if os.environ.get("SUBWAY_MIRROR_RAISE") == "1":
        _run(["open", "-a", MIRROR_APP])
        time.sleep(0.35)


def _restore() -> None:
    """Give focus back to the app the user was in (skipped while a multi-step action holds it)."""
    if _HOLD["n"] > 0 or not _PREV["pid"]:
        return
    pid, _PREV["pid"] = _PREV["pid"], 0
    time.sleep(0.15)
    _mirror("activate", str(pid))


def _to_screen(x, y):
    w = _mirror_window()
    if not w:
        return None
    pw, ph = _SHOT.get("w") or w["w"] * 2, _SHOT.get("h") or w["h"] * 2
    return w["x"] + float(x) * w["w"] / pw, w["y"] + float(y) * w["h"] / ph


def _needs_trust():
    if not _mirror("trusted").get("trusted"):
        return [{"type": "text", "text": NOT_TRUSTED}], True
    return None


def t_mirror_screenshot(_a):
    w = _mirror_window()
    if not w:
        return [{"type": "text", "text": NOT_OPEN}], True
    d = os.path.join(subway_dir(), "hub_media", worker_id())
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"iphone-{int(time.time() * 1000)}.png")
    code, _o, err = _run(["screencapture", "-x", "-o", "-l", str(w["id"]), path])
    if code != 0 or not os.path.isfile(path) or os.path.getsize(path) == 0:
        return [{"type": "text", "text": "Could not capture iPhone Mirroring (Screen Recording?): " + err[:160]}], True
    _c, sz, _e = _run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path])
    nums = [int(t) for t in sz.split() if t.isdigit()]
    if len(nums) >= 2:
        _SHOT.update(w=nums[-2], h=nums[-1])
    with open(path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode()
    return [{"type": "image", "data": data, "mimeType": "image/png"},
            {"type": "text", "text": f"iPhone screen, {_SHOT.get('w')}x{_SHOT.get('h')} px. Use pixel coordinates from this "
                                     f"image with mirror_tap / mirror_swipe. Saved {path}"}], False


def t_mirror_tap(a):
    bad = _needs_trust()
    if bad:
        return bad
    p = _to_screen(a.get("x"), a.get("y"))
    if not p:
        return [{"type": "text", "text": NOT_OPEN}], True
    _front()
    r = _mirror("click", round(p[0], 1), round(p[1], 1))
    _restore()
    return [{"type": "text", "text": "Tapped" if r.get("ok") else f"Tap failed: {r.get('error')}"}], not r.get("ok")


def t_mirror_swipe(a):
    bad = _needs_trust()
    if bad:
        return bad
    p1, p2 = _to_screen(a.get("x1"), a.get("y1")), _to_screen(a.get("x2"), a.get("y2"))
    if not p1 or not p2:
        return [{"type": "text", "text": NOT_OPEN}], True
    _front()
    r = _mirror("drag", round(p1[0], 1), round(p1[1], 1), round(p2[0], 1), round(p2[1], 1), int(a.get("ms") or 300))
    _restore()
    return [{"type": "text", "text": "Swiped" if r.get("ok") else f"Swipe failed: {r.get('error')}"}], not r.get("ok")


def t_mirror_scroll(a):
    bad = _needs_trust()
    if bad:
        return bad
    w = _mirror_window()
    if not w:
        return [{"type": "text", "text": NOT_OPEN}], True
    _front()
    if a.get("focus"):
        _mirror("click", round(w["x"] + w["w"] / 2, 1), round(w["y"] + w["h"] / 2, 1))
    r = _mirror("scroll", int(a.get("dy") or 0), int(a.get("dx") or 0))
    _restore()
    return [{"type": "text", "text": "Scrolled" if r.get("ok") else f"Scroll failed: {r.get('error')}"}], not r.get("ok")


def t_mirror_type(a):
    bad = _needs_trust()
    if bad:
        return bad
    if not _mirror_window():
        return [{"type": "text", "text": NOT_OPEN}], True
    _front()
    r = _mirror("type", str(a.get("text") or ""))
    _restore()
    if not r.get("ok"):
        return [{"type": "text", "text": f"Typing failed: {r.get('error')}"}], True
    msg = f"Typed {r.get('typed', 0)} characters"
    if r.get("skipped"):
        msg += f"; these have no US key and were skipped: {r['skipped'][:40]}"
    return [{"type": "text", "text": msg}], False


def _mirror_combo(combo):
    parts = [p.strip().lower() for p in str(combo or "").split("+") if p.strip()]
    if not parts or parts[-1] not in KEYMAP:
        return None
    mods = ",".join({"command": "cmd", "option": "alt", "control": "ctrl"}.get(p, p) for p in parts[:-1])
    return KEYMAP[parts[-1]], mods


def t_mirror_key(a):
    bad = _needs_trust()
    if bad:
        return bad
    got = _mirror_combo(a.get("combo"))
    if not got:
        return [{"type": "text", "text": f"unknown key combo {a.get('combo')}"}], True
    if not _mirror_window():
        return [{"type": "text", "text": NOT_OPEN}], True
    _front()
    r = _mirror("key", got[0], *([got[1]] if got[1] else []))
    _restore()
    return [{"type": "text", "text": f"Pressed {a.get('combo')}" if r.get("ok") else f"Key failed: {r.get('error')}"}], not r.get("ok")


def t_mirror_home(_a):
    return t_mirror_key({"combo": "cmd+1"})


def t_mirror_open_app(a):
    """Open an iPhone app by name through Spotlight (cmd+3 in iPhone Mirroring)."""
    name = str(a.get("name") or "").strip()
    if not name:
        return [{"type": "text", "text": "name is required"}], True
    _HOLD["n"] += 1
    try:
        content, err = t_mirror_key({"combo": "cmd+3"})
        if err:
            return content, True
        time.sleep(0.6)
        content, err = t_mirror_type({"text": name})
        if err:
            return content, True
        time.sleep(0.8)
        content, err = t_mirror_key({"combo": "return"})
        if err:
            return content, True
        time.sleep(1.2)
        return [{"type": "text", "text": f"Opened {name} on the iPhone (take mirror_screenshot to check)."}], False
    finally:
        _HOLD["n"] -= 1
        _restore()


def t_screenshot(_a):
    d = os.path.join(subway_dir(), "hub_media", worker_id())
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"screen-{int(time.time() * 1000)}.png")
    code, _o, err = _run(["screencapture", "-x", "-t", "png", path])
    if code != 0 or not os.path.isfile(path) or os.path.getsize(path) == 0:
        return [{"type": "text", "text": "macOS refused the screenshot: give Screen Recording to the app running Subway "
                 "(System Settings → Privacy & Security → Screen Recording). " + err[:160]}], True
    with open(path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode()
    return [{"type": "image", "data": data, "mimeType": "image/png"}, {"type": "text", "text": f"Saved {path}"}], False


def t_open(a):
    target = str(a.get("target") or "").strip()
    if not target:
        return [{"type": "text", "text": "target is required"}], True
    is_url_or_path = "://" in target or target.startswith(("/", "~"))
    cmd = ["open", os.path.expanduser(target)] if is_url_or_path else ["open", "-a", target]
    code, _o, err = _run(cmd)
    return [{"type": "text", "text": f"Opened {target}" if code == 0 else f"Could not open {target}: {err[:200]}"}], code != 0


def t_click(a):
    x, y = int(a.get("x")), int(a.get("y"))
    dbl = bool(a.get("double"))
    if shutil.which("cliclick"):
        code, _o, err = _run(["cliclick", f"{'dc' if dbl else 'c'}:{x},{y}"])
    else:
        code, _o, err = _osa(f'tell application "System Events" to click at {{{x}, {y}}}' + ("\n" if not dbl else
                             f'\ndelay 0.05\ntell application "System Events" to click at {{{x}, {y}}}'))
    return [{"type": "text", "text": f"Clicked {x},{y}" if code == 0 else _refused(err, "the click")}], code != 0


def t_type(a):
    text = str(a.get("text") or "")
    esc = text.replace("\\", "\\\\").replace('"', '\\"')
    code, _o, err = _osa(f'tell application "System Events" to keystroke "{esc}"')
    return [{"type": "text", "text": f"Typed {len(text)} characters" if code == 0 else _refused(err, "typing")}], code != 0


def t_key(a):
    parts = [p.strip().lower() for p in str(a.get("combo") or "").split("+") if p.strip()]
    if not parts:
        return [{"type": "text", "text": "combo is required"}], True
    mods = [MODS[p] for p in parts[:-1] if p in MODS]
    k = parts[-1]
    using = f" using {{{', '.join(mods)}}}" if mods else ""
    if k in KEY_CODES:
        script = f'tell application "System Events" to key code {KEY_CODES[k]}{using}'
    elif len(k) == 1:
        script = f'tell application "System Events" to keystroke "{k}"{using}'
    else:
        return [{"type": "text", "text": f"unknown key {k}"}], True
    code, _o, err = _osa(script)
    return [{"type": "text", "text": f"Pressed {a.get('combo')}" if code == 0 else _refused(err, "the key press")}], code != 0


def t_list_apps(_a):
    code, out, err = _osa('tell application "System Events" to get name of every process whose visible is true')
    _c2, front, _e2 = _osa('tell application "System Events" to get name of first process whose frontmost is true')
    if code != 0:
        return [{"type": "text", "text": _refused(err, "listing apps")}], True
    return [{"type": "text", "text": f"Frontmost: {front}\nVisible: {out}"}], False


HANDLERS = {"screenshot": t_screenshot, "open": t_open, "click": t_click, "type_text": t_type, "key": t_key,
            "list_apps": t_list_apps, "mirror_screenshot": t_mirror_screenshot, "mirror_tap": t_mirror_tap,
            "mirror_swipe": t_mirror_swipe, "mirror_scroll": t_mirror_scroll, "mirror_type": t_mirror_type,
            "mirror_key": t_mirror_key, "mirror_home": t_mirror_home, "mirror_open_app": t_mirror_open_app}


def handle(msg: dict):
    """One JSON-RPC message -> response dict, or None for notifications."""
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                                                       "serverInfo": {"name": "subway-desktop", "version": "1"}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        p = msg.get("params") or {}
        name, args = p.get("name"), p.get("arguments") or {}
        fn = HANDLERS.get(name)
        if not fn:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown tool {name}"}}
        _audit(name, args)
        try:
            content, is_err = fn(args)
        except Exception as e:  # report, never crash the server
            content, is_err = [{"type": "text", "text": f"{name} failed: {e}"}], True
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": content, "isError": is_err}}
    if mid is None:
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
