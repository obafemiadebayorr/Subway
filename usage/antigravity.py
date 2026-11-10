"""Usage package: antigravity submodule (split from usage.py)."""

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


def _parse_ts(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        d = _dt.datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", str(value)).replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)).timestamp()
    except ValueError:
        return None


def _fetch_agy_limits() -> Dict[str, Any]:
    hubs = _s._agy_hubs()
    if not hubs:
        raise RuntimeError("Antigravity is not running")
    last_err: Optional[Exception] = None
    for hub in hubs:
        try:
            summary = _s._agy_rpc(hub, "RetrieveUserQuotaSummary").get("response") or {}
            plan = ""
            with contextlib.suppress(Exception):
                status = _s._agy_rpc(hub, "GetUserStatus").get("userStatus") or {}
                plan = ((status.get("planStatus") or {}).get("planInfo") or {}).get("planName") or ""
            pools = []
            for g in summary.get("groups") or []:
                windows = []
                for b in g.get("buckets") or []:
                    frac = b.get("remainingFraction")
                    left = round(float(frac) * 100, 1) if frac is not None else (0.0 if b.get("resetTime") else None)
                    weekly = b.get("window") == "weekly"
                    windows.append({
                        "key": "weekly" if weekly else "5h",
                        "label": "Weekly" if weekly else "5-hour",
                        "left_pct": left,
                        "resets_at": _s._parse_ts(b.get("resetTime")),
                        "disabled": bool(b.get("disabled")),
                        "note": b.get("description") or "",
                    })
                windows.sort(key=lambda w: w["key"] != "5h")
                pools.append({"key": re.sub(r"\W+", "_", (g.get("displayName") or "pool").lower()),
                              "name": g.get("displayName") or "Pool", "windows": windows})
            if pools:
                return {"plan": plan, "pools": pools}
            last_err = RuntimeError("no quota groups in response")
        except (OSError, ValueError, urllib.error.URLError) as e:
            last_err = e
    raise RuntimeError(f"Antigravity quota call failed: {last_err}")


def _parse_agy_usage(path: str) -> List[Tuple[float, int, int, int]]:
    events = []
    with open(path, "rb") as f:
        for raw in f:
            if b'"input_tokens"' not in raw and b'"output_tokens"' not in raw:
                continue
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            events.append((_s.sort_key_ts(o.get("created_at")), int(o.get("input_tokens") or 0),
                           int(o.get("output_tokens") or 0), int(o.get("cache_read_tokens") or 0)))
    return events


def _fetch_agy_tokens() -> Dict[str, Any]:
    # The IDE and the agy CLI keep separate folders, and workers run under the CLI.
    roots = [_s.AGY_DIR, os.path.join(os.path.dirname(_s.AGY_DIR), "antigravity-cli")]
    return _s._scan_ledger([p for root in roots for p in glob.glob(
        os.path.join(root, "brain", "*", ".system_generated", "logs", "transcript.jsonl"))], _s._parse_agy_usage)
