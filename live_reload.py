"""Live reload for the Subway MCP server.

Detects changes to .py files in the repo directory, safety-checks them in a
subprocess, and reloads logic modules in-process with importlib.reload.
Never re-execs the process, so worker supervision, background threads, and
HUD state survive. The subway tool is a stable dispatcher that resolves
tools by name at call time, so newly reloaded tools are reachable.
"""
from __future__ import annotations

import contextlib
import glob
import importlib
import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple

_state_lock = threading.Lock()
_last_reload_time: float = 0.0
_last_mtimes: Dict[str, float] = {}
_last_reload_error: str = ""
_reload_lock = threading.Lock()

_RELOAD_ORDER = [
    "binary_discovery",
    "store",
    "chat_readers",
    "permissions",
    "workers",
    "worker_supervision",
    "hud_server",
    "local_models",
    "usage",
    "cli_tools",
    # NOTE: "live_reload" must NOT be in _RELOAD_ORDER. Reloading this module
    # while _reload_lock is held would re-initialize its globals (_last_mtimes,
    # _reload_lock, etc.) mid-check and corrupt the reload state machine.
]


def _server() -> Any:
    import server as _s
    return _s


def _rebind_into_server(_s: Any) -> int:
    """Point server attributes imported by name from a reloaded module at the new objects."""
    n = 0
    for mod_name in _RELOAD_ORDER:
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        for attr, val in list(vars(_s).items()):
            if callable(val) and getattr(val, "__module__", None) == mod_name and hasattr(mod, attr):
                new = getattr(mod, attr)
                if new is not val:
                    setattr(_s, attr, new)
                    n += 1
    return n


def _repo_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def t_subway(tool: str, args: Optional[Dict[str, Any]] = None) -> str:
    """Stable dispatcher to call any Subway tool by name."""
    _s = _server()
    if tool == "list":
        tools = [
            {"name": name, "inputSchema": spec.get("inputSchema", {})}
            for name, (_fn, spec) in _s.TOOLS.items()
        ]
        return json.dumps(tools, indent=2)
    entry = _s.TOOLS.get(tool)
    if entry is None:
        msg = f"Error: unknown tool {tool}"
        if _last_reload_error:
            msg += f"\nLast reload error: {_last_reload_error}"
        return msg
    # Resolve the newest function from the reloaded module whenever possible.
    # _s.TOOLS may still hold stale function objects if a reload happened
    # without rebuilding the table (we rebuild on reload, but dynamic
    # resolution here is a safety net for any path that bypasses it).
    fn = entry[0]
    fn_name = getattr(fn, "__name__", None)
    fn_module = getattr(fn, "__module__", None)
    if fn_name and fn_module and fn_module in sys.modules:
        try:
            fresh = getattr(sys.modules[fn_module], fn_name, None)
            if fresh is not None and fresh is not fn:
                fn = fresh
        except Exception:
            pass
    result = fn(**(args or {}))
    if isinstance(result, str):
        return result
    return json.dumps(result, default=str) if not isinstance(result, str) else result


def _scan_mtimes(repo_dir: str) -> Dict[str, float]:
    mtimes: Dict[str, float] = {}
    for path in sorted(glob.glob(os.path.join(repo_dir, "*.py"))):
        try:
            mtimes[path] = os.path.getmtime(path)
        except OSError:
            continue
    return mtimes


def _rebuild_server_tools(_s: Any) -> None:
    """Re-bind every entry in server.TOOLS to the reloaded function object.

    importlib.reload re-executes the logic modules in _RELOAD_ORDER, creating
    new function objects, but server.TOOLS still holds the old references
    captured when server was first imported. Walk TOOLS and, via each stale
    function's __module__/__name__, fetch the fresh object from the reloaded
    module. Entries whose module isn't reloaded (or no longer exists) are
    left untouched. The table is updated in place so existing TOOLS holders
    see the new functions.
    """
    tools = getattr(_s, "TOOLS", None)
    if not isinstance(tools, dict):
        return
    for name, entry in list(tools.items()):
        if not (isinstance(entry, tuple) and len(entry) == 2):
            continue
        fn, spec = entry
        fn_name = getattr(fn, "__name__", None)
        fn_module = getattr(fn, "__module__", None)
        if not fn_name or not fn_module:
            continue
        # live_reload itself is never reloaded (not in _RELOAD_ORDER), but
        # skip it here too for defense in depth.
        if fn_module == "live_reload" or fn_module == __name__:
            continue
        mod = sys.modules.get(fn_module)
        if mod is None:
            continue
        fresh = getattr(mod, fn_name, None)
        if fresh is not None and fresh is not fn:
            tools[name] = (fresh, spec)


def check_and_reload(force: bool = False) -> Tuple[bool, str]:
    """Reload logic modules if any .py file changed. Returns (reloaded, message)."""
    global _last_reload_time, _last_mtimes, _last_reload_error
    _s = _server()
    with _reload_lock:
        try:
            cfg = _s._load_json("config.json") or {}
        except Exception:
            cfg = {}
        if cfg.get("reload_gate") is True and not force:
            return (False, "gate_locked")

        repo_dir = _repo_dir()
        current = _scan_mtimes(repo_dir)
        if not _last_mtimes:
            _last_mtimes = dict(current)
            return (False, "no_changes")

        changed = {
            path: mtime
            for path, mtime in current.items()
            if path not in _last_mtimes or _last_mtimes[path] != mtime
        }
        if not changed:
            return (False, "no_changes")

        # Wait for stillness: avoid reading half-written files.
        newest = max(changed.values())
        if time.time() - newest < 2:
            return (False, "waiting_for_stillness")

        # Safety pre-check in a subprocess; never reload on syntax/import error.
        try:
            proc = subprocess.run(
                [sys.executable, "-c", "import server"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=15,
            )
            failed = proc.returncode != 0
            err = (proc.stderr or proc.stdout or "").strip()
        except subprocess.TimeoutExpired as exc:
            failed = True
            err = f"pre-check timeout: {exc}"
        except OSError as exc:
            failed = True
            err = f"pre-check failed: {exc}"

        if failed:
            # Remember this broken state so we do not re-run the subprocess
            # on every subsequent call (retry storm with 15s timeout each).
            _last_reload_error = err or "pre-check failed"
            _last_mtimes = dict(current)
            return (False, err)

        for name in _RELOAD_ORDER:
            if name in sys.modules:
                try:
                    importlib.reload(sys.modules[name])
                except Exception as exc:
                    _last_reload_error = f"reload {name}: {exc}"
                    _last_mtimes = dict(current)
                    return (False, _last_reload_error)

        # server.py imports some helpers by name (from binary_discovery import ...), so
        # those names still point at the old functions until they are re-bound here.
        _rebind_into_server(_s)

        # Rebuild server.TOOLS so it holds the freshly reloaded functions
        # instead of stale references captured at initial import time.
        try:
            _rebuild_server_tools(_s)
        except Exception as exc:
            _last_reload_error = f"rebuild TOOLS: {exc}"
            _last_mtimes = dict(current)
            return (False, _last_reload_error)

        _last_reload_error = ""
        _last_reload_time = time.time()
        _last_mtimes = dict(current)
        try:
            _s.send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        except Exception:
            pass
        return (True, "reloaded")
