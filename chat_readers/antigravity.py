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
def agy_read_segments(chat: Dict[str, Any]) -> Tuple[List[List[Dict[str, Any]]], bool]:
    cid = chat.get("id", "")
    tp = chat.get("path")
    if not tp or not os.path.exists(tp):
        tp = _s._agy_transcript_path(cid)
    if tp and os.path.basename(tp) == "transcript.jsonl":
        full = os.path.join(os.path.dirname(tp), "transcript_full.jsonl")
        if os.path.exists(full):
            tp = full
    segs: List[List[Dict[str, Any]]] = [[]]
    has_markers = False
    if tp and os.path.exists(tp):
        with open(tp, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                typ = o.get("type")
                subtyp = o.get("subtype")
                if (typ == "system" and subtyp == "compact_boundary") or typ == "compaction":
                    has_markers = True
                    segs.append([])
                    continue
                ts = o.get("created_at")
                if typ == "USER_INPUT" and o.get("source", "").startswith("USER"):
                    segs[-1].append({"role": "user", "text": _s._strip_user_request(o.get("content", "")), "ts": ts})
                elif typ == "SYSTEM_MESSAGE" and not segs[-1]:
                    raw_c = o.get("content", "")
                    m = re.search(r"content=(.*?)(?:\Z|</SYSTEM_MESSAGE>)", raw_c, re.S)
                    prompt_txt = m.group(1).strip() if m else raw_c.strip()
                    if prompt_txt:
                        segs[-1].append({"role": "user", "text": f"[Subagent Task Brief]\n{prompt_txt}", "ts": ts})
                elif typ == "PLANNER_RESPONSE":
                    if o.get("thinking"):
                        th = o["thinking"].strip()
                        if th:
                            segs[-1].append({"role": "assistant", "text": "", "thought": th, "ts": ts})
                    for tc in o.get("tool_calls") or []:
                        name = tc.get("name") if isinstance(tc, dict) else str(tc)
                        args = tc.get("args") if isinstance(tc, dict) else None
                        if not isinstance(args, dict):
                            args = {}
                        if name == "invoke_subagent":
                            role = args.get("Role") or args.get("TypeName") or "subagent"
                            prompt = args.get("Prompt") or ""
                            segs[-1].append({
                                "role": "assistant", "text": "",
                                "tool": f"invoke_subagent [{role}]: {_s.one_line(str(prompt), 100)}",
                                "tool_name": str(name), "tool_input": args, "ts": ts,
                            })
                        else:
                            cmd = args.get("CommandLine") or args.get("Path") or ""
                            segs[-1].append({
                                "role": "assistant", "text": "", "tool": f"{name} {_s.one_line(str(cmd), 100)}",
                                "tool_name": str(name), "tool_input": args, "ts": ts,
                            })
                    if o.get("content"):
                        segs[-1].append({"role": "assistant", "text": o["content"], "ts": ts})
        return (segs if segs else [[]]), has_markers
    return [_s.agy_read(cid)], False


def _agy_db() -> Optional[sqlite3.Connection]:
    p = os.path.join(_s.AGY_DIR, "conversation_summaries.db")
    if not os.path.exists(p):
        return None
    for uri in (f"file:{p}?mode=ro", f"file:{p}?immutable=1"):
        try:
            con = sqlite3.connect(uri, uri=True, timeout=5)
            con.execute("select 1 from conversation_summaries limit 1")
            return con
        except Exception:
            continue
    return None


def _agy_transcript_path(cid: str) -> str:
    full = os.path.join(_s.AGY_DIR, "brain", cid, ".system_generated", "logs", "transcript_full.jsonl")
    if os.path.exists(full):
        return full
    return os.path.join(_s.AGY_DIR, "brain", cid, ".system_generated", "logs", "transcript.jsonl")


def _workspace_name(uris: str) -> str:
    try:
        lst = json.loads(uris) if uris else []
    except Exception:
        lst = [uris]
    names = [u.rstrip("/").split("/")[-1] for u in lst if u]
    return ", ".join(names)


def _workspace_paths(uris: str) -> List[str]:
    try:
        values = json.loads(uris) if uris else []
    except Exception:
        values = [uris]
    if not isinstance(values, list):
        values = [values]
    paths = []
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        parsed = urllib.parse.urlparse(value)
        path = urllib.parse.unquote(parsed.path) if parsed.scheme == "file" else value
        if path:
            paths.append(path)
    return paths


def agy_list(include_subagents: bool = False) -> List[Dict[str, Any]]:
    con = _s._agy_db()
    out: List[Dict[str, Any]] = []
    if con is None:
        return out
    try:
        fts_titles: Dict[str, str] = {}
        try:
            for cid, title in con.execute(
                    "select conversation_id, title from conversations_fts where chunk_index=-1"):
                if title:
                    fts_titles[cid] = title
        except Exception:
            pass
        rows = con.execute(
            "select conversation_id, title, preview, step_count, last_modified_time, workspace_uris, "
            "parent_conversation_id, killed, last_user_input_time from conversation_summaries").fetchall()
        for cid, title, preview, steps, lmt, ws, parent, killed, lui in rows:
            if parent and not include_subagents:
                continue
            display_title = title or fts_titles.get(cid)
            if not display_title and preview:
                display_title = _s.one_line(preview, 80)
            if not display_title and parent:
                # Subagent without title: check task brief from transcript
                try:
                    tp = _s._agy_transcript_path(cid)
                    if os.path.exists(tp):
                        with open(tp, "r", encoding="utf-8") as f:
                            for _ in range(6):
                                l = f.readline()
                                if not l: break
                                m = re.search(r"content=(.*?)(?:\n|\Z)", l)
                                if m:
                                    snippet = m.group(1).replace("\\n", " ").strip()
                                    display_title = f"[Subagent] {_s.one_line(snippet, 70)}"
                                    break
                except Exception:
                    pass
            if not display_title:
                display_title = f"[Subagent of {parent[:8]}]" if parent else "(untitled)"
            out.append({
                "source": "antigravity", "id": cid,
                "title": display_title,
                "project": _s._workspace_name(ws), "_project_path": _s._workspace_paths(ws),
                "updated": lmt, "steps": steps,
                "subagent": bool(parent), "parent": parent or "",
            })
    finally:
        con.close()
    return out


def _strip_user_request(c: str) -> str:
    m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", c or "", re.S)
    return (m.group(1) if m else (c or "")).strip()


def agy_read(cid: str) -> List[Dict[str, Any]]:
    tp = _s._agy_transcript_path(cid)
    turns: List[Dict[str, Any]] = []
    if os.path.exists(tp):
        with open(tp, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                typ = o.get("type")
                ts = o.get("created_at")
                if typ == "USER_INPUT" and o.get("source", "").startswith("USER"):
                    turns.append({"role": "user", "text": _s._strip_user_request(o.get("content", "")), "ts": ts})
                elif typ == "SYSTEM_MESSAGE" and not turns:
                    # Capture subagent task instructions
                    raw_c = o.get("content", "")
                    m = re.search(r"content=(.*?)(?:\Z|</SYSTEM_MESSAGE>)", raw_c, re.S)
                    prompt_txt = m.group(1).strip() if m else raw_c.strip()
                    if prompt_txt:
                        turns.append({"role": "user", "text": f"[Subagent Task Brief]\n{prompt_txt}", "ts": ts})
                elif typ == "PLANNER_RESPONSE":
                    if o.get("thinking"):
                        th = o["thinking"].strip()
                        if th:
                            turns.append({"role": "assistant", "text": "", "thought": th, "ts": ts})
                    for tc in o.get("tool_calls") or []:
                        name = tc.get("name") if isinstance(tc, dict) else str(tc)
                        args = tc.get("args") if isinstance(tc, dict) else None
                        if not isinstance(args, dict):
                            args = {}
                        if name == "invoke_subagent":
                            role = args.get("Role") or args.get("TypeName") or "subagent"
                            prompt = args.get("Prompt") or ""
                            turns.append({"role": "assistant", "text": "", "tool": f"invoke_subagent [{role}]: {_s.one_line(str(prompt), 100)}", "ts": ts})
                        else:
                            cmd = args.get("CommandLine") or args.get("Path") or ""
                            turns.append({"role": "assistant", "text": "", "tool": f"{name} {_s.one_line(str(cmd), 100)}", "ts": ts})
                    if o.get("content"):
                        turns.append({"role": "assistant", "text": o["content"], "ts": ts})
        if turns:
            return turns

    con = _s._agy_db()
    if con is None:
        return turns
    try:
        rows = con.execute(
            "select chunk_index, user_body, agent_body, user_step_byte_offsets, agent_step_byte_offsets, "
            "last_step_created_at from conversations_fts where conversation_id=? and chunk_index>=0 "
            "order by chunk_index", (cid,)).fetchall()
    finally:
        con.close()
    segs: List[Tuple[int, str, str]] = []
    for _ci, ub, ab, uo, ao, _ts in rows:
        for role, body, offs in (("user", ub, uo), ("assistant", ab, ao)):
            if not body:
                continue
            b = body.encode("utf-8")
            try:
                pairs = json.loads(offs.decode() if isinstance(offs, bytes) else (offs or "[]"))
            except Exception:
                pairs = []
            if not pairs:
                segs.append((0, role, body))
                continue
            for k, (step, off) in enumerate(pairs):
                end = pairs[k + 1][1] if k + 1 < len(pairs) else len(b)
                txt = b[off:end].decode("utf-8", "replace").strip()
                if txt:
                    segs.append((int(step), role, txt))
    segs.sort(key=lambda s: (s[0], 0 if s[1] == "user" else 1))
    for _step, role, txt in segs:
        turns.append({"role": role, "text": txt, "ts": None})
    return turns


def _agy_hubs(daemon_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Discovery files Antigravity writes for local clients, live processes only."""
    hubs = []
    base_dir = daemon_dir if daemon_dir is not None else os.path.join(_s.AGY_DIR, "daemon")
    files = glob.glob(os.path.join(base_dir, "hub_*.json"))
    files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0.0, reverse=True)
    for p in files:
        with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if _s._pid_alive(int(d["pid"])) and d.get("httpPort") and d.get("csrfToken"):
                hubs.append(d)
    return hubs


def _agy_rpc(hub: Dict[str, Any], method: str, data: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
    payload = json.dumps(data or {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{hub['httpPort']}/exa.language_server_pb.LanguageServerService/{method}",
        data=payload, method="POST",
        headers={"Content-Type": "application/json", "Connect-Protocol-Version": "1",
                 "X-Codeium-Csrf-Token": hub["csrfToken"]})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # Loopback only: ignore any proxy.
    with opener.open(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}
