"""Usage package: copilot submodule (split from usage.py)."""

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


def _copilot_tokens_for_session(session_id: str) -> Optional[int]:
    if not session_id or os.path.basename(session_id) != session_id or "/" in session_id or "\\" in session_id:
        return None
    direct = os.path.join(_s.COPILOT_DIR, "session-state", session_id, "events.jsonl")
    paths = [direct] if os.path.isfile(direct) else []
    if not paths:
        paths = glob.glob(os.path.join(_s.COPILOT_DIR, "session-state", "*", "events.jsonl"))
    for path in paths:
        if os.path.basename(os.path.dirname(path)) != session_id and path != direct:
            continue
        usage = _s._copilot_session_usage(path)
        if usage:
            return int(usage.get("total") or 0)
    for path, _project, _surface in _s._vscode_chat_files():
        try:
            meta = _s._vscode_chat_meta(path, _s._vscode_workspace_project(os.path.dirname(os.path.dirname(path))), _surface, {})
        except OSError:
            continue
        if meta and meta["id"] == session_id:
            return int(meta.get("tokens") or 0)
    return None


def _copilot_fresh_input(input_tokens: Any, cache_read_tokens: Any) -> int:
    return max(0, int(input_tokens or 0) - int(cache_read_tokens or 0))


def _normalise_copilot_worker_record(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Return a display-only normalized copy of legacy Copilot usage."""
    usage = meta.get("usage")
    if (str(meta.get("provider") or "").lower() != "copilot"
            or meta.get("input_excludes_cache") is True
            or not isinstance(usage, dict)):
        return meta
    normalized = dict(meta)
    normalized_usage = dict(usage)
    normalized_usage["input"] = _s._copilot_fresh_input(
        normalized_usage.get("input"), normalized_usage.get("cache_read"))
    normalized["usage"] = normalized_usage
    normalized["input_excludes_cache"] = True
    if "cache_write" not in usage or usage.get("cache_write") is None:
        normalized["estimate"] = True
        normalized["usage_estimate"] = True
        normalized_usage["estimate"] = True
    return normalized


@_s.ttl_cached(120.0)
def _copilot_cli_usage(base_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    session_dir = base_dir if base_dir is not None else os.path.join(_s.COPILOT_DIR, "session-state")
    if not os.path.isdir(session_dir):
        return None
    now = time.time()
    events: List[Tuple[float, int, int, int]] = []
    credit_events: List[Tuple[float, float]] = []
    per_model: Dict[str, int] = {}
    file_paths: List[str] = []
    try:
        for entry in os.scandir(session_dir):
            if entry.is_dir(follow_symlinks=False):
                p = os.path.join(entry.path, "events.jsonl")
                if os.path.isfile(p):
                    file_paths.append(p)
    except OSError:
        pass

    for p in file_paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_mtime < now - _s.SEVEN_D:
            continue
        cps: List[Dict[str, Any]] = []
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "session.usage_checkpoint" in line:
                        try:
                            ev = json.loads(line)
                            if ev.get("type") == "session.usage_checkpoint":
                                cps.append(ev)
                        except Exception:
                            pass
        except OSError:
            continue
        if not cps:
            continue

        def _get_ts(cp: Dict[str, Any]) -> float:
            v = _s._parse_ts(cp.get("timestamp"))
            return v if v is not None else 0.0

        cps.sort(key=_get_ts)
        prev_fresh_inp, prev_out, prev_cache, prev_aiu = 0, 0, 0, 0.0
        prev_model_metrics: Dict[str, int] = {}
        for cp in cps:
            ts = _get_ts(cp)
            data = cp.get("data") or {}
            snap = data.get("accountingSnapshot") or {}
            td = snap.get("tokenDetails") or {}
            inp = int((td.get("input") or {}).get("tokenCount") or 0)
            out = int((td.get("output") or {}).get("tokenCount") or 0)
            cache = int((td.get("cache_read") or {}).get("tokenCount") or 0)
            write = int((td.get("cache_write") or td.get("cache_creation") or {}).get("tokenCount") or 0)
            nano_aiu = float(data.get("totalNanoAiu") or 0.0)

            fresh_inp = inp + write  # tokenDetails.input excludes cache reads and writes
            delta_inp = max(0, fresh_inp - prev_fresh_inp)
            delta_out = max(0, out - prev_out)
            delta_cache = max(0, cache - prev_cache)
            delta_aiu = max(0.0, nano_aiu - prev_aiu)
            prev_fresh_inp, prev_out, prev_cache, prev_aiu = fresh_inp, out, cache, nano_aiu

            events.append((ts, delta_inp, delta_out, delta_cache))
            credit_events.append((ts, delta_aiu / 1e9))

            for m_name, m_info in (snap.get("modelMetrics") or {}).items():
                m_usage = (m_info.get("usage") if isinstance(m_info, dict) else {}) or {}
                m_inp = int(m_usage.get("inputTokens") or 0)
                m_out = int(m_usage.get("outputTokens") or 0)
                m_cache = int(m_usage.get("cacheReadTokens") or 0)
                m_used = _s._copilot_fresh_input(m_inp, m_cache) + m_out
                prev_m = prev_model_metrics.get(m_name, 0)
                delta_m = max(0, m_used - prev_m)
                prev_model_metrics[m_name] = m_used
                if ts >= now - _s.SEVEN_D:
                    per_model[m_name] = per_model.get(m_name, 0) + delta_m

    res = _s._window_totals(events, now)
    credits = {"5h": 0.0, "7d": 0.0}
    for ts, cred in credit_events:
        if ts >= now - _s.FIVE_H:
            credits["5h"] += cred
        if ts >= now - _s.SEVEN_D:
            credits["7d"] += cred
    credits["5h"] = round(credits["5h"], 4)
    credits["7d"] = round(credits["7d"], 4)
    res["credits"] = credits
    res["per_model"] = per_model
    return res
