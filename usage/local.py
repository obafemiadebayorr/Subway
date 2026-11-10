"""Usage package: local submodule (split from usage.py)."""

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


def chat_tokens_used(session_id: Optional[str] = None) -> Optional[int]:
    if session_id:
        matches = glob.glob(os.path.join(_s.CLAUDE_DIR, "projects", "*", f"{session_id}.jsonl"))
        if matches:
            total = 0
            for ts, inp, out, cache_read in _s._parse_claude_usage(matches[0]):
                total += inp + out
            return total
        copilot_tokens = _s._copilot_tokens_for_session(session_id)
        if copilot_tokens is not None:
            return copilot_tokens
        return None
    ident = _s.detect_self()
    if not ident:
        return None
    source = ident.get("source")
    cid = ident.get("id")
    if source == "claude":
        if not cid:
            return None
        matches = glob.glob(os.path.join(_s.CLAUDE_DIR, "projects", "*", f"{cid}.jsonl"))
        if not matches:
            return None
        total = 0
        for ts, inp, out, cache_read in _s._parse_claude_usage(matches[0]):
            total += inp + out
        return total
    if source == "copilot":
        if not cid or ident.get("synthetic"):
            return None
        return _s._copilot_tokens_for_session(str(cid))
    if source in ("antigravity", "gemini", "agy"):
        if not cid or ident.get("synthetic") or cid.startswith("agy-"):
            return None
        candidates = [
            os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{cid}/.system_generated/logs/transcript.jsonl"),
            os.path.expanduser(f"~/.gemini/antigravity/brain/{cid}/.system_generated/logs/transcript.jsonl"),
            os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{cid}/transcript.jsonl"),
            os.path.expanduser(f"~/.gemini/antigravity/brain/{cid}/transcript.jsonl"),
        ]
        matches = [p for p in candidates if os.path.exists(p)]
        if not matches:
            matches = glob.glob(os.path.expanduser(f"~/.gemini/**/{cid}/**/transcript*.jsonl"), recursive=True)
        if not matches:
            return None
        t_file = max(matches, key=os.path.getmtime)
        total = 0
        try:
            with open(t_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ent = json.loads(line)
                        total += int(ent.get("input_tokens") or 0) + int(ent.get("output_tokens") or 0)
                    except Exception:
                        continue
            return total
        except Exception:
            return None
    if source in ("opencode", "opencode2"):
        db_path = os.path.expanduser("~/.local/share/opencode/opencode.db")
        if not os.path.exists(db_path):
            return None
        try:
            import sqlite3
            cwd = os.path.realpath(os.getcwd())
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cols = [c[1] for c in cur.execute("PRAGMA table_info(session_v2)").fetchall()]
            dir_col = next((c for c in ("directory", "path", "cwd", "project_dir", "project_path") if c in cols), None)
            time_col = next((c for c in ("updated_at", "created_at", "time", "timestamp") if c in cols), "rowid")
            query = "SELECT * FROM session_v2"
            params = []
            if dir_col:
                query += f" WHERE {dir_col} = ? OR {dir_col} = ?"
                params = [cwd, os.getcwd()]
            query += f" ORDER BY {time_col} DESC LIMIT 1"
            row = cur.execute(query, params).fetchone()
            if not row and dir_col:
                row = cur.execute(f"SELECT * FROM session_v2 ORDER BY {time_col} DESC LIMIT 1").fetchone()
            if not row:
                return None
            rdict = dict(zip(cols, row))
            total = 0
            if "tokens" in rdict and isinstance(rdict["tokens"], (int, float)):
                total = int(rdict["tokens"])
            elif "total_tokens" in rdict and isinstance(rdict["total_tokens"], (int, float)):
                total = int(rdict["total_tokens"])
            else:
                for k, v in rdict.items():
                    if ("token" in k or "usage" in k) and isinstance(v, (int, float)):
                        total += int(v)
                    elif ("token" in k or "usage" in k) and isinstance(v, str):
                        try:
                            p = json.loads(v)
                            if isinstance(p, dict):
                                total += int(p.get("input", 0) or p.get("input_tokens", 0) or p.get("total", 0)) + int(p.get("output", 0) or p.get("output_tokens", 0))
                        except Exception:
                            pass
            return total if total > 0 else None
        except Exception:
            return None
    return None


def _provider_5h_tokens(provider: str) -> int:
    prov = (provider or "").lower().strip()
    norm = {"agy": "antigravity", "gemini": "antigravity", "opencode2": "opencode", "claude-code": "claude"}.get(prov, prov)
    try:
        data = None
        if norm == "claude":
            data = _s._fetch_claude_tokens()
        elif norm == "antigravity":
            data = _s._fetch_agy_tokens()
        elif norm == "opencode":
            data = _s._fetch_opencode_usage()
        elif norm == "copilot":
            data = _s._copilot_cli_usage()
        if data and isinstance(data, dict):
            h5 = data.get("5h") or {}
            if isinstance(h5, dict):
                inp = int(h5.get("input") or 0)
                out = int(h5.get("output") or 0)
                return inp + out
    except Exception:
        pass
    total = 0
    try:
        cutoff = time.time() - 5 * 3600
        for w in _s._all_workers():
            w_prov = (w.get("provider") or "").lower().strip()
            if w_prov in (prov, norm):
                c_ts = 0
                if w.get("created_at"):
                    try:
                        c_ts = _dt.datetime.fromisoformat(w["created_at"]).timestamp()
                    except Exception:
                        pass
                if not c_ts or c_ts >= cutoff:
                    u = w.get("usage") or {}
                    total += int(u.get("input") or 0) + int(u.get("output") or 0)
    except Exception:
        pass
    return total


def _run_source_task(name: str) -> Dict[str, Any]:
    fn, _ttl = _s._SOURCES[name]
    t0 = time.time()
    try:
        data, err = fn(), None
    except Exception as e:
        data, err = None, _s.one_line(str(e), 160)
    now = time.time()
    with _s._usage_lock:
        rec = dict(_s._usage.get(name) or {})
        rec.update({
            "checked_at": now,
            "latency_ms": int((now - t0) * 1000),
            "error": err,
            "loading": False,
            "stale": False,
        })
        rec.pop("age", None)
        if data is not None:
            rec.update({"data": data, "fetched_at": now})
        _s._usage[name] = rec

    if data is not None:
        with _s._state_lock("usage"):
            shared = _s._load_json("usage_cache.json")
            shared[name] = {"data": data, "fetched_at": now}
            _s._save_json("usage_cache.json", shared)

    with _s._source_in_flight_lock:
        entry = _s._source_in_flight.get(name)
        if entry and entry[0].done():
            _s._source_in_flight.pop(name, None)

    with _s._hud_cache_lock:
        for entry in _s._hud_cache.values():
            entry["stale"] = True
            if "data" in entry and isinstance(entry["data"], dict) and "quotas" in entry["data"]:
                lq = getattr(_s, "load_quotas", None)
                if lq is not None:
                    entry["data"]["quotas"] = lq(refresh=False)

    return rec


def _fallback_source_record(name: str) -> Dict[str, Any]:
    now = time.time()
    saved = None
    with _s._state_lock("usage"):
        shared = _s._load_json("usage_cache.json")
        saved = shared.get(name)

    if saved and saved.get("data") is not None:
        fetched_at = saved.get("fetched_at", now)
        age = max(0.0, round(now - fetched_at, 1))
        rec = {
            "data": saved["data"],
            "fetched_at": fetched_at,
            "stale": True,
            "age": age,
            "loading": False,
            "checked_at": now,
            "latency_ms": None,
            "error": None,
        }
    else:
        rec = {
            "data": None,
            "fetched_at": None,
            "stale": False,
            "loading": True,
            "checked_at": now,
            "latency_ms": None,
            "error": None,
        }
    with _s._usage_lock:
        cur = _s._usage.get(name)
        if not cur or cur.get("loading") or not cur.get("data"):
            _s._usage[name] = rec
    return rec


def _refresh_source(name: str, deadline: Optional[float] = None) -> Dict[str, Any]:
    if name not in _s._SOURCES:
        return {}
    if deadline is None:
        deadline = _s.SOURCE_DEADLINE

    cur_fn = _s._SOURCES[name][0]
    with _s._source_in_flight_lock:
        entry = _s._source_in_flight.get(name)
        if entry is not None:
            fut, fn = entry
            if fut.done() or fn != cur_fn:
                _s._source_in_flight.pop(name, None)
                entry = None
        if entry is None:
            fut = _s._source_pool.submit(_s._run_source_task, name)
            _s._source_in_flight[name] = (fut, cur_fn)
        else:
            fut, _ = entry

    if deadline <= 0:
        with _s._usage_lock:
            rec = _s._usage.get(name)
        return rec or _s._fallback_source_record(name)

    try:
        return fut.result(timeout=deadline)
    except (concurrent.futures.TimeoutError, TimeoutError):
        return _s._fallback_source_record(name)


def _source(name: str, max_age: Optional[float] = None) -> Dict[str, Any]:
    """A source's latest record, refreshed if older than max_age. None never refreshes."""
    with _s._usage_lock:
        rec = _s._usage.get(name)
    if not rec or not rec.get("data"):
        saved = _s._load_json("usage_cache.json").get(name)
        if saved:
            with _s._usage_lock:
                rec = dict(saved, **(_s._usage.get(name) or {}))
                _s._usage[name] = rec
    if max_age is not None and (not rec or rec.get("loading") or rec.get("stale") or time.time() - rec.get("checked_at", 0) > max_age):
        rec = _s._refresh_source(name)
    return rec or {}


def _sampler_loop() -> None:
    """HUD background poller. Each source runs at its own pace, and /usage only while someone's watching."""
    last_slow = 0.0
    while True:
        now = time.time()
        with contextlib.suppress(Exception):
            _s.enforce_budgets()
        for name, (_fn, ttl) in _s._SOURCES.items():
            if name == "claude_live" and now - _s._viewer_seen > 30:
                continue
            rec = _s._usage.get(name) or {}
            if now - rec.get("checked_at", 0) >= ttl:
                _s._refresh_source(name, deadline=0.0)
        if now - last_slow >= 10:  # Neither needs checking every second.
            last_slow = now
            with contextlib.suppress(Exception):
                _s.start_approved_workers()
            with contextlib.suppress(Exception):
                _s.refresh_benchmarks()
            with contextlib.suppress(Exception):
                _s.sync_caffeinate()
        time.sleep(1.0)


def load_quotas(refresh: bool = True) -> Dict[str, Any]:
    """Usage across every detected client, from each tool's own source."""
    providers = _s.discover_connected_providers(refresh)
    return {"providers": providers,
            "sources": {k: dict(v) for k, v in _s._usage.items()},
            "updated_at": _dt.datetime.now().astimezone().isoformat()}
