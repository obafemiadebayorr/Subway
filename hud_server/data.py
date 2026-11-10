from __future__ import annotations

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

import queue

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

import sse_stream

import threads

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

import guard
import server as _s


def _compile_hud_data(project: str = "") -> Dict[str, Any]:
    """Compile a full snapshot of the Subway transit network for the HUD."""
    _s._ensure_subway_dir()
    state = _s.load_locks()
    now = time.time()
    lease_files = {}
    for path, s in state["signoffs"].items():
        lease_files[path] = dict(s, drifted=bool(s.get("content_sha")) and _s._file_sha(path) != s["content_sha"])

    records = _s._all_workers()
    active, history = [], []
    for w in records:
        w = dict(w)
        w.pop("cmd", None)
        if w.get("status") == "pending_approval":
            w["approval_code"] = _s.approval_code(w["id"])
        w["priority"] = w.get("priority") if w.get("priority") in ("urgent", "normal", "low") else "normal"
        if w.get("partial_patch") or "partial patch kept" in str(w.get("reason") or "").lower():
            w["partial_patch"] = True
        (active if w.get("status") in _s.ACTIVE_WORKER_STATES else history).append(w)
    history.sort(key=lambda x: str(x.get("completed_at") or x.get("created_at") or ""), reverse=True)

    tracks_summary = {}
    for tf in glob.glob(os.path.join(_s.TRACKS_DIR, "*.jsonl")):
        tname = os.path.basename(tf)[:-6]
        if tname == _s._ALERTS_TRACK:
            continue
        cnt = 0
        last_line = ""
        with contextlib.suppress(OSError):
            with open(tf, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.strip():
                        cnt += 1
                        last_line = line
        last_entry = None
        with contextlib.suppress(ValueError):
            last_entry = json.loads(last_line) if last_line else None
        tracks_summary[tname] = {"count": cnt, "last_entry": last_entry}

    b_info = _s._load_json("benchmarks.json")
    benchmarks_meta = {
        "source": b_info.get("source"),
        "fetched_at": b_info.get("fetched_at"),
        "error": b_info.get("error"),
    }

    cfg_data = _s._load_json("config.json")
    shed_pct = cfg_data.get("shed_at_pct", 20)
    low_limits = []
    quotas_data = _s.load_quotas(refresh=not _s._sampler_running)
    provs = quotas_data.get("providers") or []
    for p_val in provs:
        p_name = p_val["id"]
        if not isinstance(p_val, dict):
            continue
        for pool in p_val.get("pools", []):
            pool_name = pool.get("name") or ""
            for win in pool.get("windows", []):
                left_pct = win.get("left_pct")
                if left_pct is not None and left_pct <= shed_pct:
                    low_limits.append({
                        "provider": p_name,
                        "pool": pool_name,
                        "label": win.get("label") or "",
                        "left_pct": left_pct,
                        "resets_at": win.get("resets_at"),
                    })

    _mt = _s.get_models_track()
    hud_dict = {
        "version": _s.VERSION,
        "timestamp": now,
        "fetched_at": now,
        "time_iso": _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "locks": state["locks"],
        "signoffs": lease_files,
        "workers": active,
        "history": [_slim_hist(e) for e in history[:50]],
        "total_history": len(history),
        "permissions": _s._pending_permissions(),
        "models_track": _mt,
        "models_track_rev": hashlib.sha1(json.dumps(_mt, sort_keys=True, default=str).encode()).hexdigest()[:12],
        "exhausted_models": _s.get_exhausted_models(),
        "settings": _live_settings(cfg_data, low_limits, benchmarks_meta, tracks_summary, quotas_data),
        "tracks": tracks_summary,
        "quotas": quotas_data,
    }
    chat_ctx = _s.get_chat_context()
    if chat_ctx is not None:
        hud_dict["chat_context"] = chat_ctx
    try:
        import station
        hud_dict["station"] = station.get_station_card()
    except Exception:
        pass
    try:
        f = os.path.join(_s.SUBWAY_DIR, "copilot_inline_estimate.json")
        if os.path.exists(f):
            with open(f, "r", encoding="utf-8") as fh:
                hud_dict["copilot_inline_estimate"] = json.load(fh)
        else:
            hud_dict["copilot_inline_estimate"] = {"kind": "copilot_inline_estimate", "estimate": True, "days": {}}
    except Exception:
        hud_dict["copilot_inline_estimate"] = {"kind": "copilot_inline_estimate", "estimate": True, "days": {}}
    return hud_dict

def mark_hud_cache_stale() -> None:
    """Mark every HUD snapshot stale so the next request refreshes in the background."""
    with _s._hud_cache_lock:
        for entry in _s._hud_cache.values():
            entry["stale"] = True

def _bg_refresh_hud(project: str = "") -> None:
    try:
        data = _s._compile_hud_data(project)
        with _s._hud_cache_lock:
            _s._hud_cache[project] = {"data": data, "at": data.get("fetched_at", time.time()), "stale": False, "busy": False}
    except Exception:
        with _s._hud_cache_lock:
            if project in _s._hud_cache:
                _s._hud_cache[project]["busy"] = False

_HIST_DROP = ("spin_args", "git_dirty_before", "code_hashes")
_HIST_TRUNC = {"prompt": 400, "report": 600, "reason": 300}


def _slim_hist(e):
    """History rows ride every 1 s status poll: keep them small. Full rows come from /api/worker_state."""
    if not isinstance(e, dict):
        return e
    out = {k: v for k, v in e.items() if k not in _HIST_DROP}
    for k, n in _HIST_TRUNC.items():
        v = out.get(k)
        if isinstance(v, str) and len(v) > n:
            out[k] = v[:n] + "…"
            out[k + "_truncated"] = True
    return out


def get_hud_data(project: str = "") -> Dict[str, Any]:
    """The HUD snapshot for a project: the last one at once, refreshed in the background when over 10 s old or stale.
    Only the HUD reads this; routing and budgets read live data."""
    with _s._hud_cache_lock:
        entry = _s._hud_cache.get(project)
        if entry is not None:
            if (entry["stale"] or time.time() - entry["at"] > 10.0) and not entry["busy"]:
                entry["busy"] = True
                threading.Thread(target=_s._bg_refresh_hud, args=(project,), daemon=True).start()
            cached = entry["data"]
        else:
            cached = None
    if cached is not None:
        try:
            return {**cached, "settings": {**(cached.get("settings") or {}), **_fast_settings()}}
        except Exception:
            return cached
    data = _s._compile_hud_data(project)  # First call for this project: build it outside the lock.
    with _s._hud_cache_lock:
        _s._hud_cache[project] = {"data": data, "at": data.get("fetched_at", time.time()), "stale": False, "busy": False}
    return data

def t_hud_status(project: str = "") -> str:
    """Display an ASCII departures-board style dashboard of the Subway transit network."""
    text = _t_hud_status_impl(project)
    # Strip the live clock and lease countdowns: cosmetic churn that isn't a real change.
    sig_text = re.sub(r"\[\d{4}-\d{2}-\d{2}T[\d:.-]+\]", "", text)
    sig_text = re.sub(r"\d+s left", "", sig_text)
    return _s.poll_guard_check("hud_status", project, text, sig_text=sig_text)


def _t_hud_status_impl(project: str = "") -> str:
    data = _s.get_hud_data(project=project)
    now = time.time()
    locks = data.get("locks", {})
    workers = data.get("workers", [])
    history = data.get("history", [])
    tracks = data.get("tracks", {})

    sep = "=" * 78
    subsep = "-" * 78
    lines = [
        sep,
        f"SUBWAY CONTEXT LIVE HUD - STATION DEPARTURE BOARD   [{data['time_iso']}]",
        sep,
        "[WORKSPACE GUARD - ACTIVE FILE LEASES]",
    ]
    if not locks:
        lines.append("  (No files locked.)")
    for p, info in locks.items():
        rem = max(0, int(info.get("expires_at", 0) - now))
        unv = " [unverified]" if info.get("unverified") else ""
        lines.append(f"  [LOCKED] {p} <- {info.get('holder')}{unv} ({info.get('role')}, {rem}s left) :: {info.get('purpose', '')}")

    lines.extend([subsep, "[AUTONOMOUS WORKER SWARM - ACTIVE]"])
    if not workers:
        hist_note = f" ({len(history)} past sessions in archive)" if history else ""
        lines.append(f"  (No active workers.{hist_note})")
    for w in workers[:6]:
        lines.append(f"  [*] {w['id']:<14} [{w.get('provider', '').upper():<11}] [{w.get('status', '').upper()}] "
                     f"Track: {w.get('track', 'main')} Role: {w.get('role')}"
                     f"{' :: ' + str(w.get('status_reason')) if w.get('status_reason') else ''}")

    lines.extend([subsep, "[SHARED CONTEXT TRACKS]"])
    if not tracks:
        lines.append("  (No active tracks. Join or broadcast with join_track() / broadcast_track().)")
    for tname, tinfo in tracks.items():
        last = tinfo.get("last_entry") or {}
        st = last.get('station_id', 'none')
        unv = " [unverified]" if last.get("unverified") else ""
        parent = f" (parent: {last.get('parent')})" if last.get("parent") else ""
        lines.append(f"  [TRACK] '{tname}': {tinfo.get('count', 0)} events. "
                     f"Latest from `{st}`{parent}{unv}: {last.get('message', 'No dispatches yet')}")

    probe = _s._hud_probe(_s.HUD_PORT)
    if probe and probe.get("version") == _s.VERSION:
        hud_line = f"Live Web HUD running at: {_s._hud_url(_s.HUD_PORT)}"
    elif probe:
        hud_line = f"An older HUD (v{probe.get('version', '?')}) holds port {_s.HUD_PORT}. Restart it to update."
    else:
        hud_line = "Live Web HUD is not running. Offer to start it with start_hud()."
    lines.extend([sep, hud_line, sep])
    return "\n".join(lines)


def _get_reply_style_settings() -> dict:
    try:
        import reply_styles
        return reply_styles.settings()
    except Exception:
        return {"default": "concise", "providers": {}}


def _get_governor_settings() -> dict:
    try:
        import governor
        return governor.settings()
    except Exception:
        return {
            "enabled": True,
            "probe_calls_per_turn": 6,
            "providers": {"claude": True, "antigravity": True, "opencode": True, "pi": True},
        }


def _fast_settings(cfg_data=None) -> dict:
    """Settings a click changes: config reads and mode getters only, cheap enough for every /api/status.
    Slow parts (capacity, models, quotas, budgets, rows) stay in the cached snapshot."""
    if cfg_data is None:
        cfg_data = _s._load_json("config.json")
    cfg_data = cfg_data if isinstance(cfg_data, dict) else {}
    return {
        "permission_mode": _s.get_permission_mode(), "terminal_mode": _s.get_terminal_mode(),
        "protected_commands": _s.get_protected_commands(), "protected_exceptions": _s.get_protected_exceptions(),
        "compaction": _s.get_compaction(), "compact_at_tokens": _s.compact_at_tokens(),
        "chat_budget": cfg_data.get("chat_budget_default") or cfg_data.get("chat_budget_tokens", 0),
        "archive": {"compress_days": cfg_data.get("archive_compress_days", "off"),
                    "delete_days": cfg_data.get("archive_delete_days", "off"),
                    "branch_days": cfg_data.get("archive_branch_days", "off"),
                    "choices": ["off", "3", "7", "30", "60", "90"]},
        "wake": {"policy": cfg_data.get("wake_events", "all"), "choices": ["all", "outcomes", "failures", "off"]},
        "principal": {"guard_enabled": bool(cfg_data.get("principal_guard_enabled", True)),
                      "max_reads": max(0, int(cfg_data.get("principal_max_reads", 30) or 0)),
                      "mcp_read_tools": guard.principal_mcp_read_tools(cfg_data)},
        "hud_autostart": bool(cfg_data.get("hud_autostart", True)),
        "hud_open_in": str(cfg_data.get("hud_open_in") or "auto"),
        "hud_open_on_use": bool(cfg_data.get("hud_open_on_use", True)),
        "god_mode": bool(cfg_data.get("god_mode", False)),
        "worker_limits": cfg_data.get("worker_limits", True) is not False,
        "max_workers": cfg_data.get("max_workers"),
        "local_memory_guard": cfg_data.get("local_memory_guard", True) is not False and os.environ.get("SUBWAY_LOCAL_MEMORY_GUARD") != "0",
        "local_concurrency_limits": cfg_data.get("local_concurrency_limits", True) is not False,
        "max_local_workers": cfg_data.get("max_local_workers"),
        "git_branch_mode": bool(cfg_data.get("git_branch_mode", False)),
        "reply_style": _get_reply_style_settings(),
        "token_controls": {
            "read_budget": (cfg_data.get("read_budget") or {"reset": "compaction", "scope": "all"}),
            "nudge_builder_calls": int(cfg_data.get("nudge_builder_calls", 5) or 5),
            "nudge_default_calls": int(cfg_data.get("nudge_default_calls", 7) or 7),
            "no_edit_tool_calls": int(cfg_data.get("no_edit_tool_calls", 12) or 12),
            "governor": (lambda: _get_governor_settings())(),
        },
        "notification_targets": cfg_data.get("notification_targets", ["ide", "desktop"]),
        "notification_webhook": cfg_data.get("notification_webhook", ""),
        "edge_endpoints": cfg_data.get("edge_endpoints", []),
        "caffeinate": cfg_data.get("caffeinate", "off"),
    }


def _live_settings(cfg_data=None, low_limits=None, benchmarks_meta=None, tracks_summary=None, quotas_data=None) -> dict:
    """The settings block, built from cheap live getters so /api/status always shows the current values."""
    if cfg_data is None:
        cfg_data = _s._load_json("config.json")
    if low_limits is None:
        low_limits = []
    if benchmarks_meta is None:
        b_info = _s._load_json("benchmarks.json")
        benchmarks_meta = {"source": b_info.get("source"), "fetched_at": b_info.get("fetched_at"), "error": b_info.get("error")}
    if tracks_summary is None:
        tracks_summary = {}
    if quotas_data is None:
        quotas_data = _s.load_quotas(refresh=not _s._sampler_running)
    slow = {
        "permission_modes": _s.PERMISSION_MODES,
        "protected_defaults": list(terminals.PROTECTED_DEFAULTS),
        "thread_permissions": _s.thread_permission_rows(),
        "capacity": _s.capacity(),
        "models": _s.model_catalog(),
        "benchmarks": benchmarks_meta,
        "chat_budgets": _s.chat_budget_rows(),
        "low_limits": low_limits,
        "budgets": _s.budget_rows(),
    }
    return {**slow, **_fast_settings(cfg_data)}


def patch_cached_worker(event_type: str, data: dict) -> None:
    """Spool listener: patch worker rows in every cached HUD snapshot so /api/status is current immediately."""
    if event_type != "worker" or not isinstance(data, dict) or not data.get("id"):
        return
    wid = data["id"]
    status = data.get("status")
    role = data.get("role")
    provider = data.get("provider")
    with _s._hud_cache_lock:
        for entry in _s._hud_cache.values():
            snap = entry.get("data") or {}
            rows = snap.get("workers")
            if not isinstance(rows, list):
                continue
            row = None
            for r in rows:
                if isinstance(r, dict) and (r.get("id") == wid or r.get("worker_id") == wid):
                    row = r
                    break
            if row is not None:
                if status is not None:
                    row["status"] = status
                if role is not None:
                    row["role"] = role
                if provider is not None:
                    row["provider"] = provider
            elif status in ("queued", "pending", "pending_approval", "running"):
                rows.insert(0, {"id": wid, "status": status, "role": role, "provider": provider})
            entry["stale"] = True
