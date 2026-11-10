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

from store import ttl_cached
import server as _s
def all_chats(source: str = "all", include_subagents: bool = False) -> List[Dict[str, Any]]:
    chats: List[Dict[str, Any]] = []
    if source in ("all", "claude"):
        opens = _s.claude_open_sessions()
        for p in _s.claude_files(include_subagents):
            try:
                chats.append(_s.claude_meta(p, opens))
            except Exception as e:
                _s.log("claude meta failed", p, e)
    if source in ("all", "antigravity"):
        chats.extend(_s.agy_list(include_subagents))
    if source in ("all", "opencode"):
        chats.extend(_s.opencode_list(include_subagents))
    if source in ("all", "copilot"):
        chats.extend(_s.copilot_list(include_subagents))
    if source in ("all", "pi"):
        chats.extend(getattr(_s, "pi_list", lambda include_subagents=False: [])(include_subagents))
    if source in ("all", "cursor"):
        chats.extend(getattr(_s, "cursor_list", lambda include_subagents=False: [])(include_subagents))
    if source in ("all", "devin"):
        chats.extend(getattr(_s, "devin_list", lambda include_subagents=False: [])(include_subagents))
    if not include_subagents:
        worker_sessions = _s._worker_chat_session_ids()
        chats = [chat for chat in chats
                 if not _s._chat_is_in_worker_worktree(chat)
                 and str(chat.get("id") or "") not in worker_sessions]
    excl = threads.load_exclusions(_s.SUBWAY_DIR)
    if excl.get("apps") or excl.get("projects"):
        chats = [c for c in chats if not threads.is_chat_excluded(c, excl)]
    return chats


def _project_names(project: Any) -> List[str]:
    """Folder names for a chat's project. Claude stores a full cwd, and Antigravity can list several."""
    names = []
    for part in str(project or "").split(","):
        base = os.path.basename(part.strip().rstrip("/")).lower()
        if base and base != "unknown":
            names.append(base)
    return names


def _newest(chats: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return max(chats, key=lambda c: _s.sort_key_ts(c.get("updated"))) if chats else None


def resolve(ident: str, source: str = "all", project: str = "", strict: bool = False) -> Dict[str, Any]:
    """Resolve a chat reference. strict=True refuses fuzzy title matches, for actions
    such as send_note where the wrong recipient is worse than an error."""
    ident = (ident or "").strip()
    aliases = _s.load_aliases()
    clean_alias = ident.lower().lstrip("@")

    # 1. Explicit pinned aliases (highest precedence, never drifts)
    if clean_alias in aliases:
        target = aliases[clean_alias]
        for c in _s.all_chats(source, include_subagents=True):
            if c["id"].lower() == target.lower():
                return c
        ident = target
        clean_alias = ident.lower().lstrip("@")

    chats = _s.all_chats(source, include_subagents=True)
    if project:
        chats = [c for c in chats if project.lower() in (c.get("project") or "").lower()]
    if not chats:
        raise ValueError(f"no chats found across active agents (source={source}, project={project})")

    # 2. Floating Latest. Never the caller itself or a subagent: "the latest other window" is what's meant.
    me = _s.detect_self()
    my_id = (me or {}).get("id")
    boardable = [c for c in chats if not c.get("subagent") and c["id"] != my_id]
    if clean_alias in ("", "latest", "last", "newest"):
        hit = _s._newest(boardable)
        if hit:
            return hit
    latest_for = {"agy-latest": "antigravity", "gemini-latest": "antigravity", "agy": "antigravity",
                  "gemini": "antigravity", "claude-latest": "claude", "claude": "claude",
                  "opencode-latest": "opencode", "opencode": "opencode",
                  "copilot-latest": "copilot", "copilot": "copilot",
                  "deep-latest": "pi", "deep": "pi", "deepai-latest": "pi", "deepai": "pi",
                  "pi-latest": "pi", "pi": "pi",
                  "cursor-latest": "cursor", "cursor": "cursor",
                  "devin-latest": "devin", "devin": "devin"}
    if clean_alias in latest_for:
        hit = _s._newest([c for c in boardable if c.get("source") == latest_for[clean_alias]])
        if hit:
            return hit

    # 3. Dynamic Workspace Heads & Topic Slugs (<agent>-<target>)
    m = re.match(r"^(agy|gemini|claude|opencode|copilot|deep|deepai|pi|cursor|devin)-([a-z0-9_.-]+)$", clean_alias)
    if m:
        agent_prefix, rest = m.group(1), m.group(2)
        src = {"agy": "antigravity", "gemini": "antigravity",
               "deep": "pi", "deepai": "pi"}.get(agent_prefix, agent_prefix)
        agent_chats = [c for c in boardable if c.get("source") == src]

        # 3a. Workspace head: exact folder name first, then a partial folder name.
        exact = [c for c in agent_chats if rest in _s._project_names(c.get("project"))]
        hit = _s._newest(exact)
        if hit:
            return hit
        hit = _s._newest([c for c in agent_chats if any(rest in p for p in _s._project_names(c.get("project")))])
        if hit:
            return hit

        # 3b. Topic slug search within this agent (e.g. @agy-subway-stations, @agy-gitignore)
        if not strict:
            tokens = [t for t in re.split(r"[\s_-]+", rest) if len(t) >= 2]
            scored = []
            for c in agent_chats:
                title_low = (c.get("title") or "").lower()
                score = sum(1 for t in tokens if t in title_low)
                if score > 0:
                    scored.append((score, _s.sort_key_ts(c.get("updated")), c))
            if scored:
                scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
                return scored[0][2]

    # 4. Fallback: ID prefix, tab_name, or title match
    low = clean_alias
    clean_low = low.replace("agent-", "")
    hits = [c for c in chats if c["id"].lower().startswith(low) or c["id"].lower().replace("agent-", "").startswith(clean_low)]
    if not hits:
        hits = [c for c in chats if (c.get("tab_name") or "").lower() == low]
    if not hits and not strict:
        hits = [c for c in chats if low in (c.get("title") or "").lower()]
    if not hits and not strict:
        # Cross-agent topic slug match
        tokens = [t for t in re.split(r"[\s_-]+", low) if len(t) >= 2]
        scored = []
        for c in chats:
            title_low = (c.get("title") or "").lower()
            score = sum(1 for t in tokens if t in title_low)
            if score > 0:
                scored.append((score, _s.sort_key_ts(c.get("updated")), c))
        if scored:
            scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
            hits = [scored[0][2]]
    if not hits:
        raise ValueError(f"no chat matches '{ident}'. Use list_chats or search_chat.")
    if len(hits) > 1:
        hits.sort(key=lambda c: _s.sort_key_ts(c.get("updated")), reverse=True)
        exact_id = [c for c in hits if c["id"].lower() == low]
        if exact_id:
            return exact_id[0]
        listing = "\n".join(f"- {c['source']} {c['id'][:8]} {c.get('title')}" for c in hits[:10])
        raise ValueError(f"'{ident}' is ambiguous; specify more characters:\n{listing}")
    return hits[0]


def _detect_self_uncached() -> Optional[Dict[str, Any]]:
    forced = os.environ.get("SUBWAY_STATION_ID")
    if forced:
        return {"source": "manual", "id": forced, "tab_name": None, "synthetic": True}
    worker_id = os.environ.get("SUBWAY_WORKER_ID")
    if worker_id:
        return {"source": "worker", "id": worker_id, "tab_name": None, "synthetic": True}
    # DeepAI Code (pi): the app spawns this MCP server as a child of the `pi`
    # runtime and marks the environment. The station id is the newest session
    # transcript for this cwd, so it matches what list_chats/read_chat report.
    if os.environ.get("PI_CODING_AGENT") or os.environ.get("PI_CODING_AGENT_DIR"):
        pi_sid = _pi_session_from_ancestry()
        return {"source": "pi", "id": pi_sid or f"pi-{os.getppid()}", "tab_name": None,
                "synthetic": not bool(pi_sid)}

    agy_conv = os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
    if agy_conv:
        return {"source": "antigravity", "id": agy_conv, "tab_name": None}
    if os.environ.get("SUBWAY_NO_NOTIFY"):
        return None

    opens: Dict[int, Dict[str, Any]] = {}
    for p in glob.glob(os.path.join(_s.CLAUDE_DIR, "sessions", "*.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            opens[int(d.get("pid", 0))] = d
        except Exception:
            pass

    pid = os.getppid()
    if sys.platform == "win32":
        try:
            cmd = 'powershell -NoProfile -Command "Get-CimInstance Win32_Process | Select ProcessId,ParentProcessId,CommandLine | ConvertTo-Json"'
            raw = subprocess.check_output(cmd, shell=True, text=True, timeout=5)
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            win_procs = {int(p["ProcessId"]): (int(p.get("ParentProcessId") or 0), str(p.get("CommandLine") or "")) for p in data if p.get("ProcessId")}
        except Exception:
            return None
        for _ in range(10):
            if pid <= 0:
                return None
            if pid in opens:
                return {"source": "claude", "id": opens[pid].get("sessionId"), "tab_name": opens[pid].get("name")}
            entry = win_procs.get(pid)
            if not entry:
                return None
            ppid, cl = entry
            name = os.path.basename(cl.split()[0].replace('"', '')).lower() if cl.strip() else ""
            if name.startswith("opencode"):
                return {"source": "opencode", "id": f"opencode-{pid}", "tab_name": None, "synthetic": True}
            if name == "agy" or name.startswith("antigravity"):
                return {"source": "antigravity", "id": f"agy-{pid}", "tab_name": None, "synthetic": True}
            if name.startswith("copilot"):
                session_id = _s._copilot_current_session()
                return {"source": "copilot", "id": session_id or f"copilot-{pid}",
                        "tab_name": None, "synthetic": not bool(session_id)}
            pid = ppid
        return None

    for _ in range(10):
        if pid <= 1:
            return None
        if pid in opens:
            return {"source": "claude", "id": opens[pid].get("sessionId"), "tab_name": opens[pid].get("name")}
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=2).stdout.strip()
            ppid, _, comm = out.partition(" ")
            name = os.path.basename(comm.strip()).lower()
        except Exception:
            return None
        # One MCP process serves a whole hub or client, so the window it belongs to is the
        # identity available. Synthetic ids keep windows apart but have no chat or inbox.
        if name.startswith("opencode"):
            return {"source": "opencode", "id": f"opencode-{pid}", "tab_name": None, "synthetic": True}
        if name == "agy" or name.startswith("antigravity"):
            best_cid = ""
            with contextlib.suppress(Exception):
                from wake_driver import resolve_agy_conversation
                best_cid = resolve_agy_conversation(os.getcwd(), allow_busy=True)
            if best_cid:
                return {"source": "antigravity", "id": best_cid, "tab_name": None}
            return {"source": "antigravity", "id": f"agy-{pid}", "tab_name": None, "synthetic": True}
        if name.startswith("copilot"):
            session_id = _s._copilot_current_session()
            return {"source": "copilot", "id": session_id or f"copilot-{pid}",
                    "tab_name": None, "synthetic": not bool(session_id)}
        try:
            pid = int(ppid)
        except ValueError:
            return None
    return None


def _pi_session_from_ancestry() -> str:
    """This pi chat's session id, found via the process that spawned this server.

    The wake extension writes ~/.subway/pi_sessions/<pi-pid> = sessionId,
    and this MCP server is a child of that pi process. Walking the ancestor chain
    gives the RIGHT session; picking the newest transcript file does not, because
    several chats share a cwd and the newest file belongs to whichever chatted last.
    Falls back to the newest session file when no mapping is present yet.
    """
    subway_dir = getattr(_s, "SUBWAY_DIR", os.path.join(os.path.expanduser("~"), ".subway"))
    mapping_dir = os.path.join(subway_dir, "pi_sessions")
    pid = os.getpid()
    for _ in range(6):
        try:
            pid = int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                                     capture_output=True, text=True, timeout=2).stdout.strip() or 0)
        except (OSError, ValueError, subprocess.SubprocessError):
            break
        if pid <= 1:
            break
        try:
            path = os.path.join(mapping_dir, str(pid))
            if os.path.isfile(path):
                sid = open(path, "r", encoding="utf-8", errors="replace").read().strip()
                if sid:
                    return sid
        except OSError:
            continue
    try:
        files = _s.pi_files()
        if files:
            newest = max(files, key=os.path.getmtime)
            base = os.path.basename(newest)
            return base.rsplit("_", 1)[-1].split(".")[0] if "_" in base else base.split(".")[0]
    except Exception:
        pass
    return f"pi-{os.getppid()}"


def detect_self() -> Optional[Dict[str, Any]]:
    """Identify the agent window this server belongs to, across every supported client."""
    now = time.time()
    forced = os.environ.get("SUBWAY_STATION_ID")
    worker = os.environ.get("SUBWAY_WORKER_ID")
    if forced and (_s._self_cache[1] is None or _s._self_cache[1].get("id") != forced):
        _s._self_cache = (now, _s._detect_self_uncached())
    elif not forced and _s._self_cache[1] and _s._self_cache[1].get("source") == "manual":
        _s._self_cache = (now, _s._detect_self_uncached())
    elif worker and (_s._self_cache[1] is None or _s._self_cache[1].get("id") != worker):
        _s._self_cache = (now, _s._detect_self_uncached())
    elif not worker and _s._self_cache[1] and _s._self_cache[1].get("source") == "worker":
        _s._self_cache = (now, _s._detect_self_uncached())
    elif now - _s._self_cache[0] > _s._SELF_TTL:
        _s._self_cache = (now, _s._detect_self_uncached())
    return _s._self_cache[1]


def _self_id() -> str:
    """Stable id for this window. Unknown callers get one per process, so two of them still collide."""
    me = _s.detect_self()
    return me["id"] if me else f"host-{os.getpid()}"


def _short_id(station: str) -> str:
    return station[:8] if re.match(r"^[0-9a-f]{8}-", station) else station


def _detected_chat() -> Optional[Dict[str, Any]]:
    """Return the detected calling chat, or None if Subway cannot detect the caller at all."""
    if _s._self_id is not _s._orig_self_id:
        try:
            sid = _s._self_id()
            if sid and not sid.startswith(f"host-{os.getpid()}"):
                return {"source": "test", "id": sid, "tab_name": None}
        except Exception:
            pass
    me = _s.detect_self()
    if me:
        return me
    try:
        sid = _s._self_id()
        if sid and not sid.startswith(f"host-{os.getpid()}"):
            return {"source": "test", "id": sid, "tab_name": None}
    except Exception:
        pass
    return None


def _id_matches(supplied: Optional[str], target: Optional[str]) -> bool:
    """Check if a supplied caller/station ID matches the target detected ID."""
    if not supplied or not target:
        return False
    s = str(supplied).strip().lower()
    t = str(target).strip().lower()
    if s == t:
        return True
    if _s._short_id(s) == _s._short_id(t):
        return True
    if len(s) >= 8 and len(t) >= 8:
        if s.startswith(t) or t.startswith(s):
            return True
    try:
        aliases = _s.load_aliases()
        clean = s.lstrip("@")
        if clean in aliases:
            resolved = aliases[clean].lower()
            if resolved == t or _s._short_id(resolved) == _s._short_id(t):
                return True
    except Exception:
        pass
    return False


def _get_last_entry_preview(chat: Dict[str, Any]) -> str:
    """Return a 1-2 sentence snippet of the last user prompt and assistant response."""
    src = chat.get("source")
    cid = chat.get("id")
    try:
        if src == "claude":
            lp = chat.get("last_prompt") or ""
            if lp:
                return f"Last: {_s.one_line(lp, 110)}"
            data = _s.claude_read(chat["path"], "tail", 1)
        elif src == "opencode":
            data = _s.opencode_read(cid)
        elif src == "copilot":
            data = _s.copilot_read_segments(chat)[0][0]
        elif src == "pi":
            data = _s.pi_read_segments(chat)[0][0]
        else:
            data = _s.agy_read(cid)

        ex = _s.to_exchanges(data)
        if not ex:
            return chat.get("title") or "(no messages)"
        last = ex[-1]
        u = _s.one_line(last.get("user") or "", 65)
        a = _s.one_line(last.get("assistant")[0] if last.get("assistant") else "", 75)
        if u and a:
            return f"User: \"{u}\" -> Agent: \"{a}\""
        elif u:
            return f"User: \"{u}\""
        elif a:
            return f"Agent: \"{a}\""
        return chat.get("title") or ""
    except Exception:
        return chat.get("title") or ""


def _file_may_contain(path: str, needles: List[bytes]) -> bool:
    """Cheap whole-file check, so search only parses transcripts that can match."""
    overlap = max(len(n) for n in needles)
    tail = b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                return False
            window = (tail + chunk).lower()
            if any(n in window for n in needles):
                return True
            tail = chunk[-overlap:]


@ttl_cached(15.0)
def get_chat_context(session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    path = None
    if session_id:
        if os.path.isfile(session_id):
            path = session_id
        else:
            matches = glob.glob(os.path.join(_s.CLAUDE_DIR, "projects", "*", f"{session_id}.jsonl"))
            if matches:
                path = matches[0]
    if not path:
        ident = _s.detect_self()
        if ident and ident.get("source") == "claude" and ident.get("id"):
            matches = glob.glob(os.path.join(_s.CLAUDE_DIR, "projects", "*", f"{ident['id']}.jsonl"))
            if matches:
                path = matches[0]

    if not path or not os.path.exists(path):
        return None

    last_tokens: Optional[int] = None
    window = 200000
    segments = 1
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"compact_boundary"' in line:
                    try:
                        ent = json.loads(line)
                        if ent.get("type") == "system" and ent.get("subtype") == "compact_boundary":
                            segments += 1
                            continue
                    except Exception:
                        pass
                if '"assistant"' not in line and '"type":"assistant"' not in line:
                    continue
                try:
                    ent = json.loads(line)
                except Exception:
                    continue
                if ent.get("type") != "assistant":
                    continue
                msg = ent.get("message") if isinstance(ent.get("message"), dict) else ent
                usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else ent.get("usage")
                if isinstance(usage, dict):
                    inp = int(usage.get("input_tokens") or 0)
                    cread = int(usage.get("cache_read_input_tokens") or 0)
                    ccreate = int(usage.get("cache_creation_input_tokens") or 0)
                    last_tokens = inp + cread + ccreate
                model = str(msg.get("model") or ent.get("model") or "")
                if "[1m]" in model.lower() or "1m" in model.lower():
                    window = 1000000
                elif model:
                    window = 200000
    except Exception:
        pass
    if last_tokens and last_tokens > window:
        window = 1000000
    if last_tokens is None:
        return None
    return {
        "pct": round((last_tokens / window) * 100, 1),
        "tokens": last_tokens,
        "window": window,
        "segments": segments,
    }
