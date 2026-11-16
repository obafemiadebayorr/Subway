#!/usr/bin/env python3
"""Wait for Subway wake events and rewake Claude Code via asyncRewake hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import wake_driver


INTERVAL = float(os.environ.get("SUBWAY_WAKE_WAIT_INTERVAL", "0.5"))
MAX_SECONDS = float(os.environ.get("SUBWAY_WAKE_WAIT_MAX", str(23 * 60 * 60)))
COALESCE_SECONDS = 1.5
MAX_LINES = 20
CURSOR_GRACE = 0.001


def _env_float(names, default: float) -> float:
    for name in names:
        raw = os.environ.get(name)
        if not raw:
            continue
        try:
            return float(raw)
        except ValueError:
            pass
    return default


OWNER_RETRY_SECONDS = _env_float(
    ("SUBWAY_WAKE_OWNER_RETRY_SECONDS", "SUBWAY_WAKE_OWNER_RETRY", "SUBWAY_WAKE_WAIT_OWNER_RETRY"),
    10.0,
)
OWNER_RETRY_INTERVAL = _env_float(
    ("SUBWAY_WAKE_OWNER_RETRY_INTERVAL", "SUBWAY_WAKE_WAIT_OWNER_RETRY_INTERVAL"),
    0.25,
)
OWNER_CHECK_SECONDS = _env_float(
    ("SUBWAY_WAKE_OWNER_CHECK_SECONDS", "SUBWAY_WAKE_WAIT_OWNER_CHECK"),
    5.0,
)


def _sessions_dir() -> str:
    override = os.environ.get("SUBWAY_CLAUDE_SESSIONS_DIR")
    if override:
        return override
    try:
        import server as _server
        return os.path.join(_server.CLAUDE_DIR, "sessions")
    except Exception:
        base = os.environ.get("SUBWAY_CLAUDE_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
        return os.path.join(base, "sessions")


def _session_pid(chat_id: str) -> int:
    """Newest Claude session file whose sessionId is this chat, or 0."""
    sessions = _sessions_dir()
    best_pid = 0
    best_mtime = -1.0
    try:
        names = os.listdir(sessions)
    except OSError:
        return 0
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(sessions, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if str(data.get("sessionId") or "") != chat_id:
                continue
            pid = int(data.get("pid") or 0)
            mtime = os.path.getmtime(path)
        except Exception:
            continue
        if pid > 0 and mtime > best_mtime:
            best_pid, best_mtime = pid, mtime
    return best_pid


def _find_owner_pid(chat_id: str) -> int:
    wait_seconds = min(OWNER_RETRY_SECONDS, MAX_SECONDS)
    deadline = time.time() + wait_seconds
    while True:
        pid = _session_pid(chat_id)
        if pid:
            return pid
        if time.time() >= deadline:
            return 0
        time.sleep(OWNER_RETRY_INTERVAL)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except Exception:
        return True
    if sys.platform != "win32":
        try:
            stat = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(pid)],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            if stat.upper().startswith("Z"):
                return False
        except Exception:
            pass
    return True


def _process_args(pid: int) -> str:
    if sys.platform == "win32":
        return ""
    try:
        return subprocess.run(
            ["ps", "-o", "args=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
    except Exception:
        return ""


def _is_headless_print(args: str) -> bool:
    if not args or "stream-json" in args:
        return False
    return " -p " in f" {args} " or "--print" in args


def _spool_dir() -> str:
    override = os.environ.get("SUBWAY_SPOOL_DIR")
    if override:
        os.makedirs(override, exist_ok=True)
        # align wake_driver helpers used by this process
        wake_driver.get_spool_dir = lambda: override  # type: ignore[assignment]
        return override
    return wake_driver.get_spool_dir()


def _safe_chat(chat_id: str) -> str:
    return "".join(c for c in chat_id if c.isalnum() or c in ("-", "_", "@", "."))


def _read_pid(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(f.read().strip() or "0")
    except Exception:
        return None


def _owns_waiter(path: str, pid: int) -> bool:
    return _read_pid(path) == pid


def _write_waiter(path: str, pid: int) -> None:
    tmp = path + f".{pid}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(str(pid))
    os.replace(tmp, path)


def _read_cursor(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return float(f.read().strip())
    except Exception:
        return None


def _write_cursor(path: str, ts: float) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(repr(float(ts)))
    os.replace(tmp, path)


def _collect_new(chat_id: str, cursor: float):
    events, _ = wake_driver.wake_push_tail(chat_id, 0)
    out = []
    for ev in events:
        try:
            ts = float(ev.get("ts", 0) or 0)
        except Exception:
            ts = 0.0
        if ts > cursor + CURSOR_GRACE:
            out.append((ts, ev))
    return out


def _emit(events) -> int:
    lines = [f"[SUBWAY EVENT] {str(ev.get('text', ''))}" for _, ev in events]
    if len(lines) > MAX_LINES:
        more = len(lines) - MAX_LINES
        lines = lines[:MAX_LINES] + [f"... and {more} more"]
    for line in lines:
        print(line, file=sys.stderr)
    return 2


def main() -> int:
    try:
        if os.environ.get("SUBWAY_WORKER_ID") or os.environ.get("SUBWAY_WORKER"):
            return 0
        try:
            hook = json.load(sys.stdin)
        except Exception:
            return 0
        if not isinstance(hook, dict):
            return 0
        chat = hook.get("session_id")
        if not isinstance(chat, str) or not chat:
            return 0

        # Async hooks do not run in the background for headless `claude -p`; those
        # sessions also have no chat to wake, so do not hold a waiter for them.
        owner_pid = _find_owner_pid(chat)
        if not owner_pid:
            return 0
        if _is_headless_print(_process_args(owner_pid)):
            return 0

        spool = _spool_dir()
        safe = _safe_chat(chat) or "session"
        waiter_path = os.path.join(spool, f"{safe}.waiter")
        cursor_path = os.path.join(spool, f"{safe}.cursor")
        pid = os.getpid()
        _write_waiter(waiter_path, pid)

        cursor = _read_cursor(cursor_path)
        if cursor is None:
            cursor = time.time()
            _write_cursor(cursor_path, cursor)

        deadline = time.time() + MAX_SECONDS
        next_owner_check = time.time() + OWNER_CHECK_SECONDS
        while time.time() < deadline:
            if time.time() >= next_owner_check:
                if not _pid_alive(owner_pid):
                    return 0
                next_owner_check = time.time() + OWNER_CHECK_SECONDS
            if not _owns_waiter(waiter_path, pid):
                return 0
            new = _collect_new(chat, cursor)
            if new:
                time.sleep(COALESCE_SECONDS)
                if not _owns_waiter(waiter_path, pid):
                    return 0
                new = _collect_new(chat, cursor)
                if new:
                    max_ts = max(ts for ts, _ in new)
                    _write_cursor(cursor_path, max_ts)
                    return _emit(new)
            time.sleep(INTERVAL)
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
