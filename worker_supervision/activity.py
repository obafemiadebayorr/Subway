from __future__ import annotations
import sys
sys.modules.setdefault("server", sys.modules[__name__])
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
    ctypes = None
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
    fcntl = None
import server as _s


def get_stall_timeout(meta: Dict[str, Any]) -> float:
    """Configurable provider stall timeout (default 300s for Copilot/Antigravity, 120s otherwise)."""
    cfg = _s._load_json("config.json")
    default_timeout = 300.0 if meta.get("provider") in ("copilot", "antigravity") else _s.DEFAULT_STALL_TIMEOUT
    val = (
        meta.get("stall_timeout")
        or os.environ.get("SUBWAY_STALL_TIMEOUT")
        or (cfg.get("stall_timeout") if isinstance(cfg, dict) else None)
        or (cfg.get("worker_stall_timeout") if isinstance(cfg, dict) else None)
        or default_timeout
    )
    try:
        timeout = float(val)
    except (TypeError, ValueError):
        timeout = default_timeout
    model = str(meta.get("model") or "").lower()
    effort = str(meta.get("effort") or "").lower()
    if "thinking" in model or effort in ("high", "max"):
        timeout = max(timeout * 2, timeout + 120.0)
    return timeout


def _worker_stall_timeout(meta: Dict[str, Any], proc: Any) -> float:
    timeout = _s.get_stall_timeout(meta)
    if meta.get("provider") == "copilot" and proc.poll() is None:
        return timeout + 10 * 60
    return timeout


def _process_cpu_time(proc: Any) -> Optional[float]:
    """Return user plus system CPU seconds for a live child process when the OS exposes them."""
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class FileTime(ctypes.Structure):
                _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetProcessTimes.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(FileTime), ctypes.POINTER(FileTime),
                ctypes.POINTER(FileTime), ctypes.POINTER(FileTime),
            ]
            kernel32.GetProcessTimes.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel, user = FileTime(), FileTime()
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                ok = kernel32.GetProcessTimes(
                    handle, ctypes.byref(FileTime()), ctypes.byref(FileTime()),
                    ctypes.byref(kernel), ctypes.byref(user),
                )
                if not ok:
                    return None
                ticks = ((kernel.high << 32) | kernel.low) + ((user.high << 32) | user.low)
                return ticks / 10_000_000
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError, TypeError, ValueError):
            return None
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="ascii") as stat_file:
            fields = stat_file.read().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / float(os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError, AttributeError, TypeError):
        pass
    try:
        result = subprocess.run(
            ["ps", "-o", "cputime=", "-p", str(int(pid))],
            capture_output=True, text=True, timeout=1,
        )
        if result.returncode:
            return None
        value = result.stdout.strip()
        match = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)", value)
        if not match:
            return None
        days, hours, minutes = (int(part or 0) for part in match.groups()[:3])
        return days * 86400 + hours * 3600 + minutes * 60 + float(match.group(4))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _file_activity_signature(path: str) -> Tuple[bool, int, int]:
    try:
        stat = os.stat(path)
        return True, stat.st_size, stat.st_mtime_ns
    except OSError:
        return False, 0, 0


def _copilot_has_open_tool_execution(events_path: str) -> bool:
    open_tools: List[Optional[str]] = []

    def event_call_id(event: Dict[str, Any]) -> Optional[str]:
        data = event.get("data")
        sources = [data, event] if isinstance(data, dict) else [event]
        for source in sources:
            for key in ("toolCallId", "tool_call_id", "callId", "call_id", "id"):
                value = source.get(key)
                if value is not None and value != "":
                    return str(value)
        return None

    try:
        with open(events_path, "r", encoding="utf-8", errors="replace") as events_file:
            for line in events_file:
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(event, dict):
                    continue
                event_type = event.get("type")
                call_id = event_call_id(event)
                if event_type == "tool.execution_start":
                    open_tools.append(call_id)
                elif event_type == "tool.execution_complete":
                    if call_id is not None and call_id in open_tools:
                        open_tools.remove(call_id)
                    elif call_id is None:
                        anonymous = next((i for i, active_id in enumerate(open_tools) if active_id is None), None)
                        if anonymous is not None:
                            open_tools.pop(anonymous)
                        elif open_tools:
                            open_tools.pop(0)
    except OSError:
        return False
    return bool(open_tools)


def _worker_folder_activity(meta: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
    """A cheap change fingerprint of the worker's folder: git status plus each changed path's size and mtime."""
    folder = meta.get("worktree") or meta.get("project_dir")
    if not folder or not os.path.isdir(folder):
        return None
    real = os.path.realpath(folder)
    if real in (os.path.realpath(_s.HOME), os.path.realpath(os.sep)):
        return None  # Too big to watch; the other signals decide.
    try:
        status = subprocess.run(["git", "status", "--porcelain=1", "-z", "--untracked-files=all"],
                                cwd=real, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        status = None
    if status is not None and status.returncode == 0:
        digest = hashlib.sha256(status.stdout)
        for entry in status.stdout.split(b"\0"):
            path = entry[3:].decode("utf-8", "replace") if len(entry) > 3 else ""
            if path:
                digest.update(repr(_s._file_activity_signature(os.path.join(real, path))).encode())
        return "git", digest.hexdigest()
    newest_mtime, seen = 0, 0
    for root, dirs, files in os.walk(real):
        dirs[:] = [name for name in dirs if name not in (".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__")]
        for name in files:
            seen += 1
            if seen > 5000:
                return "mtime", newest_mtime
            try:
                newest_mtime = max(newest_mtime, os.stat(os.path.join(root, name)).st_mtime_ns)
            except OSError:
                continue
    return "mtime", newest_mtime


def _opencode_worker_session_activity(meta: Dict[str, Any], started_at: float) -> Optional[Tuple[str, Any]]:
    """Return the matching OpenCode session's last update timestamp."""
    db_path = os.path.join(_s.OPENCODE_DIR, "opencode.db")
    if not os.path.exists(db_path):
        return None
    worker_dir = meta.get("worktree") or meta.get("project_dir") or _s.HOME
    directory = os.path.abspath(worker_dir)
    real_directory = os.path.realpath(directory)
    uri_path = urllib.parse.quote(db_path, safe="/")
    try:
        con = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=0.1)
        try:
            con.execute("PRAGMA busy_timeout=100")
            session = con.execute(
                "select id, time_created from session_v2 where directory in (?, ?) "
                "and (case when time_created > 100000000000 then time_created / 1000.0 else time_created end) >= ? "
                "order by (case when time_created > 100000000000 then time_created / 1000.0 else time_created end) desc limit 1",
                (directory, real_directory, started_at),
            ).fetchone()
            updated = con.execute(
                "select max(case when time_updated > 100000000000 then time_updated / 1000.0 else time_updated end) "
                "from session_message where session_id=?",
                (session[0],),
            ).fetchone() if session else None
        finally:
            con.close()
    except (OSError, sqlite3.Error):
        return None
    latest = updated[0] if updated else None
    return (str(session[0]), latest) if session and latest is not None else None


def _worker_activity_signals(meta: Dict[str, Any], proc: Any, started_at: float) -> Dict[str, Any]:
    """Activity readings are advisory: one that fails is skipped, never fatal to the supervisor."""
    try:
        return _s._collect_worker_activity_signals(meta, proc, started_at)
    except Exception:
        return {}


def _collect_worker_activity_signals(meta: Dict[str, Any], proc: Any, started_at: float) -> Dict[str, Any]:
    signals: Dict[str, Any] = {}
    log_path = meta.get("log_file")
    if log_path:
        signals["output"] = _s._file_activity_signature(log_path)
    if meta.get("provider") == "copilot" and meta.get("copilot_session_id"):
        events_path = os.path.join(_s.COPILOT_DIR, "session-state", str(meta["copilot_session_id"]), "events.jsonl")
        signals["copilot_events"] = _s._file_activity_signature(events_path)
        signals["copilot_tool_running"] = _s._copilot_has_open_tool_execution(events_path)
    if meta.get("provider") == "opencode":
        activity = _s._opencode_worker_session_activity(meta, started_at)
        if activity is not None:
            signals["opencode"] = activity
    folder_activity = _s._worker_folder_activity(meta)
    if folder_activity is not None:
        signals["worker_folder"] = folder_activity
    cpu_time = _s._process_cpu_time(proc)
    if cpu_time is not None:
        signals["cpu"] = cpu_time
    waiting_on = _s._active_subworkers(str(meta.get("id") or ""))
    if waiting_on:
        # A lead waiting on its own sub-workers is busy, even when it prints nothing.
        signals["subworkers"] = (tuple(waiting_on), time.time())
    return signals


def _update_worker_activity(
    previous: Dict[str, Any], last_active: Dict[str, float], current: Dict[str, Any], now: float,
) -> None:
    for sig, value in current.items():
        if sig not in previous or previous[sig] != value:
            last_active[sig] = now
    previous.update(current)


def _worker_is_stalled(
    last_active: Dict[str, float], now: float, timeout: float, active: bool = False,
) -> bool:
    return not active and (not last_active or all(now - seen >= timeout for seen in last_active.values()))


def _poll_opencode_worker_retry(
    meta: Dict[str, Any],
    started_at: float,
    seen: Optional[Set[Tuple[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """Read an OpenCode worker's latest retry error without waiting on its database."""
    db_path = os.path.join(_s.OPENCODE_DIR, "opencode.db")
    if not os.path.exists(db_path):
        return None

    worker_dir = meta.get("worktree") or meta.get("project_dir") or _s.HOME
    directory = os.path.abspath(worker_dir)
    real_directory = os.path.realpath(directory)
    uri_path = urllib.parse.quote(db_path, safe="/")
    try:
        con = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=0.1)
        try:
            con.execute("PRAGMA busy_timeout=100")
            session = con.execute(
                "select id from session_v2 where directory in (?, ?) "
                "and (case when time_created > 100000000000 then time_created / 1000.0 else time_created end) >= ? "
                "order by (case when time_created > 100000000000 then time_created / 1000.0 else time_created end) desc limit 1",
                (directory, real_directory, started_at),
            ).fetchone()
            if not session:
                return None
            message = con.execute(
                "select seq, data from session_message where session_id=? and type='assistant' "
                "order by seq desc limit 1",
                (session[0],),
            ).fetchone()
        finally:
            con.close()
    except (OSError, sqlite3.Error):
        return None
    if not message:
        return None
    seq, raw_data = message
    try:
        data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
    except (TypeError, ValueError):
        return None
    retry = data.get("retry") if isinstance(data, dict) else None
    error = retry.get("error") if isinstance(retry, dict) else None
    if not isinstance(error, dict):
        return None

    session_id = str(session[0])
    error_type = _s.one_line(str(error.get("type") or "unknown"), 120)
    message_text = _s.one_line(str(error.get("message") or ""), 500)
    attempt = retry.get("attempt", "?")
    retry_at = retry.get("at")
    retry_key = (session_id, f"{seq}:{attempt}:{retry_at}:{error_type}:{message_text}")
    if seen is not None and retry_key in seen:
        return None
    if seen is not None:
        seen.add(retry_key)

    try:
        retry_clock = _dt.datetime.fromtimestamp(float(retry_at) / 1000).strftime("%H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        retry_clock = "--:--"
    line = f"[opencode] {error_type}: {message_text} (retry {attempt} at {retry_clock})"
    log_path = meta.get("log_file")
    if log_path:
        with contextlib.suppress(OSError):
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(line + "\n")

    reason = f"{error_type}: {message_text}".strip(": ")
    with _s._state_lock("workers"):
        current = _s._load_worker(meta.get("id")) or meta
        current["status_reason"] = reason
        _s._save_worker(current)

    quota_error = error_type.lower() == "provider.rate-limit" or bool(
        _s.check_quota_error("opencode", message_text, source="error_event")
    )
    result = None
    if quota_error:
        try:
            reset_time = float(retry_at) / 1000
        except (TypeError, ValueError):
            reset_time = None
        handler_reason = f"Quota/usage limit error: {reason}"
        result = _s._handle_worker_exhaustion_or_stall(
            str(meta.get("id") or ""), meta, handler_reason, is_stall=False, reset_time=reset_time,
        )
    return {"reason": reason, "quota": quota_error, "result": result}

