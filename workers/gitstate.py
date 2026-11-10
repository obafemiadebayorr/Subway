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

def _git_change_fingerprint(project_dir: str) -> Optional[str]:
    """Fingerprint tracked and untracked git changes relative to the worker's start state."""
    status = subprocess.run(
        ["git", "status", "--porcelain=1", "-z", "--untracked-files=all"],
        cwd=project_dir, capture_output=True,
    )
    if status.returncode:
        if b"not a git repository" in status.stderr.lower():
            return None
        raise subprocess.CalledProcessError(status.returncode, status.args, status.stdout, status.stderr)

    digest = hashlib.sha256(status.stdout)
    for args in (["git", "diff", "--binary", "--"], ["git", "diff", "--cached", "--binary", "--"]):
        diff = subprocess.run(args, cwd=project_dir, capture_output=True, check=True)
        digest.update(diff.stdout)
    for entry in status.stdout.split(b"\0"):
        if not entry.startswith(b"?? "):
            continue
        path = os.path.join(project_dir, os.fsdecode(entry[3:]))
        if os.path.islink(path):
            digest.update(os.fsencode(os.readlink(path)))
        elif os.path.isfile(path):
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    digest.update(chunk)
    return digest.hexdigest()

def _git_status_entries(status: bytes) -> List[Tuple[str, str]]:
    entries = []
    records = status.split(b"\0")
    i = 0
    while i < len(records):
        record = records[i]
        i += 1
        if len(record) < 4 or record[2:3] != b" ":
            continue
        code = os.fsdecode(record[:2])
        entries.append((code, os.fsdecode(record[3:])))
        if "R" in code or "C" in code:
            if i < len(records) and records[i]:
                entries.append((code, os.fsdecode(records[i])))
                i += 1
    return entries

def _git_dirty_paths(project_dir: str) -> Optional[List[str]]:
    root = subprocess.run(
        ["git", "-C", project_dir, "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if root.returncode:
        if "not a git repository" in root.stderr.lower():
            return None
        raise subprocess.CalledProcessError(root.returncode, root.args, root.stdout, root.stderr)
    status = subprocess.run(
        ["git", "-C", root.stdout.strip(), "status", "--porcelain=1", "-z", "--untracked-files=all"],
        capture_output=True,
    )
    if status.returncode:
        if b"not a git repository" in status.stderr.lower():
            return None
        raise subprocess.CalledProcessError(status.returncode, status.args, status.stdout, status.stderr)
    return [path for _, path in _s._git_status_entries(status.stdout)][:500]

