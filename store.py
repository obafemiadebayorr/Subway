"""Paths, config, state locks, atomic JSON writes and generic helpers.

Moved from server.py (Subway 1.3.5). Rule: server-level names are read late through `_s.NAME` so tests that patch `server.X` still apply. Import this module through server, not directly."""

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


def ttl_cached(seconds: float) -> Callable[..., Any]:
    """Reuse a slow result for a while. HUD polls arrive every second per tab, and this data barely changes."""
    # Under the test sandbox every cache is a cross-test leak: a mocked fetcher
    # from one test stays cached for up to 120s and the next test reads stale
    # quota/routing state. Tests run with caching off.
    if os.environ.get("SUBWAY_NO_CACHE"):
        seconds = 0.0
    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        store: Dict[Any, Tuple[float, Any]] = {}

        def inner(*args: Any, **kwargs: Any) -> Any:
            key = (args, tuple(sorted(kwargs.items())), _s.SUBWAY_DIR)
            hit = store.get(key)
            now = time.monotonic()
            if hit and now - hit[0] < seconds:
                return hit[1]
            value = fn(*args, **kwargs)
            store[key] = (now, value)
            return value
        inner.clear = store.clear  # type: ignore[attr-defined]
        return inner
    return wrap


def log(*a: Any) -> None:
    print("[subway-context]", *a, file=sys.stderr, flush=True)


def clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def one_line(s: str, n: int = 120) -> str:
    return _s.clip(re.sub(r"\s+", " ", s or "").strip(), n)


def fmt_ts(ts: Any) -> str:
    if ts is None or ts == "":
        return ""
    try:
        if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.isdigit()):
            v = float(ts)
            while v > 1e11:
                v /= 1000.0
            return _dt.datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M")
        s = str(ts).replace("Z", "+00:00")
        s = re.sub(r"(\.\d{6})\d+", r"\1", s)
        d = _dt.datetime.fromisoformat(s.split(" +")[0].strip())
        if d.tzinfo is not None:
            d = d.astimezone()
        return d.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)[:16]


def sort_key_ts(ts: Any) -> float:
    try:
        if isinstance(ts, (int, float)):
            v = float(ts)
            while v > 1e11:
                v /= 1000.0
            return v
        s = str(ts).replace("Z", "+00:00")
        s = re.sub(r"(\.\d{6})\d+", r"\1", s).split(" +")[0].strip()
        d = _dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except Exception:
        return 0.0


def _ensure_subway_dir() -> None:
    os.makedirs(_s.SUBWAY_DIR, exist_ok=True)
    os.makedirs(os.path.join(_s.SUBWAY_DIR, "inbox"), exist_ok=True)
    os.makedirs(_s.WORKERS_DIR, exist_ok=True)
    os.makedirs(_s.TRACKS_DIR, exist_ok=True)
    os.makedirs(_s._terminals_dir(), exist_ok=True)
    with contextlib.suppress(OSError):  # Holds prompts, worker logs and the HUD token.
        os.chmod(_s.SUBWAY_DIR, 0o700)


@contextlib.contextmanager
def _state_lock(name: str, timeout: float = 10.0) -> Iterator[None]:
    """Serialize a read-modify-write on shared state across every Subway process.

    Not re-entrant: never nest two locks with the same name.
    Uses non-blocking flock with retries up to timeout to prevent permanent lockouts.
    """
    _s._ensure_subway_dir()
    fd = os.open(os.path.join(_s.SUBWAY_DIR, f".{name}.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    deadline = time.time() + max(0.1, timeout)
    try:
        if fcntl is not None:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (BlockingIOError, OSError):
                    if time.time() >= deadline:
                        _s.log(f"Timeout ({timeout}s) acquiring lock .{name}.lock; proceeding to avoid fleet deadlock")
                        break
                    time.sleep(0.05)
        yield
    finally:
        if acquired and fcntl is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _write_json_atomic(path: str, data: Any) -> None:
    """Write JSON through a unique temp file so concurrent writers can't collide."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def _load_json(filename: str) -> Dict[str, Any]:
    path = os.path.join(_s.SUBWAY_DIR, filename)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            _s.log(f"could not read {filename}: {e}")
            return {}
    return {}


def _save_json(filename: str, data: Dict[str, Any]) -> None:
    _s._ensure_subway_dir()
    _s._write_json_atomic(os.path.join(_s.SUBWAY_DIR, filename), data)


def _write_if_changed(path: str, content: str) -> None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            if f.read() == content:
                return
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _file_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _escape_xml(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _find_executable(name: str, fallback_paths: Tuple[str, ...] = ()) -> Optional[str]:
    executable = shutil.which(name)
    if executable:
        return executable
    for path in fallback_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _home(*parts: str) -> str:
    return os.path.join(_s.HOME, *parts)
