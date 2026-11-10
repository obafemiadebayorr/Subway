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
def opencode_read_segments(sid: str) -> List[List[Dict[str, Any]]]:
    con = _s._opencode_db()
    if con is None:
        return [[]]
    segs: List[List[Dict[str, Any]]] = [[]]
    try:
        rows = con.execute(
            "select type, data, time_created from session_message where session_id=? order by seq", (sid,)
        ).fetchall()
        for m_type, raw_data, ts in rows:
            ts = _s._opencode_timestamp(ts)
            if m_type == "compaction":
                segs.append([])
                continue
            try:
                d = json.loads(raw_data)
            except Exception:
                continue
            if m_type == "user":
                txt = d.get("text", "")
                if txt.strip():
                    segs[-1].append({"role": "user", "text": txt, "ts": ts})
            elif m_type == "assistant":
                content = d.get("content", [])
                ast_text = []
                for b in content:
                    bt = b.get("type")
                    if bt == "reasoning":
                        th = (b.get("text") or "").strip()
                        if th:
                            segs[-1].append({"role": "assistant", "text": "", "thought": th, "ts": ts})
                    elif bt == "text":
                        txt = (b.get("text") or "").strip()
                        if txt:
                            ast_text.append(txt)
                    elif bt == "tool":
                        call_name = b.get("name") or "tool"
                        tool_input = b.get("input") or b.get("arguments") or b.get("args")
                        if tool_input is None and isinstance(b.get("state"), dict):
                            tool_input = b["state"].get("input")
                        segs[-1].append({
                            "role": "assistant", "text": "", "tool": f"[tool {call_name}]",
                            "tool_name": str(call_name), "tool_input": tool_input, "ts": ts,
                        })
                if ast_text:
                    segs[-1].append({"role": "assistant", "text": "\n".join(ast_text), "ts": ts})
    finally:
        con.close()
    return segs if segs else [[]]


def _opencode_db() -> Optional[sqlite3.Connection]:
    p = os.path.join(_s.OPENCODE_DIR, "opencode.db")
    if not os.path.exists(p):
        return None
    for uri in (f"file:{p}?mode=ro", f"file:{p}?immutable=1"):
        try:
            con = sqlite3.connect(uri, uri=True, timeout=5)
            con.execute("select 1")
            return con
        except Exception:
            continue
    return None


def opencode_list(include_subagents: bool = False) -> List[Dict[str, Any]]:
    con = _s._opencode_db()
    out: List[Dict[str, Any]] = []
    if con is None:
        return out
    try:
        rows = con.execute(
            "select id, title, directory, time_updated, cost, agent, model, parent_id from session_v2 "
            "order by (case when time_updated > 100000000000 then time_updated / 1000.0 else time_updated end) desc"
        ).fetchall()
        for sid, title, dirpath, updated, cost, agent, model, parent_id in rows:
            if parent_id and not include_subagents:
                continue
            display_title = (f"[{agent}] " if agent else "") + (title or "(opencode session)")
            out.append({
                "source": "opencode",
                "id": sid,
                "title": display_title,
                "project": os.path.basename(dirpath or ""),
                "_project_path": dirpath or "",
                "updated": _s._opencode_timestamp(updated),
                "cost": cost,
                "subagent": bool(parent_id),
                "parent": parent_id or "",
            })
    except Exception:
        pass
    finally:
        con.close()
    return out


def opencode_read(sid: str) -> List[Dict[str, Any]]:
    con = _s._opencode_db()
    turns: List[Dict[str, Any]] = []
    if con is None:
        return turns
    try:
        rows = con.execute(
            "select type, data, time_created from session_message where session_id=? order by seq", (sid,)
        ).fetchall()
        for m_type, raw_data, ts in rows:
            ts = _s._opencode_timestamp(ts)
            try:
                d = json.loads(raw_data)
            except Exception:
                continue
            if m_type == "user":
                txt = d.get("text", "")
                if txt.strip():
                    turns.append({"role": "user", "text": txt, "ts": ts})
            elif m_type == "assistant":
                content = d.get("content", [])
                ast_text = []
                for b in content:
                    bt = b.get("type")
                    if bt == "reasoning":
                        th = (b.get("text") or "").strip()
                        if th:
                            turns.append({"role": "assistant", "text": "", "thought": th, "ts": ts})
                    elif bt == "text":
                        txt = (b.get("text") or "").strip()
                        if txt:
                            ast_text.append(txt)
                    elif bt == "tool":
                        call_name = b.get("name") or "tool"
                        tool_input = b.get("input") or b.get("arguments") or b.get("args")
                        if tool_input is None and isinstance(b.get("state"), dict):
                            tool_input = b["state"].get("input")
                        turns.append({
                            "role": "assistant", "text": "", "tool": f"[tool {call_name}]",
                            "tool_name": str(call_name), "tool_input": tool_input, "ts": ts,
                        })
                if ast_text:
                    turns.append({"role": "assistant", "text": "\n".join(ast_text), "ts": ts})
    finally:
        con.close()
    return turns


def _opencode_api(method: str, path: str, directory: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Call OpenCode's local background service, scoped to a project folder."""
    with open(os.path.join(_s.HOME, ".local", "state", "opencode", "service.json"), "r", encoding="utf-8") as f:
        service = json.load(f)
    auth = base64.b64encode(f"opencode:{service['password']}".encode()).decode()
    req = urllib.request.Request(f"{service['url']}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json",
                                          "x-opencode-directory": directory})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=10) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}
