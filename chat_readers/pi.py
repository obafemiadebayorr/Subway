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
def _pi_sessions_root() -> str:
    return os.path.join(_s.PI_DIR, "sessions")


def _pi_session_roots() -> List[str]:
    """Candidate pi session roots: env override, default PI_DIR, and DeepAI userdata globs."""
    roots: List[str] = []
    override = os.environ.get("SUBWAY_PI_SESSIONS_DIR", "").strip()
    if override:
        roots.append(os.path.expanduser(override))
    pi_dir = getattr(_s, "PI_DIR", os.path.join(getattr(_s, "HOME", os.path.expanduser("~")), ".pi", "agent"))
    roots.append(os.path.join(pi_dir, "sessions"))
    home = getattr(_s, "HOME", os.path.expanduser("~"))
    # DeepAI Code stores sessions under ~/.deepai-code/userdata/*/pi/*/sessions
    # (glob app segments; do not hardcode a specific app name).
    roots.extend(glob.glob(os.path.join(home, ".deepai-code", "userdata", "*", "pi", "*", "sessions")))
    seen: set = set()
    out: List[str] = []
    for root in roots:
        try:
            key = os.path.realpath(root)
        except OSError:
            key = os.path.normpath(root)
        if key in seen or not os.path.isdir(root):
            continue
        seen.add(key)
        out.append(root)
    return out


def pi_files(include_subagents: bool = False) -> List[str]:
    """Session transcript files from pi/DeepAI session roots (JSONL)."""
    files: List[str] = []
    for root in _pi_session_roots():
        files.extend(p for p in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
                     if os.path.isfile(p))
    return sorted(set(files))

def _pi_entries(path: str) -> List[Dict[str, Any]]:
    """Parse a pi transcript file into entry dicts; corrupt lines are skipped."""
    entries: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return entries
    if not raw.strip():
        return entries
    # Single-JSON documents (a list of entries, or one entry object).
    stripped = raw.lstrip()
    if stripped[:1] == "[":
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [e for e in data if isinstance(e, dict)]
        except ValueError:
            pass
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
        elif isinstance(entry, list):
            entries.extend(e for e in entry if isinstance(e, dict))
    return entries


def _pi_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                if block.strip():
                    parts.append(block.strip())
            elif isinstance(block, dict) and block.get("type") in ("text", None):
                text = block.get("text") or block.get("content") or ""
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    if isinstance(content, dict):
        text = content.get("text") or content.get("content") or ""
        return text.strip() if isinstance(text, str) else ""
    return ""


def _pi_turns_from_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    turns: List[Dict[str, Any]] = []
    for entry in entries:
        typ = str(entry.get("type") or "")
        if typ in ("session", "model_change", "thinking_level_change", "label", "custom"):
            continue
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else entry
        role = str(msg.get("role") or entry.get("role") or typ or "").lower()
        if role not in ("user", "assistant", "toolResult", "tool_result", "tool"):
            # Compaction/summary entries surface as summary turns.
            if typ in ("compaction", "summary") or "compact" in typ:
                text = _pi_content_text(entry.get("summary") or entry.get("content") or msg.get("content"))
                if text:
                    turns.append({"role": "summary", "text": text, "ts": entry.get("timestamp")})
            continue
        ts = entry.get("timestamp") or msg.get("timestamp")
        content = msg.get("content", entry.get("content"))
        if role == "user":
            text = _pi_content_text(content)
            if text:
                turns.append({"role": "user", "text": text, "ts": ts})
        elif role == "assistant":
            blocks = content if isinstance(content, list) else []
            text_parts = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "thinking":
                    thought = str(block.get("thinking") or block.get("text") or "").strip()
                    if thought:
                        turns.append({"role": "assistant", "text": "", "thought": thought, "ts": ts})
                elif btype == "toolCall":
                    name = str(block.get("name") or "tool")
                    args = block.get("arguments") if block.get("arguments") is not None else block.get("args", block.get("input"))
                    turns.append({"role": "assistant", "text": f"[tool {name}]", "tool": f"[tool {name}]",
                                  "tool_name": name, "tool_input": args, "ts": ts})
                elif btype in ("text", None):
                    text = str(block.get("text") or "").strip()
                    if text:
                        text_parts.append(text)
            text = "\n".join(text_parts).strip() or _pi_content_text(content) if not blocks else "\n".join(text_parts).strip()
            if text:
                turns.append({"role": "assistant", "text": text, "ts": ts})
        else:  # toolResult / tool_result / tool
            name = str(msg.get("toolName") or msg.get("name") or entry.get("toolName") or "tool")
            result = _pi_content_text(content)
            line = f"[tool {name}] {result}".strip() if result else f"[tool {name}]"
            turns.append({"role": "assistant", "text": line, "tool": f"[tool {name}]",
                          "tool_name": name, "ts": ts})
    return turns


def pi_turns(path: str) -> List[Dict[str, Any]]:
    """Extract user prompts, agent turns, tool calls and decisions from a pi file."""
    if not path or not os.path.isfile(path):
        return []
    return _pi_turns_from_entries(_pi_entries(path))


def pi_read(path: str, mode: str = "full", n: int = 0) -> List[Dict[str, Any]]:
    return pi_turns(path)


def pi_read_segments(chat: Dict[str, Any], mode: str = "full") -> Tuple[List[List[Dict[str, Any]]], bool]:
    """Split pi turns into segments at compaction entries."""
    path = str(chat.get("path") or "")
    segs: List[List[Dict[str, Any]]] = [[]]
    has_markers = False
    if not path or not os.path.isfile(path):
        return segs, has_markers
    for entry in _pi_entries(path):
        typ = str(entry.get("type") or "")
        if typ in ("compaction",) or (typ == "message" and isinstance(entry.get("message"), dict)
                                      and entry["message"].get("role") == "compaction"):
            has_markers = True
            summary = _pi_content_text(entry.get("summary") or (entry.get("message") or {}).get("content"))
            if summary:
                segs[-1].append({"role": "summary", "text": summary, "ts": entry.get("timestamp")})
            segs.append([])
            continue
        segs[-1].extend(_pi_turns_from_entries([entry]))
    return (segs if segs else [[]]), has_markers


def pi_meta(path: str) -> Dict[str, Any]:
    st = os.stat(path)
    entries = _pi_entries(path)
    sid = ""
    project = ""
    title = ""
    model = ""
    tokens = 0
    updated: Any = st.st_mtime
    first_prompt = ""
    last_prompt = ""
    is_sub = bool(re.search(r"(?:^|/)subagents?/", path.lower()))  # parent/parentId is branching, not a subagent signal
    parent = ""
    for entry in entries:
        if entry.get("type") == "session":
            sid = str(entry.get("id") or sid)
            project = str(entry.get("cwd") or entry.get("project") or project)
            title = str(entry.get("name") or entry.get("title") or title)
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else entry.get("usage")
        if isinstance(usage, dict):
            tokens += int(usage.get("input") or usage.get("input_tokens") or 0)
            tokens += int(usage.get("output") or usage.get("output_tokens") or 0)
        if msg.get("model") or entry.get("model"):
            model = str(msg.get("model") or entry.get("model"))
        ts = _s._timestamp_seconds(entry.get("timestamp") or msg.get("timestamp"))
        if ts is not None:
            updated = max(updated, ts) if isinstance(updated, (int, float)) else ts
        if entry.get("parent") or entry.get("parentId"):
            parent = str(entry.get("parent") or entry.get("parentId"))
    for turn in _pi_turns_from_entries(entries):
        if turn["role"] == "user":
            if not first_prompt:
                first_prompt = turn["text"]
            last_prompt = turn["text"]
    if not sid:
        sid = os.path.basename(path)
        for suffix in (".jsonl", ".json"):
            if sid.endswith(suffix):
                sid = sid[: -len(suffix)]
                break
    if not project:
        encoded = os.path.basename(os.path.dirname(path))
        project = encoded.lstrip("-").replace("-", "/") if encoded and encoded != "sessions" else ""
        if project and not os.path.isabs(project):
            project = "/" + project
    display_title = title or (_s.one_line(first_prompt, 80) if first_prompt else "(untitled)")
    if is_sub and not title:
        display_title = f"[Subagent] {display_title}"
    return {
        "source": "pi", "id": sid, "path": path, "format": "pi-jsonl",
        "title": display_title, "first_prompt": first_prompt, "last_prompt": last_prompt,
        "project": project, "updated": updated, "size": st.st_size,
        "model": model, "usage": {"total": tokens}, "tokens": tokens,
        "open": False, "subagent": bool(is_sub), "parent": parent,
    }


def pi_list(include_subagents: bool = False) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in pi_files(include_subagents):
        try:
            meta = pi_meta(path)
        except Exception as e:
            _s.log("pi meta failed", path, e)
            continue
        if meta.get("subagent") and not include_subagents:
            continue
        out.append(meta)
    return out
