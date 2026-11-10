from __future__ import annotations

from __future__ import annotations

import base64

import concurrent.futures

import contextlib

import contextvars

import datetime as _dt

import difflib

import glob

import hashlib

import hmac

import http.server

import json

import math

import os

import queue

import re

import secrets

import select

import shutil

import signal

import sqlite3

import subprocess

import sys

import tempfile

import threading

try:
    import ctypes
except ImportError:
    ctypes = None  # type: ignore[assignment]

import time

import traceback

import urllib.error

import urllib.parse

import urllib.request

import uuid

from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

import terminals

import native_terminals

import sse_stream

import threads

try:
    from . import data as _hud_data
except ImportError:  # run as a flat script
    import data as _hud_data

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

import server as _s

# HUD stragglers (section 6/7) — canonical definitions; re-exported via server.py and hud_server
_notification_log_lock = threading.Lock()
_hud_cache_lock = threading.Lock()
_hud_cache: Dict[str, Dict[str, Any]] = {}  # project -> {"data", "at", "stale", "busy"}
_hud_token_cache: Dict[str, str] = {}
EDITOR_HINT: Dict[str, str] = {}
MAX_BODY_BYTES = 1024 * 1024
_hud_server_instance: Optional[http.server.HTTPServer] = None
_sampler_running = False
_hud_opened_on_use: bool = False
_hud_open_lock = threading.Lock()
_hud_open_thread: Optional[threading.Thread] = None


def _hud_port() -> int:
    with contextlib.suppress(Exception):
        return int((_s._load_json("hud.json") or {}).get("port") or 7890)
    return 7890

def hud_token() -> str:
    """Per-install secret for the HUD API, readable only by this user."""
    _s._ensure_subway_dir()
    path = os.path.join(_s.SUBWAY_DIR, "hud_token")
    if path in _s._hud_token_cache:
        return _s._hud_token_cache[path]
    token = ""
    with _s._state_lock("hud_token"):
        with contextlib.suppress(OSError):
            with open(path, "r", encoding="utf-8") as f:
                token = f.read().strip()
        if not token:
            token = secrets.token_urlsafe(32)
            fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(token)
    _s._hud_token_cache[path] = token
    return token

def _hud_url(port: int) -> str:
    return f"http://{_s.HUD_HOST}:{port}"

def _hud_probe(port: int) -> Optional[Dict[str, Any]]:
    """What's on the HUD port: a dict if something answers, None if nothing does."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{_s._hud_url(port)}/healthz", timeout=1) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError:
        return {"version": "older"}
    except (OSError, ValueError):
        return None

def _autostart_hud(port: int = _s.HUD_PORT) -> None:
    """Start the Live HUD on stdio server start if hud_autostart is not false and HUD is not running."""
    if os.environ.get("SUBWAY_HUD_AUTOSTART") in ("0", "false", "off"):
        return
    cfg = _s._load_json("config.json")
    val = cfg.get("hud_autostart")
    if val is False or str(val).lower() in ("false", "off", "0"):
        return
    with _s._state_lock("hud_autostart"):
        if _s._hud_probe(port) is not None:
            return
        env = dict(os.environ, SUBWAY_HUD_NO_OPEN="1", SUBWAY_PROJECT_DIR=os.environ.get("SUBWAY_PROJECT_DIR") or os.getcwd())
        subprocess.Popen(
            [sys.executable, os.path.abspath(_s.__file__), "hud", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            cwd=_s.HOME,
            env=env,
        )

def _editors() -> List[Dict[str, Any]]:
    """Each editor's bundle ids, app name, CLI and link scheme, as read from their Info.plist files."""
    return [
        {"name": "Antigravity IDE", "ids": ("com.google.antigravity-ide",), "cli": [os.path.expanduser(
            "~/.antigravity-ide/antigravity-ide/bin/agy-ide")], "scheme": "antigravity-ide"},
        {"name": "VS Code", "ids": ("com.microsoft.VSCode", "com.microsoft.VSCodeInsiders"), "cli": ["code"],
         "scheme": "vscode", "hint": "Code"},
        {"name": "Cursor", "ids": ("com.todesktop.230313mzl4w4u92",), "cli": ["cursor"], "scheme": "cursor"},
        {"name": "Windsurf", "ids": ("com.exafunction.windsurf",), "cli": ["windsurf"], "scheme": "windsurf"},
    ]

def _host_editor() -> Optional[Dict[str, Any]]:
    """The editor this process runs inside, so the HUD opens there and not in some other installed editor."""
    bundle = os.environ.get("__CFBundleIdentifier", "")
    ipc = os.environ.get("VSCODE_IPC_HOOK_CLI") or os.environ.get("VSCODE_IPC_HOOK") or ""
    for ed in _s._editors():
        if bundle in ed["ids"] or (ipc and f"/{ed.get('hint', ed['name'])}/" in ipc):
            return ed
    return None

def _cli_path(ed: Dict[str, Any]) -> Optional[str]:
    for cand in ed["cli"]:
        path = shutil.which(cand) or (cand if os.path.exists(cand) else None)
        if path:
            return path
    return None

def _has_extension(cli: str) -> bool:
    try:
        res = subprocess.run([cli, "--list-extensions"], capture_output=True, text=True, timeout=5)
        return res.returncode == 0 and "subway.subway" in (res.stdout or "").lower()
    except Exception:
        return False

def _find_editor_cli():
    """Which editor (if any) should show the HUD.

    Config `hud_open_in` decides (default "auto"):
      auto        - the editor we are running inside; otherwise the system browser.
      browser     - always the browser, never an editor.
      editor      - the first installed editor that has the Subway extension.
      antigravity | vscode | cursor | windsurf - that editor, if installed with the extension.

    Before this, the code scanned every installed editor and took the first with the extension.
    That always meant Antigravity, because it is listed first, so the HUD launched Antigravity
    even when the user was nowhere near it. Only the host editor is guessed now; any other
    editor is opt-in. (DeepAI Code's in-app browser is driven by the app itself - its
    deepai-code:// scheme is OAuth-only - so a server process cannot target it; the browser is
    the honest fallback there.)
    """
    host = _s._host_editor()
    if host:  # Inside an editor: that editor or nothing, never a different one.
        cli = _s._cli_path(host)
        if cli and _s._has_extension(cli):
            return cli, host["scheme"]
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        vsix = os.path.join(root, "vscode", "subway.vsix")
        _s.EDITOR_HINT["text"] = (f"The Subway extension isn't installed in {host['name']}, so the HUD opened in your browser. "
                               f"To open it in the editor: {cli or host['cli'][0]} --install-extension {vsix}")
        return None, None

    cfg = {}
    with contextlib.suppress(Exception):
        cfg = _s._load_json("config.json") or {}
    preference = str(cfg.get("hud_open_in") or "auto").strip().lower()

    if preference in ("browser", "external", "off", "none", "app"):
        return None, None

    def _first_with_extension(wanted=None):
        for ed in _s._editors():
            if wanted and wanted not in ed["name"].lower() and wanted not in str(ed.get("scheme", "")):
                continue
            cli = _s._cli_path(ed)
            if cli and _s._has_extension(cli):
                return cli, ed["scheme"]
        return None, None

    if preference in ("editor", "any"):
        return _first_with_extension()
    if preference not in ("auto", "host"):
        cli, scheme = _first_with_extension(preference)
        return (cli, scheme) if cli else (None, None)

    # auto / host: only claim an editor when we can tell we are inside one. A terminal inside
    # VS Code counts too; anything else (DeepAI Code, a plain terminal) goes to the browser.
    if os.environ.get("TERM_PROGRAM") == "vscode":
        for ed in _s._editors():
            cli = _s._cli_path(ed)
            if cli:
                return cli, ed["scheme"]
    return None, None

def _open_hud(port: int) -> None:
    """Open the dashboard as soon as it's up. Set SUBWAY_HUD_NO_OPEN=1 to skip this."""
    with contextlib.suppress(Exception):
        _s._write_json_atomic(os.path.join(_s.SUBWAY_DIR, "hud.json"), {"port": port, "url": _s._hud_url(port), "pid": os.getpid()})
    if os.environ.get("SUBWAY_HUD_NO_OPEN") != "1":
        opened = False
        cli, scheme = _s._find_editor_cli()
        if cli and scheme:
            uri = f"{scheme}://subway.subway/open?port={port}"
            try:
                res = subprocess.run([cli, "--open-url", uri], capture_output=True, timeout=5)
                if res.returncode == 0:
                    opened = True
            except Exception:
                opened = False
        if not opened:
            # "app" asks the desktop app to show it in its own in-app browser. DeepAI Code's
            # deepai-code:// scheme is OAuth-only, so this is a best-effort hand-off of the URL;
            # if the app ignores it, the browser below still gives the user the dashboard.
            cfg = {}
            with contextlib.suppress(Exception):
                cfg = _s._load_json("config.json") or {}
            if str(cfg.get("hud_open_in") or "").lower() == "app" and sys.platform == "darwin":
                with contextlib.suppress(Exception):
                    opened = subprocess.run(["open", "-a", "DeepAI Code", _s._hud_url(port)],
                                            capture_output=True, timeout=5).returncode == 0
        if not opened:
            import webbrowser
            with contextlib.suppress(Exception):
                webbrowser.open(_s._hud_url(port))
            if _s.EDITOR_HINT.get("text"):
                _s.log(_s.EDITOR_HINT["text"])
                with contextlib.suppress(Exception):
                    _s.notify_desktop_event("editor_extension_missing|editor", "Subway", _s.EDITOR_HINT["text"][:180])

def t_start_hud(port: int = 0) -> str:
    """Start the Live HUD as its own process, so it outlives any one agent window."""
    port = int(port or _s.HUD_PORT)
    probe = _s._hud_probe(port)
    if probe:
        if probe.get("version") == _s.VERSION:
            return f"Live HUD is already running at {_s._hud_url(port)}."
        return f"Port {port} is held by an older HUD (v{probe.get('version', '?')}). Stop it, then start again."
    subprocess.Popen([sys.executable, os.path.abspath(_s.__file__), "hud", str(port)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, cwd=_s.HOME, env=dict(os.environ, SUBWAY_PROJECT_DIR=os.environ.get("SUBWAY_PROJECT_DIR") or os.getcwd()))
    deadline = time.time() + 5
    while time.time() < deadline:
        if _s._hud_probe(port):
            _s._open_hud(port)
            return f"Live HUD started at {_s._hud_url(port)}."
        time.sleep(0.2)
    return f"Error: the HUD didn't come up on port {port}."

def _stop_hud_on_port(port: int) -> None:
    """Stop any HUD server process (in-process or external) using the specified port."""
    if _s._hud_server_instance is not None:
        try:
            _s._hud_server_instance.shutdown()
            _s._hud_server_instance.server_close()
        except Exception:
            pass
        _s._hud_server_instance = None

    pids_to_kill: Set[int] = set()
    probe = _s._hud_probe(port)
    if probe and isinstance(probe, dict) and probe.get("pid"):
        pids_to_kill.add(int(probe["pid"]))

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{_s._hud_url(port)}/api/ping", timeout=1) as r:
            data = json.loads(r.read())
            if isinstance(data, dict) and data.get("pid"):
                pids_to_kill.add(int(data["pid"]))
    except Exception:
        pass

    try:
        hud_info = _s._load_json("hud.json") or {}
        if int(hud_info.get("port") or 0) == port and hud_info.get("pid"):
            pids_to_kill.add(int(hud_info["pid"]))
    except Exception:
        pass

    if shutil.which("lsof"):
        try:
            res = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip():
                for p in res.stdout.strip().split():
                    if p.isdigit():
                        pids_to_kill.add(int(p))
        except Exception:
            pass

    my_pid = os.getpid()
    for pid in pids_to_kill:
        if pid == my_pid or pid <= 1:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    deadline = time.time() + 2.5
    while time.time() < deadline:
        if _s._hud_probe(port) is None:
            break
        time.sleep(0.1)
    else:
        for pid in pids_to_kill:
            if pid != my_pid and pid > 1:
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)

def t_restart_hud(port: int = 0) -> str:
    """Stop any stale HUD/server process on the port, reload live modules, and start the latest server cleanly."""
    port = int(port or _s.HUD_PORT)
    if hasattr(_s, "check_and_reload"):
        with contextlib.suppress(Exception):
            _s.check_and_reload(force=True)

    _stop_hud_on_port(port)

    subprocess.Popen([sys.executable, os.path.abspath(_s.__file__), "hud", str(port)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, cwd=_s.HOME, env=dict(os.environ, SUBWAY_PROJECT_DIR=os.environ.get("SUBWAY_PROJECT_DIR") or os.getcwd()))
    deadline = time.time() + 5
    while time.time() < deadline:
        if _s._hud_probe(port):
            _s._open_hud(port)
            return f"Live HUD restarted cleanly at {_s._hud_url(port)}."
        time.sleep(0.2)
    return f"Error: the HUD didn't come up on port {port}."

def start_hud_server(port: int = _s.HUD_PORT, daemon: bool = True) -> Optional[http.server.HTTPServer]:
    """Serve the HUD on loopback only. There is deliberately no option to bind wider."""
    if _s._hud_server_instance is not None:
        return _s._hud_server_instance
    try:
        httpd = _s.SubwayHUDServer((_s.HUD_HOST, port), _s.SubwayHUDHandler)
    except OSError as e:
        _s.log(f"HUD could not bind {_s.HUD_HOST}:{port}: {e}")
        return None
    _s._hud_server_instance = httpd
    threading.Thread(target=httpd.serve_forever, daemon=daemon).start()
    if not _s._sampler_running:
        _s._sampler_running = True
        threading.Thread(target=_s._sampler_loop, daemon=True).start()
    # /api/events reads a file spool so events produced by other processes (the MCP
    # server, workers) actually reach subscribers. One tailer per HUD process.
    if not getattr(_s, "_sse_tailer_started", False):
        _s._sse_tailer_started = True
        sse_stream.add_spool_listener(_hud_data.patch_cached_worker)
        sse_stream.start_spool_tailer()
    _s.log(f"Subway Live HUD running at {_s._hud_url(port)}")
    return httpd

def _maybe_open_hud_on_use() -> None:
    with _s._hud_open_lock:
        if _s._hud_opened_on_use:
            return
        _s._hud_opened_on_use = True

    if os.environ.get("SUBWAY_NO_NOTIFY") or os.environ.get("SUBWAY_WORKER"):
        return
    cfg = _s._load_json("config.json")
    val = cfg.get("hud_open_on_use")
    if val is False or str(val).lower() in ("false", "off", "0"):
        return

    def _opener() -> None:
        try:
            port = _s._hud_port()
            if _s._hud_probe(port) is None:
                _s.t_start_hud(port)
            else:
                _s._open_hud(port)
        except Exception:
            pass

    t = threading.Thread(target=_opener, daemon=True)
    _s._hud_open_thread = t
    t.start()


t_run_server = t_restart_hud
