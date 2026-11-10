"""Append-only thread log: one JSON line per event, so a thread graph can be built from records.

A thread is everything that grows from one initiator (a chat id, or a worker started from the HUD).
Records are stored at <base_dir>/threads/<safe_name(thread_id)>.jsonl. This module imports only the
standard library and never imports server, so either side can change without a cycle.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import socket
import sys
import time
from typing import Any, Dict, Iterable, List, Optional

try:
    import fcntl
except ImportError:  # Windows: each write is still one atomic append, but not locked across processes.
    fcntl = None  # type: ignore[assignment]

THREADS_SUBDIR = "threads"
KINDS = {
    "spawned", "followed", "rerouted", "approved", "rejected", "started", "stopped", "finished",
    "escalated", "permission_asked", "permission_answered", "note", "terminal_run",
    "forked", "resumed", "rewound",
}
RECORD_KINDS = KINDS
EDGE_KINDS = {"spawned", "followed", "rerouted", "note", "terminal_run", "forked", "resumed", "rewound"}

STRING_CAP = 500
LIST_CAP = 50


def _caller_station_id() -> str:
    """Station id for this window, resolved without importing server.

    Uses sys.modules first so a running server is reused, then falls back to a
    direct import of the standalone station module.
    """
    server = sys.modules.get("server")
    get_id = getattr(server, "get_station_id", None)
    if callable(get_id):
        return get_id()
    try:
        from station import get_station_id as station_id
    except ImportError:
        return ""
    return station_id()


def safe_name(thread_id: str) -> str:
    """A file name for any thread id: everything outside [A-Za-z0-9_-] becomes _, plus a hash of the id."""
    tid = str(thread_id or "")
    if not tid:
        raise ValueError("thread id is required")
    digest = hashlib.sha1(tid.encode("utf-8")).hexdigest()[:8]
    return f"{re.sub(r'[^A-Za-z0-9_-]', '_', tid)[:80]}-{digest}"


def _threads_dir(base_dir: str) -> str:
    path = os.path.join(base_dir, THREADS_SUBDIR)
    os.makedirs(path, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)
    return path


def thread_path(base_dir: str, thread_id: str) -> str:
    return os.path.join(_threads_dir(base_dir), f"{safe_name(thread_id)}.jsonl")


def _cap_string(value: str) -> str:
    return value if len(value) <= STRING_CAP else value[:STRING_CAP] + "…"


def _capped(value: Any, _depth: int = 0) -> Any:
    """Copy data with every nested string capped and every list trimmed, so one record stays small."""
    if _depth > 8:
        return "…"
    if isinstance(value, str):
        return _cap_string(value)
    if isinstance(value, dict):
        return {str(k)[:200]: _capped(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_capped(v, _depth + 1) for v in list(value)[:LIST_CAP]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _cap_string(str(value))


def record(base_dir: str, thread_id: str, kind: str, node: str, peer: str = "", data: Optional[Dict[str, Any]] = None,
           at: Optional[float] = None) -> None:
    """Append one record to the thread's log. Raises ValueError for an unknown kind or an empty thread id."""
    if kind not in KINDS:
        raise ValueError(f"unknown thread record kind '{kind}'")
    line = json.dumps({
        "v": 1,
        "thread": str(thread_id),
        "at": at if at is not None else time.time(),
        "kind": kind,
        "node": str(node or ""),
        "peer": str(peer or ""),
        "who": {
            "person": os.environ.get("USER") or os.environ.get("USERNAME") or "user",
            "station_id": _caller_station_id(),
            "hostname": socket.gethostname()
        },
        "data": _capped(dict(data or {})),
    }, ensure_ascii=False) + "\n"
    payload = line.encode("utf-8")
    path = thread_path(base_dir, thread_id)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if fcntl is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, payload)
    finally:
        if fcntl is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def record_edge(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Record an edge in the thread graph.

    Supports:
      record_edge(parent_id, child_id, kind="forked", detail=..., at=..., base_dir=...)
      record_edge(base_dir, parent_id, child_id, kind="forked", detail=..., at=...)
    """
    base_dir = kwargs.get("base_dir")
    detail = kwargs.get("detail") or kwargs.get("data")
    at = kwargs.get("at")

    if len(args) >= 4:
        # Check if first arg looks like base_dir
        base = str(args[0])
        parent_id = str(args[1])
        child_id = str(args[2])
        kind = str(args[3])
        if len(args) > 4 and detail is None:
            detail = args[4]
    elif len(args) == 3:
        # Either (base, parent, child) or (parent, child, kind)
        if (os.path.isdir(str(args[0])) or str(args[0]).startswith("/") or str(args[0]).startswith(".")) and not os.path.isdir(str(args[1])):
            base = str(args[0])
            parent_id = str(args[1])
            child_id = str(args[2])
            kind = kwargs.get("kind", "forked")
        else:
            parent_id = str(args[0])
            child_id = str(args[1])
            kind = str(args[2])
            base = base_dir
    elif len(args) == 2:
        parent_id = str(args[0])
        child_id = str(args[1])
        kind = kwargs.get("kind", "forked")
        base = base_dir
    else:
        raise ValueError("parent_id and child_id are required")

    if not base:
        s_mod = sys.modules.get("state")
        base = str(getattr(s_mod, "SUBWAY_DIR", "")) if s_mod else ""
    if not base:
        base = os.path.expanduser("~/.subway-context")

    ts = at if at is not None else time.time()
    record(base, thread_id=parent_id, kind=kind, node=parent_id, peer=child_id, data=detail, at=ts)
    return {
        "parent": parent_id,
        "child": child_id,
        "kind": kind,
        "at": ts,
        "data": detail or {},
    }


def _read_lines(path: str) -> List[Dict[str, Any]]:
    """Every valid record in file order. Blank, partial or corrupt lines are skipped, never raised."""
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "rb") as f:
            chunks = f.read().split(b"\n")
    except OSError:
        return out
    for raw in chunks:
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _empty(thread_id: str) -> Dict[str, Any]:
    return {"thread": str(thread_id), "records": [], "nodes": {}, "edges": []}


def read_thread(base_dir: str, thread_id: str, limit: int = 5000) -> Dict[str, Any]:
    """The last `limit` records, the node summary and the edges (records that point at a peer)."""
    if not str(thread_id or ""):
        raise ValueError("thread id is required")
    recs = _read_lines(os.path.join(base_dir, THREADS_SUBDIR, f"{safe_name(thread_id)}.jsonl"))
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 5000
    recs = recs[-limit:]
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    for r in recs:
        node = str(r.get("node") or "")
        at = r.get("at")
        kinds = r.get("kind")
        if node:
            entry = nodes.setdefault(node, {"first_at": at, "last_at": at, "kinds": []})
            entry["last_at"] = at
            if kinds not in entry["kinds"]:
                entry["kinds"].append(kinds)
        peer = str(r.get("peer") or "")
        if peer and kinds in EDGE_KINDS:
            nodes.setdefault(peer, {"first_at": at, "last_at": at, "kinds": []})
            edges.append({"kind": kinds, "from": node, "to": peer, "at": at, "data": r.get("data") or {}})
    return {"thread": str(thread_id), "records": recs, "nodes": nodes, "edges": edges}


def list_threads(base_dir: str) -> List[Dict[str, Any]]:
    """One entry per thread file, newest first. The thread id comes from the first valid line."""
    folder = os.path.join(base_dir, THREADS_SUBDIR)
    if not os.path.isdir(folder):
        return []
    out: List[Dict[str, Any]] = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(folder, name)
        recs = _read_lines(path)
        if not recs:
            continue
        thread_id = ""
        for r in recs:
            if isinstance(r.get("thread"), str) and r["thread"]:
                thread_id = r["thread"]
                break
        if not thread_id:
            continue
        updated = max((r.get("at") for r in recs if isinstance(r.get("at"), (int, float))), default=0.0)
        out.append({"thread": thread_id, "records": len(recs), "updated_at": updated})
    out.sort(key=lambda t: (t["updated_at"], t["thread"]), reverse=True)
    return out


def record_used_chat(base_dir: str, chat_id: str, tool_name: str, app: str = "", project: str = "") -> None:
    """Record that chat_id called Subway tool_name into used_chats.json atomically."""
    cid = str(chat_id or "").strip()
    if not cid:
        return
    now = time.time()
    path = os.path.join(base_dir, "used_chats.json")
    data = load_used_chats(base_dir)
    entry = data.get(cid)
    if not isinstance(entry, dict):
        entry = {
            "id": cid,
            "source": str(app or ""),
            "project": str(project or ""),
            "first_used": now,
            "last_used": now,
            "last_tool": str(tool_name or ""),
            "tools": [str(tool_name or "")] if tool_name else [],
            "call_count": 1,
        }
    else:
        entry["last_used"] = now
        if tool_name:
            entry["last_tool"] = str(tool_name)
            tools = entry.get("tools")
            if not isinstance(tools, list):
                tools = []
            if str(tool_name) not in tools:
                tools.append(str(tool_name))
            entry["tools"] = tools[:50]
        entry["call_count"] = int(entry.get("call_count") or 0) + 1
        if app and not entry.get("source"):
            entry["source"] = str(app)
        if project and not entry.get("project"):
            entry["project"] = str(project)
    data[cid] = entry

    tmp_path = os.path.join(base_dir, f".used_chats_{os.getpid()}_{time.time_ns()}.tmp")
    try:
        os.makedirs(base_dir, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except OSError:
        with contextlib.suppress(OSError):
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


def load_used_chats(base_dir: str) -> Dict[str, Dict[str, Any]]:
    """Return dictionary of used chats from used_chats.json, or empty dict if missing/invalid."""
    path = os.path.join(base_dir, "used_chats.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def load_exclusions(base_dir: str) -> Dict[str, List[str]]:
    """Return exclusions dict {'apps': [...], 'projects': [...]} merged from exclusions.json and config.json."""
    apps: List[str] = []
    projects: List[str] = []
    path = os.path.join(base_dir, "exclusions.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            for a in d.get("apps") or []:
                s = str(a).strip().lower()
                if s and s not in apps:
                    apps.append(s)
            for p in d.get("projects") or []:
                s = str(p).strip().lower()
                if s and s not in projects:
                    projects.append(s)
    except (OSError, ValueError, TypeError):
        pass

    cfg_path = os.path.join(base_dir, "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if isinstance(cfg, dict):
            ex = cfg.get("exclusions")
            if isinstance(ex, dict):
                for a in ex.get("apps") or []:
                    s = str(a).strip().lower()
                    if s and s not in apps:
                        apps.append(s)
                for p in ex.get("projects") or []:
                    s = str(p).strip().lower()
                    if s and s not in projects:
                        projects.append(s)
    except (OSError, ValueError, TypeError):
        pass

    return {"apps": apps, "projects": projects}


def save_exclusions(base_dir: str, exclusions: Dict[str, List[str]]) -> None:
    """Atomically save exclusions.json into base_dir."""
    apps = [str(a).strip().lower() for a in (exclusions.get("apps") or []) if str(a).strip()]
    projects = [str(p).strip().lower() for p in (exclusions.get("projects") or []) if str(p).strip()]
    data = {"apps": sorted(list(set(apps))), "projects": sorted(list(set(projects)))}
    tmp_path = os.path.join(base_dir, f".exclusions_{os.getpid()}_{time.time_ns()}.tmp")
    path = os.path.join(base_dir, "exclusions.json")
    try:
        os.makedirs(base_dir, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except OSError:
        with contextlib.suppress(OSError):
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


def is_chat_excluded(chat: Dict[str, Any], exclusions: Optional[Dict[str, List[str]]] = None, base_dir: str = "") -> bool:
    """Check if chat is excluded by app or project rules."""
    if not isinstance(chat, dict):
        return False
    if exclusions is None:
        exclusions = load_exclusions(base_dir)
    apps = set(exclusions.get("apps") or [])
    projects = set(exclusions.get("projects") or [])
    if not apps and not projects:
        return False

    c_source = str(chat.get("source") or chat.get("app") or "").strip().lower()
    if c_source and c_source in apps:
        return True

    c_proj = str(chat.get("project") or "").strip().lower()
    if c_proj and projects:
        c_base = os.path.basename(c_proj.rstrip("/")).lower()
        for p in projects:
            if p == c_proj or p == c_base:
                return True
            if c_proj.startswith(p) or p in c_proj:
                return True
            for part in c_proj.split(","):
                base_part = os.path.basename(part.strip().rstrip("/")).lower()
                if base_part == p or p in base_part:
                    return True

    return False

