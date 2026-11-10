"""Caffeinate / Stay-Awake process management and settings integration.

Manages system stay-awake assertions (caffeinate on macOS, systemd-inhibit on Linux,
SetThreadExecutionState on Windows) to prevent system sleep during worker execution or on demand.
"""
from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import subprocess
import sys
import threading
from typing import Any, Dict, Optional

_caffeinate_proc: Optional[subprocess.Popen] = None
_caffeinate_lock = threading.Lock()


def sync_caffeinate() -> bool:
    """Synchronize system stay-awake state based on config and active workers."""
    import server as _s
    global _caffeinate_proc
    cfg = _s._load_json("config.json")
    mode = str(cfg.get("caffeinate", "off")).lower().strip()
    should_run = False
    if mode == "always":
        should_run = True
    elif mode == "workers":
        try:
            should_run = any(w.get("status") in ("launching", "running") for w in _s._all_workers())
        except Exception:
            should_run = False

    # Windows support via SetThreadExecutionState
    if sys.platform == "win32":
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002
            if should_run:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
            else:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            return should_run
        except Exception:
            return False

    with _caffeinate_lock:
        if should_run:
            if _caffeinate_proc is None or _caffeinate_proc.poll() is not None:
                # macOS
                if sys.platform == "darwin":
                    caff = "/usr/bin/caffeinate"
                    if os.path.exists(caff):
                        with contextlib.suppress(Exception):
                            _caffeinate_proc = subprocess.Popen([caff, "-d", "-i", "-m"],
                                                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                               stderr=subprocess.DEVNULL, start_new_session=True)
                # Linux with systemd-inhibit
                elif sys.platform.startswith("linux") and shutil.which("systemd-inhibit"):
                    with contextlib.suppress(Exception):
                        _caffeinate_proc = subprocess.Popen(["systemd-inhibit", "--what=idle:sleep", "--why=Subway active", "sleep", "infinity"],
                                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                           stderr=subprocess.DEVNULL, start_new_session=True)
            return _caffeinate_proc is not None
        else:
            if _caffeinate_proc is not None:
                if _caffeinate_proc.poll() is None:
                    with contextlib.suppress(Exception):
                        _caffeinate_proc.terminate()
                        try:
                            _caffeinate_proc.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            _caffeinate_proc.kill()
                _caffeinate_proc = None
            return False


def _stop_caffeinate() -> None:
    """Ensure child stay-awake process is terminated upon exit."""
    global _caffeinate_proc
    with _caffeinate_lock:
        if _caffeinate_proc is not None:
            with contextlib.suppress(Exception):
                if _caffeinate_proc.poll() is None:
                    _caffeinate_proc.terminate()
                    _caffeinate_proc.wait(timeout=1.0)
            _caffeinate_proc = None


atexit.register(_stop_caffeinate)
