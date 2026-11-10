"""Usage package: kimi submodule (split from usage.py)."""

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


def _kimi_token() -> Optional[str]:
    """Access token from the freshest ~/.kimi-code/credentials file that hasn't expired."""
    now = time.time()
    candidates = []
    for path in glob.glob(_s._home(".kimi-code", "credentials") + "/*.json"):
        try:
            with open(path) as f:
                expires_at = json.load(f).get("expires_at")
            if not isinstance(expires_at, (int, float)):
                continue
            if expires_at > 1e12:  # Milliseconds on some releases.
                expires_at /= 1000.0
            if expires_at <= now:
                continue
            candidates.append((os.path.getmtime(path), path))
        except Exception:
            continue
    if not candidates:
        return None
    _, path = max(candidates)
    try:
        with open(path) as f:
            return json.load(f).get("access_token")
    except Exception:
        return None


class _KimiUsageError(Exception):
    """Other failure, distinct from an expired sign-in."""


@_s.ttl_cached(120.0)
def _kimi_raw() -> Dict[str, Any]:
    """GET the undocumented Kimi usages endpoint. None when the sign-in is stale (no token or 401)."""
    token = _s._kimi_token()
    if not token:
        return None
    try:
        req = urllib.request.Request(
            "https://api.kimi.com/coding/v1/usages",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=6) as res:
            body = res.read()
            if res.status != 200:
                raise _s._KimiUsageError(f"HTTP {res.status}")
        return json.loads(body or "{}")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return None
        raise _s._KimiUsageError(f"HTTP {e.code}")
