"""Usage package: compaction submodule (split from usage.py)."""

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


def get_compaction() -> str:
    cfg = _s._load_json("config.json")
    val = (cfg.get("compaction") or "suggest").lower().strip()
    return val if val in ("suggest", "auto", "never") else "suggest"


def compact_at_tokens() -> int:
    """The context size, in tokens, at which Subway asks for compaction (100K to 1M)."""
    try:
        v = int(_s._load_json("config.json").get("compact_at_tokens") or _s.COMPACT_AT_DEFAULT)
    except (TypeError, ValueError):
        v = _s.COMPACT_AT_DEFAULT
    return max(100000, min(1000000, v))


def _claude_settings_file() -> str:
    return os.path.join(_s.CLAUDE_DIR, "settings.json")


def _pi_settings_file() -> str:
    """The settings.json the running pi actually reads.

    pi's docs say ~/.pi/agent/settings.json, but DeepAI Code runs pi from its own
    userdata (~/.deepai-code/userdata/<app>/pi/<app>/settings.json) via
    PI_CODING_AGENT_DIR. Writing the OSS default silently did nothing there: the file
    never existed, so Subway's compaction point was never applied at all.
    """
    override = os.environ.get("PI_CODING_AGENT_DIR", "").strip()
    if override:
        return os.path.join(os.path.expanduser(override), "settings.json")
    try:
        from local_models import pi_agent_dir

        return os.path.join(pi_agent_dir(), "settings.json")
    except Exception:
        return os.path.join(getattr(_s, "PI_DIR", os.path.join(getattr(_s, "HOME", os.path.expanduser("~")), ".pi", "agent")),
                            "settings.json")


_PI_COMPACT_DEFAULT_WINDOW = 200000  # documented default when model context limit is unknown


def _parse_ctx_size(value: Any) -> int:
    """Turn a displayed context size into tokens: 1000000, "1.0M", "200K", "131.1K".

    pi's --list-models prints these as human strings, so an isinstance(int) check
    silently failed and every model fell back to the 200K default (a 1M model looked
    like it was 5x smaller than it is).
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else 0
    if not isinstance(value, str):
        return 0
    s = value.strip().replace(",", "").replace("_", "").lower()
    if not s:
        return 0
    mult = 1
    for suffix, factor in (("m", 1_000_000), ("k", 1_000)):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            mult = factor
            break
    try:
        return max(0, int(float(s) * mult))
    except (TypeError, ValueError):
        return 0


def _pi_model_context_limit(model: Optional[str]) -> int:
    """Resolve a pi model's context window from _pi_models() cache; fall back to 200K."""
    if not model:
        return _PI_COMPACT_DEFAULT_WINDOW
    try:
        models = _s._pi_models(block=False) or []
    except Exception:
        models = []
    want = str(model)
    for m in models:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "")
        if not mid:
            continue
        if mid == want or mid.endswith("/" + want) or want.endswith("/" + mid) or mid.split("/")[-1] == want.split("/")[-1]:
            ctx = _parse_ctx_size(m.get("context"))
            if ctx > 0:
                return ctx
            ctx = _parse_ctx_size(m.get("contextWindow"))
            if ctx > 0:
                return ctx
    return _PI_COMPACT_DEFAULT_WINDOW


def _pi_reference_window(at: int, model: Optional[str] = None) -> int:
    """Best-effort context window for mapping Subway's absolute `at` to pi reserveTokens."""
    if model:
        return _pi_model_context_limit(model)
    try:
        models = _s._pi_models(block=False) or []
    except Exception:
        models = []
    best = _PI_COMPACT_DEFAULT_WINDOW
    for m in models:
        if not isinstance(m, dict):
            continue
        ctx = m.get("context")
        if isinstance(ctx, (int, float)) and ctx > best:
            best = int(ctx)
    return max(best, max(0, int(at)), _PI_COMPACT_DEFAULT_WINDOW)


def _pi_reserve_tokens_for_at(at: int, model: Optional[str] = None) -> int:
    """Map Subway's absolute compact_at_tokens point to pi's reserveTokens.

    Pi triggers when contextTokens > contextWindow - reserveTokens (docs/compaction.md:
    "When It Triggers": `contextTokens > contextWindow - reserveTokens`; Settings:
    reserveTokens). To compact near `at` tokens, reserveTokens = contextWindow - at.
    Uses the best-known/largest pi model context window as a reference when unknown."""
    window = _pi_reference_window(at, model)
    return max(0, min(window, window - max(0, int(at))))


def _sync_pi_autocompact(mode: str, at: int) -> str:
    """Auto mode: write pi-coding-agent's compaction config so pi compacts itself.

    pi exposes compaction.enabled / compaction.reserveTokens in ~/.pi/agent/settings.json
    (docs/compaction.md, "Settings": enabled/reserveTokens/keepRecentTokens; "When It
    Triggers": contextTokens > contextWindow - reserveTokens). So the config path is used;
    no wake-event fallback is needed. Leaves user-set values alone unless Subway set them
    (tracked in config.json as pi_autocompact_set)."""
    path = _pi_settings_file()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return ""
    except FileNotFoundError:
        data = {}
    except Exception:
        return " Couldn't read pi settings, so pi keeps its own compaction point."
    comp = data.get("compaction")
    if not isinstance(comp, dict):
        comp = {}
    cfg = _s._load_json("config.json")
    ours = cfg.get("pi_autocompact_set") if isinstance(cfg.get("pi_autocompact_set"), dict) else {}
    our_enabled = ours.get("enabled")
    our_reserve = ours.get("reserveTokens")
    cur_enabled = comp.get("enabled")
    cur_reserve = comp.get("reserveTokens")
    if mode == "auto":
        if (cur_enabled is not None and cur_enabled != our_enabled) or (
                cur_reserve is not None and our_reserve is not None and cur_reserve != our_reserve):
            return " pi already has its own compaction settings, so Subway left them alone."
        reserve = _pi_reserve_tokens_for_at(at)
        comp["enabled"] = True
        comp["reserveTokens"] = reserve
        # One trigger for every model pi actually reports, so a small-window model is not
        # asked to compact after its window is already gone, and a 1M model compacts at
        # Subway's point instead of app-wide default. modelOverrides keys are exact,
        # case-sensitive "provider/modelId" strings (settings.schema.json).
        overrides = dict(comp.get("modelOverrides") or {})
        our_overrides = dict(ours.get("modelOverrides") or {})
        try:
            known = _s._pi_models(block=False) or []
        except Exception:
            known = []
        for m in known:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or "").strip()
            ctx = _parse_ctx_size(m.get("context")) or _parse_ctx_size(m.get("contextWindow"))
            if not mid or ctx <= 0:
                continue
            if ctx <= int(at):
                # Subway's point sits above this model's window, so an override could only
                # be 0 ("never compact") — worse than pi's own default. Leave it alone.
                continue
            prior = overrides.get(mid) if isinstance(overrides.get(mid), dict) else {}
            # Leave a value the user chose themselves alone.
            if prior.get("reserveTokens") is not None and prior.get("reserveTokens") != our_overrides.get(mid):
                continue
            overrides[mid] = {**prior, "reserveTokens": ctx - int(at)}
            our_overrides[mid] = overrides[mid]["reserveTokens"]
        if overrides:
            comp["modelOverrides"] = overrides
        data["compaction"] = comp
        cfg["pi_autocompact_set"] = {"enabled": True, "reserveTokens": reserve, "at": at,
                                     "modelOverrides": our_overrides}
        note = (f" pi compacts by itself near Subway's point (reserveTokens={reserve:,}, ~{at:,} tokens ref; "
                f"{len(overrides)} per-model overrides).")
    else:
        if not ours:
            return ""
        # Restore only values Subway set; preserve unrelated compaction keys.
        changed = False
        if cur_enabled == our_enabled and our_enabled is not None:
            if mode == "never":
                comp["enabled"] = False
                cfg["pi_autocompact_set"] = {**ours, "enabled": False}
            else:
                comp.pop("enabled", None)
            changed = True
        if cur_reserve == our_reserve and our_reserve is not None:
            comp.pop("reserveTokens", None)
            changed = True
        our_model_overrides = ours.get("modelOverrides") or {}
        cur_overrides = comp.get("modelOverrides")
        if isinstance(cur_overrides, dict) and our_model_overrides:
            kept = {}
            for mid, entry in cur_overrides.items():
                ours_mid = our_model_overrides.get(mid)
                if isinstance(entry, dict) and ours_mid is not None and entry.get("reserveTokens") == ours_mid:
                    rest = {k: v for k, v in entry.items() if k != "reserveTokens"}
                    if rest:
                        kept[mid] = rest
                    continue
                kept[mid] = entry
            if kept:
                comp["modelOverrides"] = kept
            else:
                comp.pop("modelOverrides", None)
            changed = True
        if not changed:
            return ""
        if comp:
            data["compaction"] = comp
        else:
            data.pop("compaction", None)
        if mode != "never":
            cfg.pop("pi_autocompact_set", None)
        note = " pi's compaction settings are back to their default." if mode != "never" else " pi auto-compaction disabled by Subway."
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".subway.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        return " Couldn't update pi settings."
    _s._save_json("config.json", cfg)
    return note


def _enqueue_pi_compact_wake(text: str = "/compact") -> bool:
    """Fallback: enqueue a wake event whose text is /compact for the subway-wake extension.

    subway-wake.js drains ~/.subway-context/wake_spools/<station>.jsonl and injects events
    as user messages via pi.sendUserMessage (agent_end / /subway-wake). Used only when pi
    exposes no supported auto-compaction config key (it does; see _sync_pi_autocompact)."""
    station = os.environ.get("SUBWAY_STATION_ID", "").strip()
    if not station:
        try:
            spath = os.path.join(getattr(_s, "SUBWAY_DIR", os.path.join(getattr(_s, "HOME", os.path.expanduser("~")), ".subway-context")),
                                 "station_id.txt")
            if os.path.isfile(spath):
                with open(spath, "r", encoding="utf-8") as f:
                    station = f.read().strip()
        except Exception:
            station = ""
    if not station:
        try:
            ident = _s.detect_self()
            if ident and ident.get("source") == "pi" and ident.get("id"):
                station = str(ident["id"])
        except Exception:
            station = ""
    if not station:
        return False
    try:
        from wake_driver import WakeEvent, wake_spool_enqueue
        return bool(wake_spool_enqueue(station, WakeEvent(kind="compaction", text=text)))
    except Exception:
        return False


def _sync_claude_autocompact(mode: str, at: int) -> str:
    """Auto mode: set Claude Code's own autoCompactWindow so Claude Code compacts by itself near `at` tokens.
    Leaves a value the user set themselves alone, and removes only the value Subway set."""
    path = _s._claude_settings_file()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return ""
    except FileNotFoundError:
        data = {}
    except Exception:
        return " Couldn't read Claude Code's settings, so Claude Code keeps its own compaction point."
    cfg = _s._load_json("config.json")
    ours = cfg.get("claude_autocompact_set")
    current = data.get("autoCompactWindow")
    if mode == "auto":
        if current is not None and current != ours:
            return f" Claude Code already has its own compaction point ({current:,} tokens), so Subway left it alone."
        data["autoCompactWindow"] = at
        cfg["claude_autocompact_set"] = at
        note = f" Claude Code now compacts by itself near {at:,} tokens."
    else:
        if current is None or current != ours:
            return ""
        data.pop("autoCompactWindow", None)
        cfg.pop("claude_autocompact_set", None)
        note = " Claude Code's compaction point is back to its default."
    try:
        tmp = path + ".subway.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        return " Couldn't update Claude Code's settings."
    _s._save_json("config.json", cfg)
    return note


def set_compaction(mode: str, at: Optional[int] = None) -> str:
    m = (mode or "").lower().strip()
    if m not in ("suggest", "auto", "never"):
        return f"Error: compaction mode must be one of 'suggest', 'auto', 'never', got '{mode}'."
    with _s._state_lock("config"):
        data = _s._load_json("config.json")
        data["compaction"] = m
        if at is not None:
            data["compact_at_tokens"] = max(100000, min(1000000, int(at)))
        _s._save_json("config.json", data)
        note = _s._sync_claude_autocompact(m, _s.compact_at_tokens())
        pi_note = _sync_pi_autocompact(m, _s.compact_at_tokens())
    return f"[COMPACTION] Compaction mode set to '{m}', at {_s.compact_at_tokens():,} tokens.{note}{pi_note}"


def _pi_session_cwd(path: str) -> str:
    """Read pi session header cwd/project without parsing the full transcript."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    ent = json.loads(line)
                except Exception:
                    continue
                if isinstance(ent, dict) and ent.get("type") == "session":
                    return str(ent.get("cwd") or ent.get("project") or "")
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(8192)
        m = re.search(r'"type"\s*:\s*"session".*?"cwd"\s*:\s*"([^"]*)"', head, re.S)
        if m:
            return m.group(1)
        m = re.search(r'"cwd"\s*:\s*"([^"]*)"', head)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def _pi_newest_session_for_cwd(cwd: str) -> Optional[str]:
    """Newest pi/DeepAI session transcript whose header cwd matches `cwd`."""
    want = os.path.realpath(cwd or os.getcwd())
    try:
        candidates = _s.pi_files(include_subagents=False) if hasattr(_s, "pi_files") else []
    except Exception:
        candidates = []
    if not candidates:
        return None
    best: Optional[Tuple[float, str]] = None
    for path in candidates:
        try:
            scwd = _pi_session_cwd(path)
            if scwd:
                try:
                    if os.path.realpath(scwd) != want:
                        continue
                except Exception:
                    if os.path.normpath(scwd) != os.path.normpath(want):
                        continue
            else:
                # No header cwd: only consider if path hints at this project? Skip to avoid false matches.
                continue
            mt = os.path.getmtime(path)
            if best is None or mt > best[0]:
                best = (mt, path)
        except Exception:
            continue
    return best[1] if best else None


def _pi_usage_tokens(usage: Any) -> Optional[int]:
    """Extract context-ish token count from a pi usage dict (spec: totalTokens, else last assistant usage)."""
    if not isinstance(usage, dict):
        return None
    total = usage.get("totalTokens")
    if isinstance(total, (int, float)) and total >= 0:
        return int(total)
    total = usage.get("total_tokens")
    if isinstance(total, (int, float)) and total >= 0:
        return int(total)
    inp = usage.get("input")
    if inp is None:
        inp = usage.get("input_tokens")
    cread = usage.get("cacheRead")
    if cread is None:
        cread = usage.get("cache_read_input_tokens")
    cwrite = usage.get("cacheWrite")
    if cwrite is None:
        cwrite = usage.get("cache_creation_input_tokens")
    if inp is None and cread is None and cwrite is None:
        return None
    return int(inp or 0) + int(cread or 0) + int(cwrite or 0)


def _pi_context_tokens(usage: Any) -> Optional[int]:
    """Tokens actually resent on the LAST request: input + cacheRead + cacheWrite.

    pi's `totalTokens` is cumulative for the whole session, so using it as "context
    used" makes an old chat look permanently over budget. Claude Code's transcript is
    measured the same way (input + cache_read + cache_creation on the last assistant
    message), so this keeps the two clients on one definition.
    """
    if not isinstance(usage, dict):
        return None
    inp = usage.get("input")
    if inp is None:
        inp = usage.get("input_tokens")
    cread = usage.get("cacheRead")
    if cread is None:
        cread = usage.get("cache_read_input_tokens")
    cwrite = usage.get("cacheWrite")
    if cwrite is None:
        cwrite = usage.get("cache_creation_input_tokens")
    if inp is None and cread is None and cwrite is None:
        return None
    total = int(inp or 0) + int(cread or 0) + int(cwrite or 0)
    if total:
        return total
    # Present but all zero means the provider reported no per-request breakdown, so
    # fall back to the session total rather than claiming an empty context.
    fallback = usage.get("totalTokens")
    if fallback is None:
        fallback = usage.get("total_tokens")
    if isinstance(fallback, (int, float)) and fallback > 0:
        return int(fallback)
    return 0


def _pi_chat_context_usage(session_id: Optional[str] = None) -> Tuple[Optional[int], int]:
    """Estimate pi/DeepAI context from the newest session transcript for this cwd (or session_id).

    Uses the last assistant message's usage.totalTokens when present, else input+cache fields.
    Context limit comes from _pi_models() when available, else 200000 (documented default)."""
    path = None
    if session_id:
        if os.path.isfile(session_id):
            path = session_id
        else:
            # Resolve by pi session id.
            try:
                for candidate in (_s.pi_files(include_subagents=True) if hasattr(_s, "pi_files") else []):
                    if os.path.basename(candidate).startswith(str(session_id)) or str(session_id) in os.path.basename(candidate):
                        path = candidate
                        break
                    scwd = _pi_session_cwd(candidate)
                    _ = scwd  # header read is cheap enough when few candidates match; keep simple
                if not path and hasattr(_s, "pi_list"):
                    for meta in _s.pi_list(include_subagents=True) or []:
                        if str(meta.get("id") or "") == str(session_id):
                            path = meta.get("path")
                            break
            except Exception:
                path = None
    if not path:
        ident = None
        try:
            ident = _s.detect_self()
        except Exception:
            ident = None
        if ident and ident.get("source") == "pi" and ident.get("id"):
            try:
                for meta in (_s.pi_list(include_subagents=True) if hasattr(_s, "pi_list") else []) or []:
                    if str(meta.get("id") or "") == str(ident["id"]):
                        path = meta.get("path")
                        break
            except Exception:
                path = None
        if not path and ident and ident.get("source") == "pi":
            path = _pi_newest_session_for_cwd(os.getcwd())
    if not path:
        path = _pi_newest_session_for_cwd(os.getcwd())
    if not path or not os.path.exists(path):
        return None, _PI_COMPACT_DEFAULT_WINDOW

    last_tokens: Optional[int] = None
    model: Optional[str] = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ent = json.loads(line)
                except Exception:
                    continue
                if not isinstance(ent, dict):
                    continue
                msg = ent.get("message") if isinstance(ent.get("message"), dict) else {}
                role = str(msg.get("role") or ent.get("role") or "")
                etype = str(ent.get("type") or "")
                usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else (ent.get("usage") if isinstance(ent.get("usage"), dict) else None)
                if role == "assistant" or etype == "assistant":
                    toks = _pi_context_tokens(usage)
                    if toks is None:
                        toks = _pi_usage_tokens(usage)
                    if toks is not None:
                        last_tokens = toks
                    m = msg.get("model") or ent.get("model")
                    if m:
                        model = str(m)
                elif usage is not None and (role == "assistant" or "assistant" in etype):
                    toks = _pi_context_tokens(usage)
                    if toks is None:
                        toks = _pi_usage_tokens(usage)
                    if toks is not None:
                        last_tokens = toks
    except Exception:
        pass
    # Never widen the window to fit the measurement: that made used == window and
    # pinned every long chat at "100% of context" so the threshold never fired.
    window = _pi_model_context_limit(model)
    return last_tokens, window


def chat_context_usage(session_id: Optional[str] = None) -> Tuple[Optional[int], int]:
    """Measure the chat's context: for Claude Code, the last assistant message's
    input_tokens + cache_read_input_tokens + cache_creation_input_tokens in the transcript;
    window 200000 unless the model name has [1m]. For pi/DeepAI, uses the newest session
    transcript for this cwd (or session_id) and usage.totalTokens / last assistant usage."""
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
        # pi/DeepAI: Claude path not found; estimate from pi transcript when applicable.
        return _pi_chat_context_usage(session_id)

    last_tokens: Optional[int] = None
    biggest = 0  # The largest context this chat ever had: survives compaction.
    window = 200000
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
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
                    biggest = max(biggest, last_tokens)
                model = str(msg.get("model") or ent.get("model") or "")
                if "[1m]" in model.lower() or "1m" in model.lower():
                    window = 1000000
                elif model:
                    window = 200000
    except Exception:
        pass
    if biggest > window:
        window = 1000000  # A context ever bigger than 200K proves the larger window, even after compaction.
    if last_tokens is None:
        # Not a Claude transcript; try pi (e.g. explicit .jsonl path under pi roots).
        pi_used, pi_window = _pi_chat_context_usage(session_id or path)
        if pi_used is not None:
            return pi_used, pi_window
    return last_tokens, window


def compaction_threshold_pct(prompt: str) -> float:
    """Threshold: 60% of window when estimate_complexity on latest user prompt is high, else 75%."""
    est = _s.estimate_complexity(prompt)
    if est.get("tier") in ("high", "frontier") or est.get("effort") in ("high", "max"):
        return 60.0
    return 75.0


def compaction_threshold(prompt: str, window: int = 200000) -> int:
    """The lower of a share of the window and the token point, since cost grows with the context."""
    return min(int((_s.compaction_threshold_pct(prompt) / 100.0) * window), _s.compact_at_tokens())


def is_compaction_quiet(remaining_chat_budget: Optional[int], window: int = 200000) -> bool:
    """Stay quiet when the remaining chat budget is below half a window,
    because a fresh start would cost more than it saves."""
    if remaining_chat_budget is None:
        return False
    return remaining_chat_budget < (window / 2)


def compact_focus_line(track: str = "main", limit: int = 5) -> str:
    """Build a focus line from the latest ledger entries."""
    entries = _s.read_track_entries(track, limit=limit)
    items: List[str] = []
    for e in reversed(entries):
        decs = e.get("decisions")
        if isinstance(decs, list):
            for d in decs:
                s = str(d).strip()
                if s and s not in items:
                    items.append(s)
        elif isinstance(decs, str) and decs.strip():
            s = decs.strip()
            if s not in items:
                items.append(s)
        msg = (e.get("message") or "").strip()
        if msg and not msg.startswith(("[PERMISSION", "Compacted (")) and msg not in items:  # Records, not decisions.
            items.append(msg[:120])
        if len(items) >= 3:
            break
    return "keep " + "; ".join(items[:3]) if items else "keep the current task, its decisions and open items"


def compact_opencode_workers() -> bool:
    """Call OpenCode server's compact endpoint when present."""
    service_path = os.path.join(_s.HOME, ".local", "state", "opencode", "service.json")
    if not os.path.exists(service_path):
        return False
    try:
        with open(service_path, "r", encoding="utf-8") as f:
            service = json.load(f)
        url = service.get("url")
        pwd = service.get("password")
        if not url:
            return False
        auth = base64.b64encode(f"opencode:{pwd}".encode()).decode() if pwd else ""
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Basic {auth}"
        compacted = False
        for w in _s._all_workers():
            if w.get("status") == "running" and (w.get("provider") or "").lower() == "opencode":
                sess_id = w.get("session_id") or w.get("id")
                wdir = w.get("worktree") or w.get("project_dir") or _s.HOME
                h = dict(headers)
                if wdir:
                    h["x-opencode-directory"] = wdir
                req_url = f"{url.rstrip('/')}/session/{sess_id}/compact" if sess_id else f"{url.rstrip('/')}/compact"
                req = urllib.request.Request(req_url, method="POST", data=b"{}", headers=h)
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                try:
                    with opener.open(req, timeout=5):
                        compacted = True
                except Exception:
                    pass
        if not compacted:
            req = urllib.request.Request(f"{url.rstrip('/')}/compact", method="POST", data=b"{}", headers=headers)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(req, timeout=5):
                    compacted = True
            except Exception:
                pass
        return compacted
    except Exception:
        return False


def _compact_requests_file(session_id: Optional[str] = None) -> str:
    sid = session_id or _s._self_id() or "unknown-chat"
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(sid))
    base = getattr(_s, "SUBWAY_DIR", os.path.join(os.path.expanduser("~"), ".subway-context"))
    return os.path.join(base, "compact_requests", safe + ".jsonl")


def request_chat_compaction(session_id: Optional[str] = None, reason: str = "threshold",
                            instructions: str = "", min_gap: float = 1800.0) -> str:
    """Queue a real compaction request for this chat's client to execute.

    This is the missing half of autocompaction: Subway could measure and warn, but the
    only actor that can actually compact a pi chat is pi itself. The wake extension
    drains this queue and calls `/compact`, so a threshold crossing becomes a completed
    compaction instead of a notice the model may never read.

    Returns the queue path when a request was written, else "".
    """
    path = _compact_requests_file(session_id)
    now = time.time()
    try:
        if os.path.isfile(path):
            last_ts = 0.0
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        last_ts = max(last_ts, float(json.loads(line).get("ts") or 0))
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
            if min_gap and (now - last_ts) < min_gap:
                return ""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rec = {"ts": now, "reason": reason, "instructions": (instructions or "").strip(),
               "session_id": session_id or _s._self_id() or ""}
        with _s._state_lock("compact"):
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        return path
    except OSError:
        return ""


def pending_compaction_requests(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Peek at queued compaction requests (does not consume them)."""
    path = _compact_requests_file(session_id)
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return []
    return out


def clear_compaction_requests(session_id: Optional[str] = None) -> None:
    """Consume the queue once the client has actually started the compaction."""
    path = _compact_requests_file(session_id)
    try:
        with _s._state_lock("compact"):
            if os.path.isfile(path):
                os.remove(path)
    except OSError:
        pass


def compaction_notice(session_id: Optional[str] = None, prompt: str = "") -> Optional[str]:
    mode = _s.get_compaction()
    if mode == "never":
        return None

    usage, window = _s.chat_context_usage(session_id)
    if usage is None or window <= 0:
        return None

    thresh_pct = _s.compaction_threshold_pct(prompt)
    thresh_tokens = _s.compaction_threshold(prompt, window)

    cfg = _s._load_json("config.json")
    chat_limit, _scope = _s.chat_budget_for(session_id, cfg)
    if chat_limit:
        used_tokens = _s.chat_tokens_used(session_id) or 0
        rem_budget = int(chat_limit) - used_tokens
        if _s.is_compaction_quiet(rem_budget, window):
            return None

    if usage < thresh_tokens:
        return None

    # Auto mode: ask the client to compact for real, instead of only warning the model.
    # Claude Code does it natively via autoCompactWindow; pi has no such threshold that
    # Subway can rely on, so it gets an explicit request the wake extension turns into
    # `/compact`. Deduped per session so a long chat is not asked on every turn.
    if mode == "auto":
        try:
            request_chat_compaction(session_id, reason="threshold",
                                    instructions=compact_focus_line())
        except Exception:
            pass

    pct = (usage / window) * 100.0
    level = 90 if pct >= 90 else (80 if pct >= 80 else (75 if (pct >= 75 and thresh_pct <= 75) else 60))
    level = f"{level}|{usage // 100000}"  # Warn again for every 100K more, not only at window shares.
    sid = session_id or _s._self_id() or "unknown-chat"
    key = f"compaction_warning|{sid}|{level}"
    now = time.time()
    with _s._state_lock("pings"):
        sent = _s._load_json("limit_pings.json")
        if key in sent:
            return None
        sent[key] = now
        _s._save_json("limit_pings.json", {k: v for k, v in sent.items() if now - v < 8 * 86400})

    focus = _s.compact_focus_line()
    x_pct = int(round(pct))
    msg = (f"This chat holds {usage // 1000:,}K tokens of context ({x_pct}% of its window), re-read on every turn. "
           f"Compact now: /compact {focus}")
    _s.notify_desktop_event(key, "Subway Context", msg)

    if mode == "auto":
        at = _s.compact_at_tokens()
        # Say what actually happens per line, not a blanket "can't": Claude Code honours
        # autoCompactWindow, and pi honours compaction.modelOverrides reserveTokens, both
        # written by set_compaction. Only opencode workers are compacted by us directly.
        msg += (f". Auto mode is on: your client compacts by itself near {at:,} tokens "
                f"(Claude Code via autoCompactWindow, pi via compaction reserveTokens in its own settings.json)")
        _s.compact_opencode_workers()

    return msg
