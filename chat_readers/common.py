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
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple, Union

import terminals
import native_terminals
import threads

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

import server as _s
def _opencode_timestamp(value: Any) -> Any:
    """OpenCode stores Unix timestamps in either seconds or milliseconds."""
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value
    if numeric > 1e11:
        numeric /= 1000.0
    if numeric.is_integer():
        return int(numeric)
    return numeric


def extract_files_from_text(text: str) -> Set[str]:
    hits = set()
    for m in _s.FILE_PATH_RE.finditer(text):
        path = m.group(1).strip()
        if not path.startswith("http") and not path.startswith("0.") and "/" in path:
            hits.add(path)
        elif path.endswith((".py", ".ts", ".tsx", ".js", ".html", ".md", ".sql", ".json", ".sh")):
            hits.add(path)
    return hits


def slugify_topic(title: str, max_words: int = 3) -> str:
    """Extract a clean, readable topic slug from a conversation title."""
    if not title:
        return "chat"
    text = re.sub(r"[^\w\s-]", "", title).strip().lower()
    words = [w for w in re.split(r"[\s_-]+", text)
             if w and w not in ("to", "the", "a", "an", "and", "in", "on", "for", "of", "with", "at", "by", "from")]
    if not words:
        return "chat"
    return "-".join(words[:max_words])


def _read_tail(path: str, nbytes: int) -> List[str]:
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > nbytes:
            f.seek(size - nbytes)
            data = f.read()
            lines = data.split(b"\n")[1:]
        else:
            lines = f.read().split(b"\n")
    return [l.decode("utf-8", "replace") for l in lines if l.strip()]


def _read_head(path: str, nbytes: int) -> List[str]:
    with open(path, "rb") as f:
        data = f.read(nbytes)
    lines = data.split(b"\n")
    if len(data) == nbytes:
        lines = lines[:-1]
    return [l.decode("utf-8", "replace") for l in lines if l.strip()]


class SegmentInfo(tuple):
    def __new__(cls, data, seg_label, total_segments, note: Optional[str] = None):
        return super().__new__(cls, (data, seg_label, total_segments))

    def __init__(self, data, seg_label, total_segments, note: Optional[str] = None):
        self.note = note


def chat_segments_info(chat: Dict[str, Any], segment: Union[str, int] = "all") -> Tuple[List[Dict[str, Any]], str, int]:
    source = (chat.get("source") or "").lower()
    note = None

    try:
        if source == "claude":
            is_sub = "/subagents/" in chat.get("path", "")
            segs = _s.claude_read_segments(chat.get("path", ""), is_sub=is_sub)
        elif source == "opencode":
            segs = _s.opencode_read_segments(chat.get("id", ""))
        elif source == "antigravity":
            segs, has_markers = _s.agy_read_segments(chat)
            if not has_markers:
                segs = [segs[0]] if segs else [[]]
                note = "compaction not marked in this transcript"
        elif source == "copilot":
            segs, has_markers = _s.copilot_read_segments(chat)
            if not has_markers:
                segs = [segs[0]] if segs else [[]]
                note = "compaction not marked in this transcript"
        elif source == "pi":
            segs, has_markers = _s.pi_read_segments(chat)
            if not has_markers:
                segs = [segs[0]] if segs else [[]]
                note = "compaction not marked in this transcript"
        elif source == "cursor":
            segs = _s.cursor_read_segments(chat)
            note = "compaction not marked in this transcript"
        elif source == "devin":
            segs = _s.devin_read_segments(chat)
            note = "compaction not marked in this transcript"
        else:
            segs = [[]]
            note = "compaction history not supported for this provider"
    except Exception:
        segs = [[]]
        note = "compaction history not supported for this provider"

    if not segs:
        segs = [[]]

    total = len(segs)
    seg_str = str(segment).strip().lower()

    if seg_str == "all":
        all_turns: List[Dict[str, Any]] = []
        for i, s in enumerate(segs):
            if i > 0:
                all_turns.append({"role": "summary", "text": "--- compaction boundary ---"})
            all_turns.extend(s)
        return _s.SegmentInfo(all_turns, "all", total, note=note)
    elif seg_str == "latest":
        idx = total - 1
        return _s.SegmentInfo(segs[idx], str(total), total, note=note)
    else:
        try:
            k = int(segment)
            k = max(1, min(k, total))
        except (ValueError, TypeError):
            k = total
        idx = k - 1
        return _s.SegmentInfo(segs[idx], str(k), total, note=note)


def chat_turns(chat: Dict[str, Any], segment: Union[str, int] = "all") -> List[Dict[str, Any]]:
    """All turns of a chat, from any provider, in stable order."""
    return _s.chat_segments_info(chat, segment)[0]
