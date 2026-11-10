"""Usage package: opencode submodule (split from usage.py)."""

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


def _opencode_free_exhausted_models(providers: Optional[List[Dict[str, Any]]] = None) -> Dict[str, float]:
    """Map of OpenCode model name -> timestamp when it hit free limit within the last 24 hours."""
    exhausted: Dict[str, float] = {}
    now = time.time()
    rec = None
    if providers:
        rec = next((p for p in providers if p.get("id") == "opencode"), None)
    if not rec:
        with contextlib.suppress(Exception):
            src = _s._source("opencode", 5.0)
            rec = src.get("data")
    if not rec:
        with contextlib.suppress(Exception):
            rec = _s._fetch_opencode_usage()
    if isinstance(rec, dict):
        limited = rec.get("limited") or {}
        for k, v in limited.items():
            if isinstance(v, dict):
                at = float(v.get("at") or 0)
                if at and (now - at <= 86400):
                    m = str(v.get("model") or k)
                    if m:
                        exhausted[m] = at
                        if "/" in m:
                            exhausted[m.split("/", 1)[1]] = at
        hit = rec.get("limit_hit")
        if isinstance(hit, dict):
            at = float(hit.get("at") or 0)
            if at and (now - at <= 86400):
                m = str(hit.get("model") or rec.get("model") or "")
                if m and m != "Free model":
                    exhausted[m] = at
                    if "/" in m:
                        exhausted[m.split("/", 1)[1]] = at
    return exhausted


def _is_opencode_model_exhausted(model: str, providers: Optional[List[Dict[str, Any]]] = None) -> bool:
    if not model:
        return False
    ex = _s._opencode_free_exhausted_models(providers)
    if model in ex:
        return True
    clean = model.split("/", 1)[1] if "/" in model else model
    return clean in ex


def _opencode_session_tokens(session_id: str) -> Dict[str, int]:
    """Read real token spend from OpenCode's SQLite session_v2 table."""
    p = os.path.join(_s.OPENCODE_DIR, "opencode.db")
    if not os.path.exists(p) or not session_id:
        return {}
    for uri in (f"file:{p}?mode=ro", f"file:{p}?immutable=1"):
        try:
            con = sqlite3.connect(uri, uri=True, timeout=1.0)
            try:
                row = con.execute(
                    "select coalesce(tokens_input, 0), coalesce(tokens_output, 0), "
                    "coalesce(tokens_reasoning, 0), coalesce(tokens_cache_read, 0) "
                    "from session_v2 where id = ?",
                    (session_id,),
                ).fetchone()
                if row:
                    return {
                        "input_tokens": int(row[0] or 0),
                        "output_tokens": int(row[1] or 0),
                        "thinking_tokens": int(row[2] or 0),
                        "cache_read_tokens": int(row[3] or 0),
                    }
                return {}
            finally:
                con.close()
        except Exception:
            continue
    return {}


def _fetch_opencode_usage() -> Dict[str, Any]:
    con = _s._opencode_db()
    if con is None:
        raise RuntimeError("OpenCode has no database yet")
    try:
        now_s = time.time()
        normalized_created = (
            "case when time_created > 100000000000 then time_created / 1000.0 else time_created end"
        )
        tokens: Dict[str, Dict[str, int]] = {}
        cost = {}
        for name, span in (("5h", _s.FIVE_H), ("7d", _s.SEVEN_D)):
            row = con.execute(
                "select coalesce(sum(json_extract(data,'$.tokens.input')),0),"
                " coalesce(sum(json_extract(data,'$.tokens.output')),0)+coalesce(sum(json_extract(data,'$.tokens.reasoning')),0),"
                " coalesce(sum(json_extract(data,'$.tokens.cache.read')),0), coalesce(sum(json_extract(data,'$.cost')),0)"
                f" from session_message where type='assistant' and {normalized_created}>=?",
                (now_s - span,)).fetchone()
            tokens[name] = {"input": int(row[0]), "output": int(row[1]), "cache_read": int(row[2])}
            cost[name] = round(float(row[3]), 4)
        hourly = [0] * 24
        for created, used in con.execute(
                "select time_created, coalesce(json_extract(data,'$.tokens.input'),0)+coalesce(json_extract(data,'$.tokens.output'),0)"
                f" from session_message where type='assistant' and {normalized_created}>=?",
                (now_s - 86400,)):
            created = _s._opencode_timestamp(created)
            age = int((now_s - created) // 3600)
            if 0 <= age < 24:
                hourly[23 - age] += int(used)
        tokens["hourly"] = hourly  # type: ignore[assignment]
        per_model: Dict[str, int] = {}
        with contextlib.suppress(Exception):
            for prov, mid, used in con.execute(
                    "select json_extract(data,'$.model.providerID'), json_extract(data,'$.model.id'),"
                    " coalesce(sum(coalesce(json_extract(data,'$.tokens.input'),0)+coalesce(json_extract(data,'$.tokens.output'),0)),0)"
                    f" from session_message where type='assistant' and {normalized_created}>=?"
                    " group by 1, 2",
                    (now_s - _s.SEVEN_D,)):
                if mid:
                    cnt = int(used)
                    key = f"{prov}/{mid}".strip("/") if prov else str(mid)
                    per_model[key] = cnt
                    if str(mid) not in per_model:
                        per_model[str(mid)] = cnt
        model = ""
        with contextlib.suppress(Exception):
            raw = con.execute(
                "select model from session_v2 order by "
                "(case when time_updated > 100000000000 then time_updated / 1000.0 else time_updated end) desc limit 1"
            ).fetchone()
            if raw and raw[0]:
                parsed = json.loads(raw[0])
                model = f"{parsed.get('providerID', '')}/{parsed.get('id', '')}".strip("/")
        limit_hit = None
        limited: Dict[str, Dict[str, Any]] = {}  # Limits apply per model: one free model can be out while another works.
        with contextlib.suppress(Exception):
            # A rate limit isn't recorded as an error: OpenCode keeps retrying, with the details on the message.
            for created, raw_retry, prov, mid in con.execute(
                    "select time_created, json_extract(data,'$.retry'), json_extract(data,'$.model.providerID'), json_extract(data,'$.model.id')"
                    " from session_message where type='assistant'"
                    f" and json_extract(data,'$.retry') is not null and {normalized_created}>=?"
                    f" order by {normalized_created} desc limit 50",
                    (now_s - 86400,)):
                retry = json.loads(raw_retry)
                error = retry.get("error") or {}
                key = f"{prov}/{mid}"
                if prov in _s._LOCAL_OPENCODE_PROVIDERS:
                    continue  # Local models have no free limit; their failures are memory or load problems.
                if "rate-limit" in str(error.get("type")) and key not in limited:
                    until = _s._opencode_timestamp(retry.get("at")) or 0
                    created = _s._opencode_timestamp(created)
                    limited[key] = {"at": created, "retry_at": until, "model": key,
                                    "message": _s.one_line(str(error.get("message", "")), 160), "recent": now_s < until + 3600}
            limit_hit = next((v for v in limited.values() if v["recent"]), None)
        with contextlib.suppress(Exception):
            for created, err, prov, mid in () if limit_hit else con.execute(
                    "select time_created, json_extract(data,'$.error'), json_extract(data,'$.model.providerID'), json_extract(data,'$.model.id')"
                    " from session_message where type='assistant'"
                    f" and json_extract(data,'$.finish')='error' and {normalized_created}>=?"
                    f" order by {normalized_created} desc limit 50",
                    (now_s - 86400,)):
                if prov in _s._LOCAL_OPENCODE_PROVIDERS:
                    continue
                info = json.loads(err) if isinstance(err, str) and err.startswith("{") else {}
                text = f"{info.get('type', '')} {info.get('message', '')}".lower()
                if info.get("status") == 429 or re.search(r"rate.?limit|quota|free.*(limit|usage)|usage limit|too many requests|exceeded", text):
                    key = f"{prov}/{mid}" if prov and mid else ""
                    created = _s._opencode_timestamp(created)
                    hit = {"at": created, "message": _s.one_line(str(info.get("message", "")), 160),
                           "recent": now_s - created < 3600, "model": key}
                    if key and key not in limited:
                        limited[key] = {**hit, "retry_at": 0}
                    limit_hit = limit_hit or hit
        accounts = 0
        with contextlib.suppress(Exception):
            accounts = con.execute("select (select count(*) from account)+(select count(*) from credential)").fetchone()[0]
        return {"tokens": tokens, "cost": cost, "model": model, "signed_in": accounts > 0, "limit_hit": limit_hit, "limited": limited, "per_model": per_model}
    finally:
        con.close()


@_s.ttl_cached(20.0)
def get_exhausted_models() -> List[Dict[str, Any]]:
    bad = _s._load_json("bad_models.json")
    if not isinstance(bad, dict):
        return []
    now = time.time()
    exhausted = []
    for model, entry in bad.items():
        until = None
        if isinstance(entry, dict) and ("reset_time" in entry or "resets_at" in entry):
            rt = entry.get("reset_time") or entry.get("resets_at")
            with contextlib.suppress(TypeError, ValueError):
                until = float(rt)
        elif isinstance(entry, dict) and "timestamp" in entry:
            with contextlib.suppress(TypeError, ValueError):
                until = float(entry["timestamp"]) + 24 * 3600
        elif isinstance(entry, (int, float)):
            until = float(entry) + 24 * 3600
        if until is not None and now < until:
            exhausted.append({"model": model, "until": until})
    return exhausted
