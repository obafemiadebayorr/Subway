"""Usage package: shared submodule (split from usage.py)."""

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
sys.modules.setdefault("server", sys.modules[__name__])  # one module object when run as a script
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


def _parse_reset_seconds(text: str) -> float:
    m = re.search(r'Resets in\s+([0-9a-zA-Z\s]+)', text, re.IGNORECASE)
    if m:
        dur_str = m.group(1)
        days = re.search(r'(\d+)\s*d', dur_str)
        hours = re.search(r'(\d+)\s*h', dur_str)
        mins = re.search(r'(\d+)\s*m', dur_str)
        secs = re.search(r'(\d+)\s*s', dur_str)
        total = (int(days.group(1)) * 86400 if days else 0) + \
                (int(hours.group(1)) * 3600 if hours else 0) + \
                (int(mins.group(1)) * 60 if mins else 0) + \
                (int(secs.group(1)) if secs else 0)
        if total > 0:
            return float(total)
    return 3600.0


def _add_usage(meta: Dict[str, Any], u: Dict[str, Any]) -> None:
    usage = meta.setdefault("usage", {"input": 0, "output": 0, "cache_read": 0})
    usage["input"] += int(u.get("input_tokens") or 0)
    usage["output"] += int(u.get("output_tokens") or 0) + int(u.get("thinking_tokens") or 0)
    usage["cache_read"] += int(u.get("cache_read_tokens") or 0)
    if meta.get("provider") == "copilot":
        meta["input_excludes_cache"] = True
    if "credits" in u and u["credits"] is not None:
        usage["credits"] = round(float(usage.get("credits", 0.0)) + float(u["credits"]), 5)
    if "requests" in u and u["requests"] is not None:
        usage["requests"] = int(usage.get("requests", 0)) + int(u["requests"])


def _resume_reuses_cache(meta: Dict[str, Any]) -> bool:
    """Whether resuming this worker's conversation re-reads its context from the provider's cache.
    Claude's --resume does. A resumed Antigravity process re-sends the whole conversation uncached
    (measured at about half of everything spent so far), so a wrap-up turn there would overshoot
    the cap and save nothing: its files are committed when it stops anyway."""
    return str((meta or {}).get("provider") or "").lower() == "claude"


def _check_cap(run: Dict[str, Any]) -> None:
    """Tell a worker to wrap up at two thirds of its cap, and never start a call that would pass the cap."""
    cap, spent = run.get("cap"), run.get("spent", 0)
    if not cap:
        return
    if spent >= cap:
        run["on_cap"](f"Stopped at its token cap of {cap:,}.")
    elif run.get("soft") and spent >= run["soft"]:
        run["on_cap"](f"Told to wrap up at {spent:,} of its {cap:,} token budget.")
    elif spent + run.get("last_step", 0) + run.get("max_thinking", 0) >= cap:  # Every call resends the whole context; thinking is not resent but can repeat.
        run["on_cap"](f"Stopped at {spent:,} of its {cap:,} token budget: the next call would pass it.")


def _quota_error_event_text(event: Any) -> Optional[str]:
    """Extract an explicit provider error payload without inspecting assistant messages."""
    if not isinstance(event, dict):
        return None
    kind = str(event.get("type") or event.get("event") or "").lower()
    if "assistant" in kind or kind in ("message", "text"):
        return None
    error = event.get("error")
    if error is not None:
        return json.dumps(error, ensure_ascii=False) if isinstance(error, (dict, list)) else str(error)
    if kind in ("error", "error_event", "provider_error"):
        return json.dumps(event, ensure_ascii=False)
    return None


def check_quota_error(provider: str, text: str, source: str = "stdout") -> Optional[str]:
    """Match provider error shapes only; assistant response text is never a quota signal."""
    if not text or source == "assistant":
        return None
    patterns = _s.QUOTA_ERROR_PATTERNS.get(provider, [])
    for line in text.splitlines() or [text]:
        stripped = line.strip()
        if not stripped:
            continue
        is_error_shape = (
            source in ("stderr", "error_event")
            or re.match(r"^(?:error\b|error:)", stripped, re.IGNORECASE) is not None
            or re.search(r"\bHTTP\s*429\b", stripped, re.IGNORECASE) is not None
        )
        for pat in patterns:
            match = re.search(pat, stripped, re.IGNORECASE)
            if match:
                stdout_prefix = re.fullmatch(
                    r"(?:\s*(?:\[(?:error|warning)\]\s*|(?:api\s+)?error\b\s*:?\s*|[!✗]\s*|"
                    r"(?:claude(?:\s+ai)?|copilot|antigravity|opencode)\b\s*[:,\-]?\s*))*",
                    stripped[:match.start()],
                    re.IGNORECASE,
                )
                if is_error_shape or (
                    source == "stdout" and len(stripped) <= 300 and stdout_prefix is not None
                ):
                    return match.group(0)
        if source == "error_event":
            match = re.search(r"\b(?:status|code)[\"']?\s*[:=]\s*[\"']?429\b", stripped, re.IGNORECASE)
            if match:
                return "429"
        if is_error_shape:
            match = re.search(r"\bHTTP\s*429\b", stripped, re.IGNORECASE)
            if match:
                return match.group(0)
    return None


def _parse_reset_text(text: str, now: Optional[float] = None) -> Optional[float]:
    """'resets Nov 1 at 9:10pm (Africa/Lagos)' as a timestamp."""
    m = _s._RESET_RE.search(text or "")
    if not m:
        return None
    month, day, hour, minute, ampm, zone = m.groups()
    hour_i = int(hour) % 12 + (12 if ampm.lower() == "pm" else 0)
    tz: Any = None
    with contextlib.suppress(Exception):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(zone) if zone else None
    base = _dt.datetime.fromtimestamp(now if now is not None else time.time(), tz=tz or _dt.timezone.utc)
    try:
        when = base.replace(hour=hour_i, minute=int(minute or 0), second=0, microsecond=0)
        if month and day:
            mon = _dt.datetime.strptime(month, "%b").month
            when = when.replace(month=mon, day=int(day))
            if (base - when).days > 300:
                when = when.replace(year=when.year + 1)
        elif when < base:
            when += _dt.timedelta(days=1)
        return when.timestamp()
    except ValueError:
        return None


def _select_pool(provider: Dict[str, Any], model: str) -> Optional[Dict[str, Any]]:
    pools = provider.get("pools") or []
    if not pools:
        return None
    if provider.get("id") == "antigravity":
        m = (model or "").lower()
        if "argon" in m:
            wanted = "argon"
        elif "gpt-oss" in m or any(k in m for k in ("claude", "opus", "sonnet", "gpt")):
            wanted = "claude"
        else:
            wanted = "gemini"
        for p in pools:
            name = (p.get("name") or "").lower()
            if wanted == "claude":
                if any(k in name for k in ("claude", "gpt")):
                    return p
            elif wanted == "argon":
                if "argon" in name:
                    return p
            elif wanted == "gemini":
                if "gemini" in name and "argon" not in name:
                    return p
        for p in pools:
            name = (p.get("name") or "").lower()
            if wanted in name and (wanted != "gemini" or "argon" not in name):
                return p
    return pools[0]


def low_limit_notice() -> str:
    """Ping once when any agent reaches its threshold, then stay quiet until that limit window resets."""
    now = time.time()
    if now - _s._limit_checked < 60:
        return ""
    _s._limit_checked = now
    threshold = _s._load_json("config.json").get("shed_at_pct", 20)
    notices = []
    for p in _s.discover_connected_providers(refresh=False):
        for pool in p.get("pools") or []:
            for w in pool["windows"]:
                if w.get("disabled") or w.get("left_pct") is None or w["left_pct"] > threshold:
                    continue
                if w.get("resets_at") and w["resets_at"] <= now:
                    continue
                level = 10 if w["left_pct"] <= 10 else threshold  # A second ping at 10%.
                reset = int(w.get("resets_at") or 0)
                key = f"{p['id']}|{pool['name']}|{w['label']}|{reset}|{level}"
                text = (f"{p['name']} ({pool['name']}): {w['left_pct']}% of its {w['label']} limit left, "
                        f"resets in {_s._fmt_reset(w.get('resets_at'))}")
                notices.append((key, text))
    notes = []
    for key, text in notices:
        if _s.notify_desktop_event(key, "Subway Context limit warning", text, route="usage"):
            notes.append(text)
    return "[LOW LIMIT] " + "; ".join(notes) + ". Plan around it, and shed load if it's yours." if notes else ""


def _fmt_reset(resets_at: Any) -> str:
    if isinstance(resets_at, str):
        resets_at = _s._parse_ts(resets_at)
    if not resets_at:
        return "unknown"
    secs = int(resets_at - time.time())
    if secs <= 0:
        return "now"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    return f"{days}d {hours}h" if days else f"{hours}h {rem // 60}m"
