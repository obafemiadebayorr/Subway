from __future__ import annotations
import sys
sys.modules.setdefault("server", sys.modules[__name__])
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
    ctypes = None
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
    fcntl = None
import server as _s


def _reroute_cost_rank(provider: str, model: str) -> int:
    provider = (provider or "").lower()
    provider = {"claude-code": "claude", "opencode2": "opencode", "agy": "antigravity", "ollama": "local", "github-copilot": "copilot"}.get(provider, provider)
    model_name = (model or "").lower().split("/", 1)[-1]
    if provider == "local":
        return 0
    if provider == "opencode" and "free" in model_name:
        return 1
    if provider == "antigravity":
        if "gpt-oss" in model_name or "flash" in model_name:
            return 2
        if "argon" in model_name:
            return 3
        if "sonnet" in model_name:
            return 4
        if "opus" in model_name:
            return 5
        return 3
    if provider == "copilot":
        return 3
    if provider == "claude":
        if "opus" in model_name:
            return 5
        if "sonnet" in model_name:
            return 4
        if "haiku" in model_name:
            return 3
        return 3
    if provider == "opencode":
        if "opus" in model_name:
            return 5
        if "sonnet" in model_name:
            return 4
        if "haiku" in model_name or "flash" in model_name:
            return 2
        return 3
    return 3


def _handle_worker_exhaustion_or_stall(
    worker_id: str,
    meta: Dict[str, Any],
    reason: str,
    is_stall: bool = False,
    reset_time: Optional[float] = None,
) -> Dict[str, Any]:
    """Stop a quota-limited or stalled worker, then reroute once or mark failed."""
    model = (meta.get("model") or "").strip()
    if not model or model in ("auto", "inherit", "default"):
        model = (meta.get("model_used") or "").strip()
    if not model:
        cmd = meta.get("cmd") or []
        if "--model" in cmd:
            idx = cmd.index("--model")
            if idx + 1 < len(cmd):
                model = cmd[idx + 1]
    if not model:
        model = meta.get("provider", "")

    if is_stall:
        _s._mark_model_stall_cooldown(model)
    else:
        if reset_time is None and reason:
            m_retry = re.search(r"retry\s+(?:after|in)\s+(\d+)", reason, re.IGNORECASE)
            if m_retry:
                reset_time = time.time() + float(m_retry.group(1))
        if reset_time is None:
            reset_time = time.time() + 4 * 3600
        _s._mark_model_bad(model, reason=reason, reset_time=reset_time)
        if "/" in model:
            _s._mark_model_bad(model.split("/", 1)[1], reason=reason, reset_time=reset_time)
        if (meta.get("provider") == "opencode"
                and model.lower().split("/", 1)[-1].endswith("-free")
                and re.search(r"free\s+usage\s+limit\s+reached", reason, re.IGNORECASE)):
            for free_model in _s._opencode_models():
                clean_model = str(free_model).lower().split("/", 1)[-1]
                if clean_model.endswith("-free"):
                    _s._mark_model_bad(str(free_model), reason=reason, reset_time=reset_time)
                    if "/" in str(free_model):
                        _s._mark_model_bad(str(free_model).split("/", 1)[1], reason=reason, reset_time=reset_time)

    condition = "stalled" if is_stall else "exhausted"

    launcher = str(meta.get("launched_by") or "")

    def fail_worker(fail_msg: str) -> Dict[str, Any]:
        _s._ping_launcher(launcher, fail_msg, from_id=worker_id)
        _s.notify_desktop_event(
            f"worker_stall_or_exhausted|{worker_id}", "Subway Context",
            fail_msg[:500], route="workers",
        )
        return {"status": "failed", "exit_code": 1, "reason": fail_msg, "stall_alerted": True}

    # Loop prevention: once per original task (no loops; keep a chain 'rerouted_from')
    if meta.get("rerouted_from"):
        fail_msg = f"Model '{model}' {condition}; worker `{worker_id}` was already rerouted from `{meta['rerouted_from']}`, not rerouting again."
        return fail_worker(fail_msg)

    # Route next model
    prompt = meta.get("prompt", "")
    role = meta.get("role", "")
    private = bool(meta.get("private"))
    is_god = meta.get("mode") == "god"
    route = _s.route_task(prompt, role=role, private=private, god=is_god)
    picks = route.get("picks", [])
    original_cost_rank = _s._reroute_cost_rank(str(meta.get("provider") or ""), model)

    excluded = {model.lower(), (model.split("/", 1)[1] if "/" in model else model).lower()}
    binaries = {
        "claude": _s.find_claude_binary(),
        "opencode": _s.find_opencode_binary(),
        "antigravity": _s.find_agy_binary(),
        "local": _s.find_opencode_binary(),
        "copilot": _s.find_copilot_binary(),
        "pi": getattr(_s, "find_pi_binary", lambda: None)(),
    }
    for cid, cp in {p["id"]: p for p in _s.load_custom_providers()}.items():
        binaries[cid] = shutil.which(cp.get("bin", ""))

    next_cand = None
    for cand in picks:
        cand_m = (cand.get("model") or "").lower()
        cand_m_clean = (cand_m.split("/", 1)[1] if "/" in cand_m else cand_m).lower()
        if cand_m in excluded or cand_m_clean in excluded:
            continue
        candidate_cost_rank = _s._reroute_cost_rank(
            str(cand.get("provider") or ""), str(cand.get("model") or ""),
        )
        if (original_cost_rank is None or candidate_cost_rank is None
                or candidate_cost_rank > original_cost_rank):
            continue
        if private and cand.get("provider") != "local":
            continue
        if _s._model_is_bad(cand.get("model") or ""):
            continue
        cand_prov = cand.get("provider", "")
        if not binaries.get(cand_prov):
            continue
        next_cand = cand
        break

    if not next_cand:
        if private:
            no_candidate_reason = (
                "Private worker cannot leave the local provider; no same-cost or cheaper local model is available. "
                f"{route.get('reason', 'no candidates')}"
            )
        elif original_cost_rank is None:
            no_candidate_reason = (
                "The original provider/model cost tier is unknown, so a safe non-more-expensive reroute "
                f"cannot be established. {route.get('reason', 'no candidates')}"
            )
        else:
            no_candidate_reason = (
                "No same-cost or cheaper provider/model is available for rerouting. "
                f"{route.get('reason', 'no candidates')}"
            )
        fail_msg = f"Model '{model}' {condition} and no alternative model available to reroute: {no_candidate_reason}"
        return fail_worker(fail_msg)

    # Keep the original request intact and only replace the provider/model choice.
    spin_args = dict(meta.get("spin_args") or {})
    if not spin_args:
        budget = meta.get("budget") or {}
        then = meta.get("then") or []
        spin_args = {
            "role": meta.get("role", ""),
            "prompt": prompt,
            "track": meta.get("track", "main"),
            "project_dir": meta.get("project_dir", ""),
            "timeout_seconds": int(meta.get("timeout_seconds") or _s.DEFAULT_WORKER_TIMEOUT),
            "effort": meta.get("effort", "auto"),
            "mode": meta.get("mode", "standard"),
            "permission_mode": meta.get("permission_mode", ""),
            "capabilities": meta.get("capabilities"),
            "budget_session_pct": budget.get("session_pct"),
            "budget_weekly_pct": budget.get("weekly_pct"),
            "max_budget_usd": budget.get("max_usd"),
            "max_tokens": int(budget.get("max_tokens") or 0),
            "max_seconds": int(budget.get("max_seconds") or 0),
            "thread": meta.get("thread", ""),
            "private": private,
            "isolate": bool(meta.get("isolate")),
            "priority": meta.get("priority", "normal"),
            "swarm_id": meta.get("swarm_id", ""),
            "then": json.dumps(then) if isinstance(then, list) else then,
            "review": meta.get("review", ""),
        }
    # Deduct what the failed run already consumed (bug-hunt-1.3.5 Bug 4).
    # NOTE: supervision records spend as meta["usage"] = {input, output, cache_read}
    # (worker_supervision/supervise.py _add_usage path); the historical
    # meta["spent_tokens"] key has never been written by any code path.
    usage = meta.get("usage") or {}
    spent_tokens = (int(usage.get("input") or 0) + int(usage.get("output") or 0)
                    + int(usage.get("thinking") or 0))
    if spin_args.get("max_tokens") and spent_tokens > 0:
        spin_args["max_tokens"] = max(1000, int(spin_args["max_tokens"]) - spent_tokens)
    started_raw = meta.get("started_at")
    started_at = 0.0
    if isinstance(started_raw, (int, float)):
        started_at = float(started_raw)
    elif isinstance(started_raw, str) and started_raw:
        try:
            started_at = _dt.datetime.fromisoformat(started_raw).timestamp()
        except Exception:
            pass
    if started_at > 0 and spin_args.get("max_seconds"):
        elapsed = int(time.time() - started_at)
        spin_args["max_seconds"] = max(10, int(spin_args["max_seconds"]) - elapsed)
    spin_args.pop("provider", None)
    spin_args.pop("model", None)
    spin_args.update(provider=next_cand["provider"], model=next_cand["model"])
    with _s._thread_parent(worker_id, "rerouted", {"condition": condition, "from_model": model, "to_model": next_cand["model"]}):
        spin_res = _s.t_spin_worker(**spin_args)

    m_wid = re.search(r"Worker `(worker_[0-9a-f]+)`", spin_res)
    if not m_wid:
        fail_msg = f"Model '{model}' exhausted/stalled; reroute failed: {spin_res}"
        return fail_worker(fail_msg)

    new_worker_id = m_wid.group(1)
    with _s._state_lock("workers"):
        new_meta = _s._load_worker(new_worker_id)
        if new_meta:
            new_meta["rerouted_from"] = worker_id
            if meta.get("launched_by"):
                new_meta["launched_by"] = meta.get("launched_by")
            if meta.get("priority"):
                new_meta["priority"] = meta.get("priority")
            if meta.get("review"):
                new_meta["review"] = meta.get("review")
            if meta.get("then") and not new_meta.get("then"):
                new_meta["then"] = meta.get("then")
            _s._save_worker(new_meta)

    ping_text = f"Worker `{worker_id}` model `{model}` {condition}. Task rerouted to `{next_cand['model']}` (`{new_worker_id}`)."
    _s._ping_launcher(launcher, ping_text, from_id=worker_id)
    _s.t_broadcast_track(ping_text, track=meta.get("track", "main"), station_id=worker_id)

    return {
        "status": "stopped",
        "exit_code": 0,
        "reason": f"Model '{model}' {condition}; stopped and rerouted to `{new_worker_id}` ({next_cand['model']}).",
        "rerouted_to": new_worker_id,
    }

