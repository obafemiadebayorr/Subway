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
def claude_open_sessions() -> Dict[str, Dict[str, Any]]:
    res: Dict[str, Dict[str, Any]] = {}
    for p in glob.glob(os.path.join(_s.CLAUDE_DIR, "sessions", "*.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            pid = int(d.get("pid", 0))
            alive = False
            if pid:
                alive = _s._pid_alive(pid)
            if alive and d.get("sessionId"):
                d["_pid"] = pid
                res[d["sessionId"]] = d
        except Exception:
            continue
    return res


def _claude_is_human(o: Dict[str, Any], is_subagent: bool = False) -> bool:
    if o.get("isMeta"):
        return False
    if o.get("isSidechain") and not is_subagent:
        return False
    origin = o.get("origin")
    if isinstance(origin, dict):
        return origin.get("kind") == "human"
    if o.get("turnOrigin"):
        return o.get("turnOrigin") == "human"
    c = (o.get("message") or {}).get("content")
    if isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return False
        return True
    return isinstance(c, str) and not c.lstrip().startswith("<")


def claude_parse_lines(lines: List[str], is_subagent: bool = False) -> List[Dict[str, Any]]:
    turns: List[Dict[str, Any]] = []
    for line in lines:
        if (
            '"type":"user"' not in line
            and '"type": "user"' not in line
            and '"type":"assistant"' not in line
            and '"type": "assistant"' not in line
        ):
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        typ = o.get("type")
        if o.get("isSidechain") and not is_subagent:
            continue
        msg = o.get("message") or {}
        content = msg.get("content") or []
        ts = o.get("timestamp")

        if typ == "user":
            if o.get("isCompactSummary"):
                txt = ""
                if isinstance(content, list):
                    txt = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                elif isinstance(content, str):
                    txt = content
                turns.append({"role": "summary", "text": txt, "ts": ts})
                continue
            if not _s._claude_is_human(o, is_subagent=is_subagent):
                continue
            txt_parts = []
            if isinstance(content, str):
                txt_parts.append(content)
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        t = b.get("text", "")
                        if not t.lstrip().startswith(("<ide_", "<system-reminder>", "[Image: source:")):
                            txt_parts.append(t)
            user_text = "\n".join(txt_parts).strip()
            if user_text:
                turns.append({"role": "user", "text": user_text, "ts": ts})

        elif typ == "assistant":
            ast_text = []
            ast_thoughts = []
            ast_tools = []
            if isinstance(content, str):
                ast_text.append(content)
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "text":
                        t = b.get("text", "")
                        if not t.lstrip().startswith(("<ide_", "<system-reminder>")):
                            ast_text.append(t)
                    elif bt == "thinking":
                        th = (b.get("thinking") or "").strip()
                        if th:
                            ast_thoughts.append(th)
                    elif bt == "tool_use":
                        inp = b.get("input") or {}
                        hint = inp.get("command") or inp.get("file_path") or inp.get("description") or ""
                        tool_name = b.get("name") or "tool"
                        if tool_name == "Agent":
                            hint = f"Agent [{inp.get('subagent_type', 'subagent')}]: {inp.get('description', '')}"
                        ast_tools.append(f"{tool_name}: {_s.one_line(str(hint), 120)}")
            for th in ast_thoughts:
                turns.append({"role": "assistant", "text": "", "thought": th, "ts": ts})
            for tl in ast_tools:
                turns.append({"role": "assistant", "text": "", "tool": tl, "ts": ts})
            if ast_text:
                turns.append({"role": "assistant", "text": "\n".join(ast_text), "ts": ts})
    return turns


def claude_files(include_subagents: bool = False) -> List[str]:
    files = [p for p in glob.glob(os.path.join(_s.CLAUDE_DIR, "projects", "*", "*.jsonl")) if os.path.isfile(p)]
    if include_subagents:
        files.extend([p for p in glob.glob(os.path.join(_s.CLAUDE_DIR, "projects", "*", "*", "subagents", "*.jsonl")) if os.path.isfile(p)])
    return files


def claude_meta(path: str, open_sessions: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    st = os.stat(path)
    key = (st.st_mtime, st.st_size)
    is_sub = "/subagents/" in path
    sid = os.path.basename(path)[:-6]
    parent_id = os.path.basename(os.path.dirname(os.path.dirname(path))) if is_sub else ""
    cached = _s._claude_meta_cache.get(path)
    if cached and cached[0] == key:
        meta = dict(cached[1])
    else:
        title = custom = last_prompt = ""
        for line in _s._read_tail(path, 1_500_000):
            if '"ai-title"' in line or '"custom-title"' in line or '"last-prompt"' in line:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") == "ai-title":
                    title = o.get("aiTitle") or title
                elif o.get("type") == "custom-title":
                    custom = o.get("customTitle") or o.get("title") or custom
                elif o.get("type") == "last-prompt":
                    last_prompt = o.get("lastPrompt") or last_prompt
        cwd = ""
        first_prompt = ""
        for line in _s._read_head(path, 400_000):
            if not cwd and '"cwd"' in line:
                m = re.search(r'"cwd":"([^"]+)"', line)
                if m:
                    cwd = m.group(1)
            if not first_prompt and '"type":"user"' in line:
                try:
                    o = json.loads(line)
                    if _s._claude_is_human(o, is_subagent=is_sub) and not o.get("isCompactSummary"):
                        c = (o.get("message") or {}).get("content")
                        if isinstance(c, list):
                            first_prompt = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                        elif isinstance(c, str):
                            first_prompt = c
                except Exception:
                    pass
            if cwd and first_prompt:
                break
        display_title = custom or title
        if not display_title and first_prompt:
            display_title = f"[Subagent] {_s.one_line(first_prompt, 70)}" if is_sub else _s.one_line(first_prompt, 80)
        if not display_title:
            display_title = f"[Subagent of {parent_id[:8]}]" if is_sub else "(untitled)"

        meta = {
            "source": "claude", "id": sid, "path": path,
            "title": display_title,
            "first_prompt": first_prompt, "last_prompt": last_prompt,
            "project": cwd or os.path.basename(os.path.dirname(os.path.dirname(path) if is_sub else path)),
            "updated": st.st_mtime, "size": st.st_size,
            "subagent": is_sub, "parent": parent_id,
        }
        _s._claude_meta_cache[path] = (key, dict(meta))
    os_ = open_sessions if open_sessions is not None else _s.claude_open_sessions()
    if sid in os_:
        meta["open"] = True
        meta["tab_name"] = os_[sid].get("name") or ""
        meta["status"] = os_[sid].get("status") or ""
    return meta


def _claude_read_full(path: str, is_sub: bool) -> List[Dict[str, Any]]:
    """Read an entire transcript. The byte filter skips tool results and metadata,
    which is what keeps a 200 MB session under a second."""
    lines: List[str] = []
    with open(path, "rb") as f:
        for raw in f:
            if b'"type":"assistant"' in raw or (b'"type":"user"' in raw and b'"tool_result"' not in raw[:3000]):
                lines.append(raw.decode("utf-8", "replace"))
    return _s.claude_parse_lines(lines, is_subagent=is_sub)


def claude_read(path: str, mode: str = "full", n: int = 0) -> List[Dict[str, Any]]:
    """mode="tail" reads only enough of the end for a preview. Its exchange numbers are
    relative to the tail, so everything that numbers stations must use the full read."""
    is_sub = "/subagents/" in path
    if mode != "tail":
        return _s._claude_read_full(path, is_sub)
    size = os.path.getsize(path)
    nbytes = 4_000_000
    while True:
        lines = _s._read_tail(path, nbytes)
        turns = _s.claude_parse_lines(lines, is_subagent=is_sub)
        users = sum(1 for t in turns if t["role"] in ("user", "summary"))
        if users > n or nbytes >= size:
            return turns
        nbytes *= 4


def claude_read_segments(path: str, is_sub: bool = False) -> List[List[Dict[str, Any]]]:
    """Read a transcript and split turns into segments at compact_boundary lines."""
    segments_raw: List[List[str]] = [[]]
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"compact_boundary"' in line:
                    try:
                        o = json.loads(line)
                        if o.get("type") == "system" and o.get("subtype") == "compact_boundary":
                            segments_raw.append([])
                            continue
                    except Exception:
                        pass
                segments_raw[-1].append(line)
    parsed = [_s.claude_parse_lines(s_lines, is_subagent=is_sub) for s_lines in segments_raw]
    return parsed if parsed else [[]]
