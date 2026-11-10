"""Usage package: claude submodule (split from usage.py)."""

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


def _newest_claude_transcript_tokens() -> Optional[int]:
    try:
        patterns = [
            os.path.join(_s.CLAUDE_DIR, "projects", "*", "*.jsonl"),
            os.path.join(_s.CLAUDE_DIR, "projects", "**", "*.jsonl"),
            os.path.expanduser("~/.claude/projects/*/*.jsonl"),
            os.path.expanduser("~/.claude/projects/**/*.jsonl"),
        ]
        files = []
        for pat in patterns:
            files.extend([p for p in glob.glob(pat, recursive=True) if os.path.isfile(p)])
        if not files:
            return None
        newest = max(set(files), key=os.path.getmtime)
        total = 0
        for ts, inp, out, cache_read in _s._parse_claude_usage(newest):
            total += inp + out
        if total == 0:
            with open(newest, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ent = json.loads(line)
                        u = ent.get("usage") if isinstance(ent.get("usage"), dict) else ent
                        total += int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
                    except Exception:
                        pass
        return total
    except Exception:
        return None


def _parse_usage_text(text: str) -> Dict[str, Any]:
    """Pull the two limit lines out of Claude Code's /usage report."""
    out: Dict[str, Any] = {}
    for key, pat in (("five_hour", r"Current session:\s*([\d.]+)%\s*used([^\n]*)"),
                     ("seven_day", r"Current week \(all models\):\s*([\d.]+)%\s*used([^\n]*)")):
        m = re.search(pat, text or "")
        if m:
            out[key] = {"used_pct": float(m.group(1)), "resets_at": _s._parse_reset_text(m.group(2))}
    return out


def _fetch_claude_live() -> Dict[str, Any]:
    """Ask Claude Code itself. It handles its own login and this spends no tokens or API cost."""
    cb = _s.find_claude_binary()
    if not cb:
        raise RuntimeError("Claude Code CLI not found")
    _s._ensure_subway_dir()
    r = subprocess.run([cb, "-p", "/usage", "--output-format", "json", "--no-session-persistence",
                        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
                       cwd=_s.SUBWAY_DIR, capture_output=True, text=True, timeout=45, stdin=subprocess.DEVNULL)
    body = json.loads(r.stdout or "{}")
    parsed = _s._parse_usage_text(body.get("result") or "")
    if not parsed:
        raise RuntimeError(_s.one_line(str(body.get("result") or r.stderr or "no usage in response"), 120))
    return parsed


def _read_claude_json() -> Dict[str, Any]:
    try:
        with open(_s.CLAUDE_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _fetch_claude_cached() -> Dict[str, Any]:
    """The usage snapshot Claude Code caches, which it may only rewrite every so often."""
    cfg = _s._read_claude_json()
    oauth = cfg.get("oauthAccount") or {}
    cache = cfg.get("cachedUsageUtilization") or {}
    org = str(oauth.get("organizationType") or "")
    plan = {"claude_pro": "Pro", "claude_max": "Max", "claude_team": "Team",
            "claude_enterprise": "Enterprise"}.get(org, org.replace("claude_", "").replace("_", " ").title())
    out: Dict[str, Any] = {"plan": plan, "signed_in": bool(oauth), "fetched_at": None}
    same_account = not cache.get("accountUuid") or cache.get("accountUuid") == oauth.get("accountUuid")
    util = cache.get("utilization") or {}
    if same_account and util:
        out["fetched_at"] = (cache.get("fetchedAtMs") or 0) / 1000.0
        for key in ("five_hour", "seven_day"):
            w = util.get(key)
            if isinstance(w, dict) and w.get("utilization") is not None:
                out[key] = {"used_pct": float(w["utilization"]), "resets_at": _s._parse_ts(w.get("resets_at"))}
    return out


def _window_totals(events: List[Tuple[float, int, int, int]], now: float) -> Dict[str, Dict[str, int]]:
    totals: Dict[str, Any] = {"5h": {"input": 0, "output": 0, "cache_read": 0},
                              "7d": {"input": 0, "output": 0, "cache_read": 0}}
    hourly = [0] * 24  # New tokens per hour, oldest first, ending now.
    for ts, inp, out, cache in events:
        for name, span in (("5h", _s.FIVE_H), ("7d", _s.SEVEN_D)):
            if ts >= now - span:
                totals[name]["input"] += inp
                totals[name]["output"] += out
                totals[name]["cache_read"] += cache
        age = int((now - ts) // 3600)
        if 0 <= age < 24:
            hourly[23 - age] += inp + out
    totals["hourly"] = hourly
    return totals


def _scan_ledger(paths: List[str], parse: Callable[[str], List[Tuple[float, int, int, int]]]) -> Dict[str, Dict[str, int]]:
    """Token windows across transcripts, re-reading only files that changed."""
    now = time.time()
    state = _s._ledger_cache.setdefault(parse, {"last_scan": 0.0, "last_result": None, "files": {}})
    last_scan = state.get("last_scan", 0.0)
    last_result = state.get("last_result")
    files: Dict[str, Tuple[Tuple[float, int], List[Tuple[float, int, int, int]]]] = state["files"]

    current_paths = set(paths)
    any_changed = False

    for p in list(files.keys()):
        if p not in current_paths:
            del files[p]
            any_changed = True

    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            if p in files:
                del files[p]
                any_changed = True
            continue
        if st.st_mtime < now - _s.SEVEN_D:
            if p in files:
                del files[p]
                any_changed = True
            continue
        key = (st.st_mtime, st.st_size)
        cached = files.get(p)
        if cached is not None and cached[0] == key:
            continue
        any_changed = True
        try:
            parsed = parse(p)
        except OSError:
            continue
        files[p] = (key, parsed)
        _s._ledger_files[p] = (key, parsed)

    if not any_changed and last_result is not None and (now - last_scan < 60.0):
        return last_result

    events = [ev for _, file_events in files.values() for ev in file_events]
    result = _s._window_totals(events, now)
    state["last_scan"] = now
    state["last_result"] = result
    return result


def _parse_claude_usage(path: str) -> List[Tuple[float, int, int, int]]:
    """Transcripts only grow, so read from where the last scan stopped. One can be hundreds of MB."""
    offset, by_message = _s._usage_tails.get(path, (0, {}))  # A streamed reply repeats across lines.
    if os.path.getsize(path) < offset:
        offset, by_message = 0, {}
    with open(path, "rb") as f:
        f.seek(offset)
        for raw in f:
            if not raw.endswith(b"\n"):
                break
            offset += len(raw)
            if b'"type":"assistant"' not in raw or b'"usage"' not in raw:
                continue
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            m = o.get("message") or {}
            u = m.get("usage")
            if isinstance(u, dict) and m.get("id"):
                by_message[m["id"]] = (_s.sort_key_ts(o.get("timestamp")),
                                       int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0),
                                       int(u.get("output_tokens") or 0), int(u.get("cache_read_input_tokens") or 0))
    _s._usage_tails[path] = (offset, by_message)
    return list(by_message.values())


def _fetch_claude_tokens() -> Dict[str, Any]:
    return _s._scan_ledger(_s.claude_files(include_subagents=True), _s._parse_claude_usage)
