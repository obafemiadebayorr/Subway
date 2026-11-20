"""Station-side IDE state store (Item 77 Unit 1).

Stores the active IDE snapshot reported by a local editor extension
(windows, tabs, active file + selection, terminals, agent panels)
with atomic JSON writes and a short TTL so stale windows expire.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

import subway_home

DEFAULT_TTL_SECONDS = 60.0
_LOCK = threading.Lock()


def _subway_dir() -> str:
    srv = sys.modules.get("server")
    return (
        getattr(srv, "SUBWAY_DIR", None)
        or os.environ.get("SUBWAY_DIR")
        or subway_home.subway_dir()
    )


def _state_path() -> str:
    return os.path.join(_subway_dir(), "ide_state.json")


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ide_state_", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _str(val: Any, max_len: int = 512) -> str:
    if val is None:
        return ""
    return str(val)[:max_len]


def _normalize_selection(sel: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(sel, dict):
        return None
    out: Dict[str, Any] = {}
    for k in ("start_line", "start_col", "end_line", "end_col", "line", "col"):
        if isinstance(sel.get(k), (int, float)) and not isinstance(sel.get(k), bool):
            out[k] = int(sel[k])
    if "text" in sel and sel["text"] is not None:
        out["text"] = _str(sel["text"], 400)
    return out or None


def _normalize_tab(tab: Any) -> Optional[Dict[str, Any]]:
    if isinstance(tab, str):
        p = _str(tab)
        return {"file": p, "title": os.path.basename(p) or p, "active": False, "dirty": False} if p else None
    if not isinstance(tab, dict):
        return None
    fpath = _str(tab.get("file") or tab.get("path") or "")
    title = _str(tab.get("title") or os.path.basename(fpath) or fpath, 200)
    if not fpath and not title:
        return None
    out: Dict[str, Any] = {
        "file": fpath,
        "title": title,
        "active": bool(tab.get("active", False)),
        "dirty": bool(tab.get("dirty", False)),
    }
    if tab.get("window_id") is not None:
        out["window_id"] = _str(tab.get("window_id"), 128)
    return out


def _normalize_window(win: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(win, dict):
        return None
    wid = _str(win.get("id") or win.get("window_id") or "", 128)
    title = _str(win.get("title") or win.get("name") or "", 200)
    workspace = _str(win.get("workspace") or win.get("folder") or "")
    if not (wid or title or workspace):
        return None
    tabs: List[Dict[str, Any]] = []
    for t in (win.get("tabs") or []):
        nt = _normalize_tab(t)
        if nt is not None:
            tabs.append(nt)
    return {
        "id": wid,
        "title": title,
        "workspace": workspace,
        "focused": bool(win.get("focused", False)),
        "tabs": tabs,
    }


def _normalize_terminal(term: Any) -> Optional[Dict[str, Any]]:
    if isinstance(term, str):
        name = _str(term, 200)
        return {"id": "", "name": name, "cwd": "", "active": False} if name else None
    if not isinstance(term, dict):
        return None
    name = _str(term.get("name") or term.get("title") or "", 200)
    cwd = _str(term.get("cwd") or "")
    tid = _str(term.get("id") or "", 128)
    if not (name or cwd or tid):
        return None
    return {
        "id": tid,
        "name": name or os.path.basename(cwd) or "terminal",
        "cwd": cwd,
        "shell": _str(term.get("shell") or "", 64),
        "active": bool(term.get("active", False)),
    }


def _normalize_agent(ag: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(ag, dict):
        return None
    name = _str(ag.get("name") or ag.get("title") or ag.get("role") or "", 200)
    provider = _str(ag.get("provider") or "", 64)
    aid = _str(ag.get("id") or "", 128)
    if not (name or provider or aid):
        return None
    return {
        "id": aid,
        "name": name or provider or "agent",
        "provider": provider,
        "status": _str(ag.get("status") or "", 64),
        "title": _str(ag.get("title") or "", 200),
    }


def normalize_state(payload: Any) -> Dict[str, Any]:
    """Validate and normalize an IDE state payload into the canonical schema."""
    data = payload if isinstance(payload, dict) else {}
    windows = [w for w in (_normalize_window(x) for x in (data.get("windows") or [])) if w is not None]
    tabs = [t for t in (_normalize_tab(x) for x in (data.get("tabs") or [])) if t is not None]
    act_raw = data.get("active") if isinstance(data.get("active"), dict) else {}
    active = {
        "file": _str(act_raw.get("file") or act_raw.get("path") or ""),
        "selection": _normalize_selection(act_raw.get("selection")),
    }
    terminals = [t for t in (_normalize_terminal(x) for x in (data.get("terminals") or [])) if t is not None]
    agents = [a for a in (_normalize_agent(x) for x in (data.get("agents") or [])) if a is not None]
    return {
        "windows": windows,
        "tabs": tabs,
        "active": active,
        "terminals": terminals,
        "agents": agents,
        "editor": _str(data.get("editor") or "", 64),
    }


def update_state(
    payload: Any,
    *,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Persist a normalized IDE state snapshot atomically with an expiration TTL."""
    ts = float(time.time() if now is None else now)
    ttl = max(1.0, float(ttl_seconds or DEFAULT_TTL_SECONDS))
    state = normalize_state(payload)
    state["updated_at"] = ts
    state["ttl_seconds"] = ttl
    state["expires_at"] = ts + ttl
    with _LOCK:
        _atomic_write_json(_state_path(), state)
    return state


def get_state(*, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Return the active IDE state if present and unexpired, else None."""
    ts = float(time.time() if now is None else now)
    path = _state_path()
    with _LOCK:
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        exp = raw.get("expires_at")
        if not isinstance(exp, (int, float)) or ts >= float(exp):
            return None
        out = normalize_state(raw)
        out["updated_at"] = float(raw.get("updated_at") or 0.0)
        out["ttl_seconds"] = float(raw.get("ttl_seconds") or DEFAULT_TTL_SECONDS)
        out["expires_at"] = float(exp)
        return out


def read_snapshot(*, now: Optional[float] = None) -> Dict[str, Any]:
    """Return an API-ready IDE snapshot (empty structure when missing or expired)."""
    cur = get_state(now=now)
    if cur is not None:
        return {"present": True, "expired": False, **cur}
    empty = normalize_state({})
    path = _state_path()
    expired = False
    with _LOCK:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and isinstance(raw.get("expires_at"), (int, float)):
                    ts = float(time.time() if now is None else now)
                    expired = ts >= float(raw["expires_at"])
            except Exception:
                expired = False
    return {"present": False, "expired": expired, **empty}


def clear_state() -> None:
    """Delete any stored IDE state file."""
    path = _state_path()
    with _LOCK:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
