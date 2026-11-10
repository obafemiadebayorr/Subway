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

import importlib
import inspect
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

import server as _s


def _window(key: str, label: str, used: Optional[float], resets_at: Optional[float]) -> Dict[str, Any]:
    left = None if used is None else max(0.0, round(100.0 - used, 1))
    return {"key": key, "label": label, "left_pct": left, "resets_at": resets_at, "disabled": False, "note": ""}

def _state_of(fetched_at: Optional[float], live_for: float) -> str:
    if not fetched_at:
        return "unavailable"
    return "live" if time.time() - fetched_at <= live_for else "stale"

def _other_sessions(provider: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    cached = _s._other_sessions_cache.get(provider)
    if cached and (now - cached[0] < 60.0):
        return cached[1]
    res = _s._other_sessions_uncached(provider, now)
    _s._other_sessions_cache[provider] = (now, res)
    return res

def _other_sessions_for_hud(provider: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    cached = _s._other_sessions_cache.get(provider)
    if cached and now - cached[0] < 60.0:
        return cached[1]
    with _s._other_sessions_refresh_lock:
        if provider not in _s._other_sessions_refreshing:
            _s._other_sessions_refreshing.add(provider)

            def refresh() -> None:
                try:
                    _s._other_sessions(provider)
                finally:
                    with _s._other_sessions_refresh_lock:
                        _s._other_sessions_refreshing.discard(provider)

            threading.Thread(target=refresh, daemon=True).start()
    return cached[1] if cached else None

def _source_record_age(rec: Dict[str, Any]) -> Optional[float]:
    fetched_at = rec.get("fetched_at")
    if rec.get("stale") and isinstance(fetched_at, (int, float)):
        return max(0.0, round(time.time() - fetched_at, 1))
    return rec.get("age")

def _other_sessions_uncached(provider: str, now: float) -> Optional[Dict[str, Any]]:
    worker_ids = set()
    for w in _s._all_workers():
        cid = w.get("conversation") or w.get("thread")
        if cid:
            worker_ids.add(str(cid))

    cutoff_5h = now - _s.FIVE_H
    sessions = 0
    tokens_5h = 0

    if provider == "claude":
        for path in _s.claude_files(True):
            try:
                if os.path.getmtime(path) < cutoff_5h:
                    continue
            except OSError:
                continue
            base = os.path.splitext(os.path.basename(path))[0]
            if base in worker_ids:
                continue
            usage_events = _s._parse_claude_usage(path)
            recent_tokens = sum(inp + out for ts, inp, out, _ in usage_events if ts >= cutoff_5h)
            sessions += 1
            tokens_5h += recent_tokens
        return {"sessions": sessions, "tokens_5h": tokens_5h}

    elif provider == "antigravity":
        roots = [_s.AGY_DIR]
        parent = os.path.dirname(_s.AGY_DIR)
        cli_dir = os.path.join(parent, "antigravity-cli")
        if os.path.abspath(cli_dir) != os.path.abspath(_s.AGY_DIR):
            roots.append(cli_dir)

        seen_paths = set()
        for root in roots:
            pattern = os.path.join(root, "brain", "*", ".system_generated", "logs", "transcript.jsonl")
            for path in glob.glob(pattern):
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                try:
                    if os.path.getmtime(path) < cutoff_5h:
                        continue
                except OSError:
                    continue
                parts = path.split(os.sep)
                try:
                    brain_idx = parts.index("brain")
                    cid = parts[brain_idx + 1]
                except (ValueError, IndexError):
                    cid = ""
                if cid in worker_ids:
                    continue
                usage_events = _s._parse_agy_usage(path)
                recent_tokens = sum(inp + out for ts, inp, out, _ in usage_events if ts >= cutoff_5h)
                sessions += 1
                tokens_5h += recent_tokens
        return {"sessions": sessions, "tokens_5h": tokens_5h}

    elif provider == "opencode":
        con = _s._opencode_db()
        if con is None:
            return None
        try:
            normalized_updated = (
                "case when time_updated > 100000000000 then time_updated / 1000.0 else time_updated end"
            )
            rows = con.execute(
                "select id, coalesce(tokens_input, 0) + coalesce(tokens_output, 0) "
                f"from session_v2 where {normalized_updated} >= ?",
                (time.time() - cutoff_5h,),
            ).fetchall()
            for sid, tok in rows:
                if str(sid) in worker_ids:
                    continue
                sessions += 1
                tokens_5h += int(tok or 0)
            return {"sessions": sessions, "tokens_5h": tokens_5h}
        except Exception:
            return None
        finally:
            con.close()

    return None

def _claude_view(client: Dict[str, Any], refresh: bool) -> Dict[str, Any]:
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)
    try:
        f_cached = pool.submit(_s._source, "claude_cached", 3.0 if refresh else None)
        f_live = pool.submit(_s._source, "claude_live", 120.0 if refresh else None)
        f_tokens = pool.submit(_s._source, "claude_tokens", 15.0 if refresh else None)
        cached_rec = f_cached.result()
        live_rec = f_live.result()
        tokens_rec = f_tokens.result()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    other_sessions = _s._other_sessions_for_hud("claude")

    cached = cached_rec.get("data") or {}
    live = live_rec.get("data") or {}
    live_at = live_rec.get("fetched_at")
    cache_at = cached.get("fetched_at")
    use_live = bool(live) and (not cache_at or (live_at or 0) >= cache_at)
    primary, fallback = (live, cached) if use_live else (cached, live)
    fetched_at = live_at if use_live else cache_at

    windows = []
    for key, wk, label in (("five_hour", "5h", "5-hour session"), ("seven_day", "weekly", "Weekly")):
        w = primary.get(key) or fallback.get(key) or {}
        reset = w.get("resets_at") or (fallback.get(key) or {}).get("resets_at")
        windows.append(_s._window(wk, label, w.get("used_pct"), reset))

    is_loading = bool(live_rec.get("loading"))
    is_stale = bool(live_rec.get("stale"))
    age = _s._source_record_age(live_rec)
    state = "loading" if is_loading else ("stale" if is_stale else _s._state_of(fetched_at, _s.LIVE_SECONDS["claude"]))
    source = "Claude Code /usage" if use_live else "Claude Code cache (~/.claude.json)"
    error = live_rec.get("error") if (not use_live and not is_loading and not is_stale) else None
    return {"plan": cached.get("plan") or "", "state": state, "source": source, "fetched_at": fetched_at,
            "latency_ms": live_rec.get("latency_ms") if use_live else cached_rec.get("latency_ms"),
            "pools": [{"key": "claude", "name": "Subscription", "windows": windows}] if fetched_at else [],
            "tokens": tokens_rec.get("data"),
            "other_sessions": other_sessions,
            "error": error,
            "note": "" if (fetched_at or is_loading) else "Claude Code has not reported usage yet.",
            "loading": is_loading, "stale": is_stale, "age": age}

def _agy_view(client: Dict[str, Any], refresh: bool) -> Dict[str, Any]:
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        f_rec = pool.submit(_s._source, "agy_limits", 10.0 if refresh else None)
        f_tokens = pool.submit(_s._source, "agy_tokens", 15.0 if refresh else None)
        rec = f_rec.result()
        tokens_rec = f_tokens.result()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    other_sessions = _s._other_sessions_for_hud("antigravity")
    data = rec.get("data") or {}
    tokens = tokens_rec.get("data")
    is_loading = bool(rec.get("loading"))
    is_stale = bool(rec.get("stale"))
    age = _s._source_record_age(rec)
    state = "loading" if is_loading else ("stale" if is_stale else _s._state_of(rec.get("fetched_at"), _s.LIVE_SECONDS["antigravity"]))
    note = ""
    if state == "stale":
        note = "Antigravity isn't answering, so these are the last known limits."
    elif state == "unavailable" and not is_loading:
        note = "Open Antigravity to read its limits."
    return {"plan": data.get("plan") or "", "state": state, "source": "Antigravity language server",
            "fetched_at": rec.get("fetched_at"), "latency_ms": rec.get("latency_ms"),
            "pools": data.get("pools") or [], "tokens": tokens, "other_sessions": other_sessions,
            "error": rec.get("error") if not (is_loading or is_stale) else None, "note": note,
            "loading": is_loading, "stale": is_stale, "age": age}

def _copilot_view(client: Dict[str, Any], refresh: bool) -> Dict[str, Any]:
    raw = _s._copilot_raw()
    models = _s._copilot_models()
    tokens = _s._copilot_cli_usage()
    source_label = "Tokens from Copilot CLI sessions on this Mac; VS Code chat use is counted only in the chat allowance."
    no_models_note = "Open Copilot Chat in VS Code once to list its models."
    if not raw or not isinstance(raw, dict):
        base_note = "Copilot's allowance could not be read (is gh signed in?); workers still run through the Copilot CLI when it is installed."
        note = f"{base_note} {no_models_note}" if not models else base_note
        client["dispatchable"] = bool(client.get("dispatchable")) and bool(_s.find_copilot_binary())
        return {
            "plan": "", "state": "live" if tokens else "detected", "source": source_label,
            "fetched_at": time.time() if tokens else None, "latency_ms": None,
            "pools": [], "tokens": tokens, "credits": tokens.get("credits") if tokens else None,
            "per_model": tokens.get("per_model") if tokens else {}, "error": None, "models": models,
            "note": note
        }

    plan = raw.get("copilot_plan") or ""
    quota_reset = _s._parse_ts(raw.get("quota_reset_date"))  # "2026-12-01" -> seconds, like every other gauge.
    snapshots = raw.get("quota_snapshots") or {}
    chat = snapshots.get("chat") or {}
    unlimited = bool(chat.get("unlimited"))
    rem = chat.get("remaining")
    pct = chat.get("percent_remaining")
    chat_known = bool(chat) and (rem is not None or pct is not None)
    # Only a chat pool known to be empty blocks dispatch; a missing or dataless snapshot is unknown, not exhausted.
    chat_ok = unlimited or not chat_known or (rem is not None and rem > 0) or (pct is not None and pct > 0)
    client["dispatchable"] = bool(client.get("dispatchable")) and chat_ok
    name_map = {
        "chat": "Chat",
        "completions": "Completions (inline editor suggestions Subway shows but never spends)",
        "premium_interactions": "Premium requests"
    }

    pools = []
    for snap_key, pool_name in name_map.items():
        if snap_key not in snapshots:
            continue
        snap = snapshots[snap_key]
        entitlement = snap.get("entitlement", 0)
        unlimited = snap.get("unlimited", False)
        if entitlement == 0 and not unlimited:
            continue

        pct = snap.get("percent_remaining")
        used_pct = None if pct is None else round(100.0 - pct, 2)
        window = {
            "key": "monthly",
            "label": "Monthly",
            "used_pct": used_pct,
            "left_pct": pct,
            "resets_at": quota_reset,
            "disabled": False,
            "note": "Not applied" if unlimited else ""
        }
        pools.append({"key": snap_key, "name": pool_name, "windows": [window]})

    return {
        "plan": plan, "state": "live", "source": source_label,
        "fetched_at": time.time(), "latency_ms": None,
        "pools": pools, "tokens": tokens, "credits": tokens.get("credits") if tokens else None,
        "per_model": tokens.get("per_model") if tokens else {}, "other_sessions": _s._other_sessions("copilot"),
        "error": None, "models": models, "note": no_models_note if not models else ""
    }

def _kimi_view(client: Dict[str, Any], refresh: bool) -> Dict[str, Any]:
    try:
        raw = _s._kimi_raw()
        if raw is None or not isinstance(raw, dict):
            return {
                "plan": "", "state": "detected", "source": "", "fetched_at": None, "latency_ms": None,
                "pools": [], "tokens": None, "error": None,
                "note": "Open Kimi Code to refresh your sign-in."
            }
        usages = raw.get("usages") or {}
        pools = []
        for key, name in (("limit_5h", "5-hour"), ("limit_7d", "Weekly"),
                          ("limit_month_total", "Monthly total"), ("limit_month_code", "Monthly code")):
            window = usages.get(key)
            if not isinstance(window, dict) or window.get("used_ratio") is None:
                continue
            left_pct = round((1.0 - window["used_ratio"]) * 100.0, 2)
            resets_at = _s._parse_ts(window.get("reset_time"))  # Handles "Z", which Python 3.9 can't parse alone.
            pools.append({"key": key, "name": name, "windows": [{
                "key": "rolling", "label": name, "used_pct": round(100.0 - left_pct, 2),
                "left_pct": left_pct, "resets_at": resets_at, "disabled": False, "note": ""}]})
        return {
            "plan": "Kimi Code", "state": "live", "source": "Kimi usages endpoint (undocumented, api.kimi.com)",
            "fetched_at": time.time(), "latency_ms": None,
            "pools": pools, "tokens": None, "other_sessions": _s._other_sessions("kimi"),
            "error": None,
            "note": "" if pools else "Signed in. Kimi reported no plan limits for this account."
        }
    except Exception:
        return {
            "plan": "", "state": "detected", "source": "", "fetched_at": None, "latency_ms": None,
            "pools": [], "tokens": None, "error": None,
            "note": "Doesn't expose usage locally, and Subway can't dispatch workers to it yet."
        }

def _opencode_view(client: Dict[str, Any], refresh: bool) -> Dict[str, Any]:
    rec = _s._source("opencode", 5.0 if refresh else None)
    data = rec.get("data") or {}
    is_loading = bool(rec.get("loading"))
    is_stale = bool(rec.get("stale"))
    age = _s._source_record_age(rec)
    plan = "Signed-in provider" if data.get("signed_in") else "Free models, no account" if data else ""
    hit = data.get("limit_hit")
    note = "OpenCode doesn't publish a remaining free allowance, so limits show only when one is hit."
    if hit and (time.time() - hit.get("at", 0) <= 86400):
        model_name = hit.get("model") or "A free model"  # Never the last-used model: that may be local, with no limit.
        note = f"{model_name} hit its free limit at {_s.fmt_ts(hit['at'])}; other free models may still run"
    state = "loading" if is_loading else ("stale" if is_stale else ("live" if data else "unavailable"))
    return {"plan": plan, "state": state, "source": "OpenCode database", "limit_hit": hit,
            "limited": data.get("limited") or {}, "per_model": data.get("per_model") or {},
            "fetched_at": rec.get("fetched_at"), "latency_ms": rec.get("latency_ms"),
            "pools": [], "tokens": data.get("tokens"), "other_sessions": _s._other_sessions_for_hud("opencode"),
            "error": rec.get("error") if not (is_loading or is_stale) else None, "model": data.get("model", ""),
            "cost": data.get("cost"), "note": note if not is_loading else "",
            "loading": is_loading, "stale": is_stale, "age": age}

def _local_view(client: Dict[str, Any], refresh: bool) -> Dict[str, Any]:
    rec = _s._source("local", 5.0 if refresh else None)
    data = rec.get("data") or {}
    is_loading = bool(rec.get("loading"))
    is_stale = bool(rec.get("stale"))
    age = rec.get("age")
    running = bool(data.get("running"))
    client["dispatchable"] = client["dispatchable"] and running
    room = max(0, _s.local_room(data)) if data.get("memory") else 0
    models = [dict(m, fits=_s.local_fit(m["name"], data)[0]) for m in data.get("models") or []]
    note = "No session or weekly limit. Memory is the limit." if running else "Start Ollama to use local models."
    state = "loading" if is_loading else ("stale" if is_stale else ("live" if running else "unavailable"))
    return {"plan": (data.get("profile") or {}).get("tier", ""), "state": state,
            "source": "Ollama", "fetched_at": rec.get("fetched_at"), "latency_ms": rec.get("latency_ms"), "pools": [], "tokens": None,
            "error": rec.get("error") if not (is_loading or is_stale) else None, "note": note if not is_loading else "",
            "memory": data.get("memory"), "room": room, "models": models,
            "loaded": data.get("loaded") or [], "profile": data.get("profile"),
            "loading": is_loading, "stale": is_stale, "age": age}


def _next_local_midnight(now=None):
    now = time.time() if now is None else now
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, 0, 0, 0, 0, 0, -1))


_PI_SOURCES = (("usage.pi_theos_api", "theos_live", "live from DeepAI"),
               ("usage.pi_theos_local", "theos_local", "from the DeepAI app"))


def _pi_allowance(theos):
    """Today's Theos allowance: live API, then the local DeepAI app, then the user's own figure.
    Always says which one answered, so a hand-set number is never shown as live."""
    for modname, fn, label in _PI_SOURCES:
        try:
            got = getattr(importlib.import_module(modname), fn)()
        except Exception:
            got = None
        if isinstance(got, dict) and got.get("allowance"):
            return {**got, "label": label}
    manual = ((_s._load_json("quotas.json") or {}).get("manual") or {}).get("pi") or {}
    daily = manual.get("daily_theos")
    if daily:
        return {"allowance": int(daily), "remaining": None, "spent": (theos or {}).get("spend_today"),
                "refresh_at": _next_local_midnight(), "source": "manual", "label": "set by hand",
                "fetched_at": manual.get("set_at")}
    return None


def _pi_theos_pool(info, theos):
    allowance = int(info["allowance"])
    spent = info.get("spent")
    if spent is None and info.get("remaining") is not None:
        spent = allowance - int(info["remaining"])
    if spent is None:
        spent = (theos or {}).get("spend_today")
    used = None if spent is None else round(min(100.0, max(0.0, float(spent) / allowance * 100.0)), 2)
    return {"key": "theos", "name": "Daily Theos", "windows": [{
        "key": "daily", "label": f"Daily Theos · {info.get('label', '')}".rstrip(" ·"),
        "used_pct": used, "left_pct": None if used is None else round(100.0 - used, 2),
        "resets_at": info.get("refresh_at"), "disabled": False,
        "note": "" if spent is None else f"{int(spent)} of {allowance} Theos"}]}


def _pi_view(client: Dict[str, Any], refresh: bool) -> Dict[str, Any]:
    """DeepAI Code: subscription with no published quota API. Report real token
    usage from session transcripts; never invent a percentage. Theoretical
    allocation comes from set_quota (manual) like any other provider."""
    rec = _s._source("pi_tokens", 60.0 if refresh else None) if "pi_tokens" in getattr(_s, "_SOURCES", {}) else {}
    data = rec.get("data") or {}
    is_loading = bool(rec.get("loading"))
    is_stale = bool(rec.get("stale"))
    age = rec.get("age")
    per_model = data.get("per_model") or {}
    models = [{"model": k, **(v if isinstance(v, dict) else {})} for k, v in sorted(per_model.items())]
    total = int(data.get("total") or 0)
    note = "Usage measured from DeepAI session transcripts."
    if not data:
        note = "No DeepAI session usage recorded in the last 5 hours."
    state = "loading" if is_loading else ("stale" if is_stale else ("live" if data else "detected"))
    # Theos is the real DeepAI allowance: measured spend, never a percentage.
    theos: Dict[str, Any] = {}
    try:
        measured = _s.pi_theos()
        if isinstance(measured, dict):
            theos = measured
    except Exception:
        theos = {}
    tokens = None
    if data:
        tokens = {
            "total": total,
            "hourly": data.get("hourly") or [0] * 24,
            "5h": data.get("5h") or {"input": 0, "output": 0, "cache_read": 0},
            "7d": data.get("7d") or {"input": 0, "output": 0, "cache_read": 0},
            "theos": theos,
        }
    allowance_info = _pi_allowance(theos)
    pools = [_pi_theos_pool(allowance_info, theos)] if allowance_info else []
    if not allowance_info and not is_loading:
        note = "Set a daily Theos allowance or sign in to DeepAI Code."
    return {"plan": "subscription", "state": state,
            "source": (f"Theos {allowance_info['label']}" if allowance_info else "pi session transcripts"),
            "theos": theos,
            "fetched_at": rec.get("fetched_at"), "latency_ms": rec.get("latency_ms"),
            "pools": pools, "limit_source": (allowance_info or {}).get("source"),
            "tokens": tokens, "error": rec.get("error"),
            "note": note if not is_loading else "", "models": models,
            "per_model": per_model, "sessions_active": data.get("sessions_active"),
            "loading": is_loading, "stale": is_stale, "age": age}


def discover_connected_providers(refresh: bool = True) -> List[Dict[str, Any]]:
    """Every LLM client found on this machine, with what's verified about it."""
    clients = _s.detect_clients()
    names = {"claude": "Claude Code", "antigravity": "Antigravity", "opencode": "OpenCode", "local": "Local models",
             "kimi": "Kimi", "copilot": "GitHub Copilot", "pi": "DeepAI Code", "cursor": "Cursor", "devin": "Devin"}
    views: Dict[str, Callable[[Dict[str, Any], bool], Dict[str, Any]]] = {
        "claude": _s._claude_view, "antigravity": _s._agy_view, "opencode": _s._opencode_view, "local": _s._local_view,
        "kimi": _s._kimi_view, "copilot": _s._copilot_view, "pi": _s._pi_view}
    manual = _s._load_json("quotas.json").get("manual") or {}

    custom_providers = _s.load_custom_providers()
    custom_ids = {p["id"] for p in custom_providers}
    for cp in custom_providers:
        names[cp["id"]] = cp.get("name") or cp["id"]

    keys = ["claude", "antigravity", "opencode", "local", "kimi", "copilot", "pi", "cursor", "devin"]
    for cid in custom_ids:
        if cid not in keys:
            keys.append(cid)

    def _build_provider_view(key: str) -> Optional[Dict[str, Any]]:
        client = clients.get(key)
        if not client or not client["installed"]:
            return None
        view: Dict[str, Any]
        if key in custom_ids:
            view = {
                "plan": "", "state": "detected", "source": "", "fetched_at": None, "latency_ms": None,
                "pools": [], "tokens": None, "error": None,
                "note": "Usage and the permission relay aren't available."
            }
        elif key in views or key == "copilot":
            view = dict((_s._copilot_view if key == "copilot" else views[key])(client, refresh))
            override = manual.get(key)
            if override and not view.get("pools"):
                view["pools"] = [{"key": "manual", "name": "Set by hand", "windows": [
                    {"key": "5h", "label": "5-hour session", "left_pct": override.get("session_5h_left_pct"),
                     "resets_at": None, "disabled": False, "note": ""},
                    {"key": "weekly", "label": "Weekly", "left_pct": override.get("weekly_left_pct"),
                     "resets_at": None, "disabled": False, "note": ""}]}]
                view["source"] = f"set_quota at {_s.fmt_ts(override.get('set_at'))}"
                view["manual"] = True
        else:
            view = {
                "plan": "", "state": "detected", "source": "", "fetched_at": None, "latency_ms": None,
                "pools": [], "tokens": None, "error": None,
                "note": "Doesn't expose usage locally, and Subway can't dispatch workers to it yet."}
            override = manual.get(key)
            if override and not view.get("pools"):
                view["pools"] = [{"key": "manual", "name": "Set by hand", "windows": [
                    {"key": "5h", "label": "5-hour session", "left_pct": override.get("session_5h_left_pct"),
                     "resets_at": None, "disabled": False, "note": ""},
                    {"key": "weekly", "label": "Weekly", "left_pct": override.get("weekly_left_pct"),
                     "resets_at": None, "disabled": False, "note": ""}]}]
                view["source"] = f"set_quota at {_s.fmt_ts(override.get('set_at'))}"
                view["manual"] = True
        view.update({"id": key, "category": key, "name": names.get(key, key), "client": client})
        return view

    installed_keys = [k for k in keys if clients.get(k) and clients[k]["installed"]]
    providers: List[Dict[str, Any]] = []
    if refresh and len(installed_keys) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(installed_keys), 6)) as pool:
            futures = {pool.submit(_build_provider_view, k): k for k in installed_keys}
            res_map: Dict[str, Optional[Dict[str, Any]]] = {}
            for fut in concurrent.futures.as_completed(futures):
                k = futures[fut]
                try:
                    res_map[k] = fut.result()
                except Exception:
                    res_map[k] = None
            raw_collected = [res_map.get(k) for k in installed_keys]
            providers = [p for p in raw_collected if p is not None]
    else:
        raw_providers = [_build_provider_view(k) for k in installed_keys]
        providers = [p for p in raw_providers if p is not None]
    return providers
