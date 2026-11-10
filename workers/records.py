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
import threads

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

import server as _s

def _archive_dir() -> str:
    return os.path.join(_s.WORKERS_DIR, "archive")

def _worker_file(worker_id: str) -> str:
    """Worker ids reach this from MCP and HTTP callers, so nothing but a real id builds a path."""
    if not _s.WORKER_ID_RE.match(worker_id or ""):
        raise ValueError(f"invalid worker id '{worker_id}'")
    _s._ensure_subway_dir()
    return os.path.join(_s.WORKERS_DIR, f"{worker_id}.json")

def _exit_file(worker_id: str) -> str:
    return os.path.join(_s.WORKERS_DIR, f"{worker_id}.exit")

def _log_file(worker_id: str) -> str:
    return os.path.join(_s.WORKERS_DIR, f"{worker_id}.log")

def _supervisor_log_file(worker_id: str) -> str:
    """Where a worker's supervisor writes its stderr. Without it a crash leaves no evidence."""
    _s._ensure_subway_dir()
    try:
        os.makedirs(_s.WORKERS_DIR, exist_ok=True)
    except OSError:
        pass
    return os.path.join(_s.WORKERS_DIR, f"{worker_id}.supervisor.log")


def _load_worker(worker_id: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_s._worker_file(worker_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None

def _save_worker(meta: Dict[str, Any]) -> None:
    _s._write_json_atomic(_s._worker_file(meta["id"]), meta)

def _strftime(dt: _dt.datetime, fmt: str) -> str:
    """Format datetime handling Windows vs Unix non-padded flags (%-d, %-I vs %#d, %#I)."""
    if sys.platform == "win32":
        fmt = fmt.replace("%-d", "%#d").replace("%-I", "%#I").replace("%-m", "%#m").replace("%-H", "%#H")
    return dt.strftime(fmt)

def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        if not pid or not ctypes:
            return False
        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False

def _log_tail(log_file: Optional[str], n: int = 25) -> str:
    if not log_file or not os.path.exists(log_file):
        return ""
    with contextlib.suppress(OSError):
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:]).strip()
    return ""

