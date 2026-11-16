"""Caffeinate / Stay-Awake process management and settings integration.

Manages system stay-awake assertions (caffeinate on macOS, systemd-inhibit on Linux,
SetThreadExecutionState on Windows) to prevent system sleep during worker execution or on demand.

The stay-awake process is shared by every Subway process: its pid is recorded in the state folder, so whichever process sees that no worker runs can stop it, not only the one that started it.
"""
from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import signal
import subprocess
import sys
import threading
from typing import List, Optional

_caffeinate_proc: Optional[subprocess.Popen] = None  # only so this process can reap its own child
_caffeinate_lock = threading.Lock()
_NAMES = ("caffeinate", "systemd-inhibit")


def _pid_path() -> str:
    import subway_home
    return os.path.join(subway_home.subway_dir(), "caffeinate.pid")


def _recorded_pid() -> Optional[int]:
    try:
        with open(_pid_path(), encoding="utf-8") as fh:
            return int(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _is_stay_awake(pid: Optional[int]) -> bool:
    """The recorded pid still runs a stay-awake process. Pids are reused, so check its name too."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        comm = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return os.path.basename(comm) in _NAMES


def _stay_awake_cmd() -> Optional[List[str]]:
    if sys.platform == "darwin" and os.path.exists("/usr/bin/caffeinate"):
        return ["/usr/bin/caffeinate", "-d", "-i", "-m"]
    if sys.platform.startswith("linux") and shutil.which("systemd-inhibit"):
        return ["systemd-inhibit", "--what=idle:sleep", "--why=Subway active", "sleep", "infinity"]
    return None


def _start() -> bool:
    global _caffeinate_proc
    cmd = _stay_awake_cmd()
    if not cmd:
        return False
    try:
        _caffeinate_proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    path = _pid_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(_caffeinate_proc.pid))
    return True


def _stop() -> None:
    global _caffeinate_proc
    pid = _recorded_pid()
    if _is_stay_awake(pid):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
    with contextlib.suppress(OSError):
        os.remove(_pid_path())
    if _caffeinate_proc is not None:
        with contextlib.suppress(Exception):
            if _caffeinate_proc.poll() is None:
                _caffeinate_proc.terminate()
            _caffeinate_proc.wait(timeout=1.0)  # reap our own child so it doesn't linger
        _caffeinate_proc = None


def sync_caffeinate() -> bool:
    """Synchronize system stay-awake state based on config and active workers."""
    import server as _s
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

    with _caffeinate_lock, _s._state_lock("caffeinate"):
        if should_run:
            if _is_stay_awake(_recorded_pid()):
                return True  # another Subway process already keeps the machine awake
            return _start()
        _stop()
        return False


def _stop_caffeinate() -> None:
    """At exit, re-check: stop stay-awake if nothing needs it. Another process may still need it."""
    with contextlib.suppress(Exception):
        sync_caffeinate()


atexit.register(_stop_caffeinate)
