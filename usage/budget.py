"""Usage package: budget submodule (split from usage.py)."""

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
sys.modules.setdefault("server", sys.modules[__name__])  # one module object when run as a script
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
import threads

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

import server as _s


def _chat_budget_cfg(cfg: Dict[str, Any]) -> Tuple[Dict[str, int], Optional[int]]:
    """(per-chat budgets, universal default) from a loaded config, honoring the legacy global key."""
    chat_budgets = cfg.get("chat_budgets") or {}
    default = cfg.get("chat_budget_default")
    if default is None:
        default = cfg.get("chat_budget_tokens")
    return chat_budgets, (int(default) if default else None)


def chat_budget_for(session_id: Optional[str], cfg: Optional[Dict[str, Any]] = None) -> Tuple[Optional[int], Optional[str]]:
    """This chat's own budget if set, else the universal default, else (None, None). Scope is 'chat' or 'universal'."""
    cfg = cfg if cfg is not None else _s._load_json("config.json")
    chat_budgets, default = _s._chat_budget_cfg(cfg)
    if session_id and session_id in chat_budgets:
        v = int(chat_budgets[session_id] or 0)
        if v > 0:
            return v, "chat"
    if default:
        return default, "universal"
    return None, None


def chat_budget_rows(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One row per chat with its own budget, for the HUD's per-chat budget list."""
    cfg = cfg if cfg is not None else _s._load_json("config.json")
    chat_budgets, _default = _s._chat_budget_cfg(cfg)
    if not chat_budgets:
        return {"chats": []}
    titles = {}
    with contextlib.suppress(Exception):
        titles = {c["id"]: c.get("title") for c in _s.all_chats("all") if c.get("id")}
    rows = []
    for session_id, limit in chat_budgets.items():
        limit = int(limit or 0)
        if limit <= 0:
            continue
        used = _s.chat_tokens_used(session_id) or 0
        pct = round((used / limit) * 100, 1) if limit > 0 else 0.0
        status = "reached" if used >= limit else "warning" if used >= 0.8 * limit else "ok"
        rows.append({
            "session_id": session_id,
            "label": titles.get(session_id) or _s._short_id(session_id),
            "used": used,
            "limit": limit,
            "pct": pct,
            "status": status,
        })
    rows.sort(key=lambda r: r["pct"], reverse=True)
    return {"chats": rows}


def set_budget(scope: str, tokens: int, session_id: Optional[str] = None) -> str:
    tokens = max(0, int(tokens))
    scope = (scope or "").lower().strip()
    with _s._state_lock("config"):
        data = _s._load_json("config.json")
        legacy = data.pop("chat_budget_tokens", None)
        if legacy and "chat_budget_default" not in data:
            data["chat_budget_default"] = legacy
        if scope == "chat":
            if session_id:
                chat_budgets = data.setdefault("chat_budgets", {})
                if tokens > 0:
                    chat_budgets[session_id] = tokens
                else:
                    chat_budgets.pop(session_id, None)
            else:
                if tokens > 0:
                    data["chat_budget_default"] = tokens
                else:
                    data.pop("chat_budget_default", None)
        else:
            budgets = data.setdefault("budgets", {})
            if tokens > 0:
                budgets[scope] = tokens
            else:
                budgets.pop(scope, None)
        _s._save_json("config.json", data)
    label = scope if scope != "chat" else ("this chat's" if session_id else "the universal chat")
    return f"[BUDGET] Set {label} budget to {tokens:,} tokens." if tokens > 0 else f"[BUDGET] Cleared {label} budget."


def set_chat_budget(tokens: int, session_id: Optional[str] = None) -> str:
    return _s.set_budget("chat", tokens, session_id=session_id)


def budget_state() -> List[Dict[str, Any]]:
    cfg = _s._load_json("config.json")
    res = []
    sid = _s._self_id() if "_self_id" in vars(_s) else None
    chat_limit, _scope = _s.chat_budget_for(sid, cfg)
    if chat_limit:
        limit = int(chat_limit)
        used = _s.chat_tokens_used(sid) or 0
        pct = round((used / limit) * 100, 1) if limit > 0 else 0.0
        status = "reached" if (limit > 0 and used >= limit) else "warning" if (limit > 0 and used >= 0.8 * limit) else "ok"
        res.append({
            "scope": "chat",
            "used": used,
            "limit": limit,
            "pct": pct,
            "status": status,
        })
    budgets = cfg.get("budgets") or {}
    for prov, b_limit in budgets.items():
        limit = int(b_limit or 0)
        if limit <= 0:
            continue
        used = _s._provider_5h_tokens(prov)
        pct = round((used / limit) * 100, 1) if limit > 0 else 0.0
        status = "reached" if used >= limit else "warning" if used >= 0.8 * limit else "ok"
        res.append({
            "scope": prov,
            "used": used,
            "limit": limit,
            "pct": pct,
            "status": status,
        })
    return res


def _cached_provider_5h_tokens(scope: str) -> int:
    now = time.time()
    hit = _s._provider_5h_cache.get(scope)
    if hit and (now - hit[0]) < 30:
        return hit[1]
    val = _s._provider_5h_tokens(scope)
    _s._provider_5h_cache[scope] = (now, val)
    return val


def _gauge_scopes() -> List[str]:
    """Providers with a meter: a numeric left_pct, or a live/stale reading on this machine.
    Providers only 'detected' (installed, no access: kimi, cursor, devin) or erroring are left out."""
    out: List[str] = []
    with contextlib.suppress(Exception):
        for p in _s.load_quotas(refresh=False).get("providers") or []:
            if not isinstance(p, dict) or p.get("error") or not p.get("id"):
                continue
            gauged = any(isinstance(w, dict) and isinstance(w.get("left_pct"), (int, float))
                         for pl in (p.get("pools") or []) if isinstance(pl, dict)
                         for w in (pl.get("windows") or []))
            if gauged or p.get("state") in ("live", "stale"):
                pid = str(p["id"])
                if pid not in out:
                    out.append(pid)
    return out


def budget_rows() -> List[Dict[str, Any]]:
    """budget_state plus an empty row for each scope with no budget, so the HUD can set the first one."""
    rows = _s.budget_state()
    chat_used = _s.chat_tokens_used()
    chat_detected = chat_used is not None
    if not chat_detected:
        newest_tokens = _s._newest_claude_transcript_tokens()
        for r in rows:
            if r.get("scope") == "chat":
                r["used"] = newest_tokens
                if r.get("limit", 0) > 0 and newest_tokens is not None:
                    r["pct"] = round((r["used"] / r["limit"]) * 100, 1)
                    r["status"] = "reached" if r["used"] >= r["limit"] else "warning" if r["used"] >= 0.8 * r["limit"] else "ok"

    have = {r["scope"] for r in rows}
    scopes = ["chat"] + _gauge_scopes()

    extra = []
    for x in scopes:
        if x in have:
            continue
        if x == "chat":
            used = chat_used if chat_detected else _s._newest_claude_transcript_tokens()
        else:
            used = _s._cached_provider_5h_tokens(x)
        extra.append({"scope": x, "used": used or 0, "limit": 0, "pct": 0.0, "status": "ok"})
    return rows + extra


def budget_ping_notice(scope: str, used: int, limit: int, label: Optional[str] = None,
                        chat_id: Optional[str] = None, subject_id: Optional[str] = None,
                        window: Optional[str] = None, reset_at: Any = None,
                        desktop: bool = False) -> str:
    if not limit or limit <= 0:
        return ""
    pct = (used / limit) * 100
    if pct < 80 or pct >= 100:
        return ""
    level = 90 if pct >= 90 else 80
    if scope == "chat":
        subject = subject_id or chat_id or _s._self_id() or "unknown-chat"
        key = f"budget_warning|chat|{subject}|{limit}|{level}"
    else:
        key = f"budget_warning|provider|{scope}|{window or '5h'}|{reset_at or 'unknown'}|{limit}|{level}"
    label = label or scope
    cmd = f"subway budget {scope} <tokens>" + (f" --chat {chat_id}" if scope == "chat" and chat_id else "")
    message = (f"[BUDGET] Warning: {label} budget at {pct:.1f}% ({used:,} of {limit:,} tokens). "
               f"Add more with: {cmd}, or in the HUD.")
    if desktop:
        return message if _s.notify_desktop_event(key, "Subway budget warning", message, route="overview") else ""
    now = time.time()
    with _s._state_lock("pings"):
        sent = _s._load_json("limit_pings.json")
        if key in sent:
            return ""
        sent[key] = now
        _s._save_json("limit_pings.json", {k: v for k, v in sent.items() if now - v < 8 * 86400})
    return message


def _provider_budget_reset(provider_id: str) -> Optional[float]:
    for provider in _s.discover_connected_providers(refresh=False):
        if provider.get("id") != provider_id:
            continue
        for pool in provider.get("pools") or []:
            for window in pool.get("windows") or []:
                key = str(window.get("key") or "").lower()
                label = str(window.get("label") or "").lower()
                if key in ("5h", "five_hour") or "5-hour" in label:
                    reset = window.get("resets_at")
                    try:
                        return float(reset) if reset is not None else None
                    except (TypeError, ValueError):
                        return None
    return None


def enforce_budgets() -> None:
    try:
        cfg = _s._load_json("config.json")
        budgets = cfg.get("budgets") or {}
        custom_ids = {p.get("id") for p in _s.load_custom_providers()} if "load_custom_providers" in vars(_s) else set()
        for w in _s._all_workers():
            if w.get("status") != "running":
                continue
            prov = (w.get("provider") or "").lower().strip()
            if prov in budgets:
                p_limit = int(budgets[prov] or 0)
                if p_limit > 0 and _s._provider_5h_tokens(prov) >= p_limit:
                    _s.t_stop_worker(w["id"], reason="budget")
                    continue
            w_budget = w.get("budget") or {}
            max_sec = int(w_budget.get("max_seconds") or 0)
            if max_sec > 0 and (prov in custom_ids or prov not in ("claude", "antigravity", "opencode", "local")):
                st = 0
                s_at = w.get("started_at")
                if s_at:
                    if isinstance(s_at, (int, float)):
                        st = float(s_at)
                    else:
                        try:
                            st = _dt.datetime.fromisoformat(s_at).timestamp()
                        except Exception:
                            st = 0
                if st > 0 and (time.time() - st) >= max_sec:
                    _s.t_stop_worker(w["id"], reason="budget")
                    continue
    except Exception as e:
        _s.log("enforce_budgets error:", e)


def check_budget_preflight(model: str = "", provider: str = "auto",
                           budget_session_pct: Optional[float] = None,
                           budget_weekly_pct: Optional[float] = None,
                           max_budget_usd: Optional[float] = None) -> Tuple[bool, str]:
    """Check one concrete agent against its own real limits. Unknown stays unknown, never invented."""
    cfg = _s._load_json("config.json")
    budgets = cfg.get("budgets") or {}
    if provider in budgets:
        p_limit = int(budgets[provider] or 0)
        if p_limit > 0:
            p_used = _s._provider_5h_tokens(provider)
            if p_used >= p_limit:
                return False, f"[BUDGET GUARD VIOLATION] Provider '{provider}' budget reached ({p_used:,} of {p_limit:,} tokens)."
    if provider == "local":
        model_name = model or _s.choose_local_model(need_tools=False) or ""
        if not model_name:
            return False, "[MEMORY GUARD] No installed local model fits in the memory that's free right now."
        ok, msg = _s.local_fit(model_name)
        return ok, msg if ok else f"[MEMORY GUARD] {msg}"
    if max_budget_usd is not None and provider not in ("claude", "copilot"):
        return False, (f"[BUDGET GUARD ALERT] {provider} can't enforce a USD cap (max_budget_usd). "
                       f"Remove the cap or use provider='claude' or 'copilot'.")
    record = next((p for p in _s.discover_connected_providers() if p["id"] == provider), None)
    if record is None:
        return True, f"Budget unknown: {provider} isn't detected on this machine."
    if provider == "copilot":
        raw = _s._copilot_raw()
        if isinstance(raw, dict):
            snaps = raw.get("quota_snapshots") or {}
            chat = snaps.get("chat") or {}
            unlimited = bool(chat.get("unlimited"))
            rem = chat.get("remaining")
            ent = chat.get("entitlement")
            reset_date = raw.get("quota_reset_date") or ""
            pct = chat.get("percent_remaining")
            if not unlimited and rem is not None and rem <= 0:
                return False, f"[BUDGET GUARD VIOLATION] Copilot chat pool exhausted: 0 of {ent} remaining (resets {reset_date})."
            if budget_weekly_pct is not None and pct is not None and pct < budget_weekly_pct:
                return False, f"[BUDGET GUARD ALERT] Requested task weekly budget cap ({budget_weekly_pct}%) exceeds available chat allowance ({pct}% left)."
            if budget_session_pct is not None and pct is not None and pct < budget_session_pct:
                return False, f"[BUDGET GUARD ALERT] Requested task 5h budget cap ({budget_session_pct}%) exceeds available chat allowance ({pct}% left)."
            if unlimited:
                reset_info = f" (resets {reset_date})" if reset_date else ""
                return True, f"Budget approved: Copilot chat pool is unlimited{reset_info}."
            if rem is not None and ent is not None:
                reset_info = f" (resets {reset_date})" if reset_date else ""
                return True, f"Budget approved: Copilot chat pool has {rem} of {ent} remaining{reset_info}."
    limited = (record.get("limited") or {}).get(model) if provider == "opencode" else None
    if limited and limited.get("recent"):
        return False, (f"[BUDGET GUARD VIOLATION] {model} is at its free limit, and OpenCode retries around "
                       f"{_s.fmt_ts(limited['retry_at'])}. Try another free model.")
    pool = _s._select_pool(record, model)
    if pool is None:
        return True, f"Budget unknown: {record['name']} doesn't report limits. Running without a limit check."

    label = record["name"] if pool["name"] in ("Subscription", "Set by hand") else f"{record['name']} / {pool['name']}"
    weekly = next((w for w in pool["windows"] if w["key"] == "weekly"), None)
    five = next((w for w in pool["windows"] if w["key"] == "5h"), None)

    def left(w: Optional[Dict[str, Any]]) -> Optional[float]:
        if not w or w.get("disabled") or w.get("left_pct") is None:
            return None
        if w.get("resets_at") and w["resets_at"] <= time.time():
            return None  # The window has already rolled over since this reading.
        return float(w["left_pct"])

    w_left, s_left = left(weekly), left(five)
    fresh = "" if record["state"] == "live" else f" (reading is {record['state']})"

    if w_left is not None and w_left <= 0:
        return False, (f"[BUDGET GUARD VIOLATION] '{label}' has 0% weekly allowance left "
                       f"(resets in {_s._fmt_reset(weekly['resets_at'])}). Cannot launch model '{model or label}'.")
    if s_left is not None and s_left <= 0:
        return False, (f"[BUDGET GUARD VIOLATION] '{label}' has 0% of its 5-hour limit left "
                       f"(resets in {_s._fmt_reset(five['resets_at'])}). Cannot launch model '{model or label}'.")
    if budget_weekly_pct is not None and w_left is not None and w_left < budget_weekly_pct:
        return False, (f"[BUDGET GUARD ALERT] Requested task weekly budget cap ({budget_weekly_pct}%) exceeds "
                       f"available weekly allowance for '{label}' ({w_left}% left, resets in {_s._fmt_reset(weekly['resets_at'])}).")
    if budget_session_pct is not None and s_left is not None and s_left < budget_session_pct:
        return False, (f"[BUDGET GUARD ALERT] Requested task 5h budget cap ({budget_session_pct}%) exceeds "
                       f"available 5h quota for '{label}' ({s_left}% left, resets in {_s._fmt_reset(five['resets_at'])}).")

    def show(v: Optional[float]) -> str:
        return "unknown" if v is None else f"{v}%"

    return True, f"Budget approved: '{label}' has {show(w_left)} weekly and {show(s_left)} 5h limit available{fresh}."
