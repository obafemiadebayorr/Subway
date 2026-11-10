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
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

import terminals
import native_terminals
import threads

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

import server as _s

def _has_any(text: str, words: List[str]) -> bool:
    """Whole-word match, so 'log' isn't found in 'catalog' or 'test' in 'latest'."""
    for w in words:
        pat = _s._WORD_CACHE.setdefault(w, re.compile(rf"\b{re.escape(w)}\b"))
        if pat.search(text):
            return True
    return False

def _scale_of(text: str) -> int:
    """Largest stated batch size, e.g. 29028 for 'an extraction job over 29,028 documents'."""
    best = 0
    for m in _s._SCALE_RE.finditer(text):
        with contextlib.suppress(ValueError):
            best = max(best, int(m.group(1).replace(",", "")))
    return best

def estimate_complexity(prompt: str, role: str = "") -> Dict[str, Any]:
    """Recommend a model tier and effort level. Advisory only: it never changes permissions."""
    text = (prompt or role).lower()  # Judged on the task itself: a job title says nothing about the work.
    scale = _s._scale_of(text)

    god_keywords = ["system redesign", "full rewrite", "god mode", "autonomous overhaul", "audit everything", "multi-repo", "complete architecture"]
    high_keywords = ["refactor", "migration", "architecture", "concurrency", "distributed", "auth flow", "security", "encryption", "multi-file", "protocol", "performance optimization", "memory leak", "race condition", "extraction", "pipeline"]
    med_keywords = ["test", "tests", "unit test", "spec", "bug fix", "endpoint", "parser", "component", "function", "update logic", "api route", "hook"]
    low_keywords = ["typo", "lint", "format", "rename", "comment", "docstring", "single line", "read file", "check status", "readme", "log", "clean"]

    if _s._has_any(text, god_keywords):
        tier, effort, mode = "frontier", "max", "god"
        rationale = "Scope involves full architectural overhaul or multi-system changes requiring frontier reasoning."
        recommended_provider = "claude"
    elif scale >= 1000:
        tier, effort, mode = "high", "high", "standard"
        rationale = f"Batch of about {scale:,} items: long-running work that needs care with failures and resuming."
        recommended_provider = "claude"
    elif _s._has_any(text, high_keywords):
        tier, effort, mode = "high", "high", "standard"
        rationale = "Multi-file refactoring, concurrency, or architectural security logic requires deep reasoning."
        recommended_provider = "claude"
    elif scale >= 100 or _s._has_any(text, med_keywords):
        tier, effort, mode = "medium", "medium", "standard"
        rationale = "Targeted feature, component, or unit test work matches standard reasoning models."
        recommended_provider = "auto"
    elif _s._has_any(text, low_keywords):
        tier, effort, mode = "low", "low", "standard"
        rationale = "Boilerplate, linting, formatting, or documentation tasks operate with fast/low effort."
        recommended_provider = "opencode"
    elif len(prompt.split()) > 100:
        tier, effort, mode = "high", "high", "standard"
        rationale = "Long detailed prompt with extensive requirements."
        recommended_provider = "auto"
    else:
        tier, effort, mode = "medium", "medium", "standard"
        rationale = "General task with moderate scope."
        recommended_provider = "auto"

    role_lower = (role or "").lower()
    raise_keywords = ["reviewer", "review", "architect", "architecture", "security"]
    if _s._has_any(role_lower, raise_keywords):
        tier_up = {"low": "medium", "medium": "high", "high": "frontier", "frontier": "frontier"}
        old_tier = tier
        tier = tier_up.get(tier, tier)
        if tier != old_tier:
            rationale += f" Role '{role}' raises tier from {old_tier} to {tier}."
            if tier == "high" and effort == "medium":
                effort = "high"
            elif tier == "medium" and effort == "low":
                effort = "medium"
            elif tier == "frontier":
                effort, mode = "max", "god"
                recommended_provider = "claude"

    return {
        "tier": tier,
        "effort": effort,
        "mode": mode,
        "recommended_provider": recommended_provider,
        "rationale": rationale,
    }

def _skill_for(role: str, prompt: str) -> Tuple[str, str]:
    """The role says what kind of work this is. The task decides only when the role doesn't say."""
    r = (role or "").lower()
    for skill, pat in _s.ROLE_SKILLS:
        if re.search(pat, r):
            return skill, f"the {role} role"
    text = prompt.lower()
    for skill, words in _s.TASK_SKILLS:
        if _s._has_any(text, words):
            return skill, "the task"
    return "reasoning", "the default"

def _pool_left(providers: List[Dict[str, Any]], provider: str, model: str) -> Optional[float]:
    """Lowest live allowance for this model, or None when the tool doesn't report one."""
    record = next((p for p in providers if p["id"] == provider), None)
    if not record:
        return None
    if provider == "opencode":
        if _s._is_opencode_model_exhausted(model, providers):
            return 0.0
        return 0.0 if (record.get("limited") or {}).get(model, {}).get("recent") else None
    pool = _s._select_pool(record, model)
    if not pool:
        return None
    now = time.time()
    values = [float(w["left_pct"]) for w in pool["windows"] if not w.get("disabled") and w.get("left_pct") is not None
              and not (w.get("resets_at") and w["resets_at"] <= now)]
    return min(values) if values else None

def route_task(prompt: str, role: str = "", private: bool = False, god: bool = False,
               providers: Optional[List[Dict[str, Any]]] = None, from_role: str = "") -> Dict[str, Any]:
    """Pick models using all three signals:
    the task sets hard rules and how strong the model must be, the role sets which strength counts,
    and remaining allowance chooses among models that are good enough.
    """
    c = _s.estimate_complexity(prompt, role=role)
    text = prompt.lower()
    scale = _s._scale_of(text)
    bulk = scale >= 100 and _s._has_any(text, _s.BULK_WORDS)
    skill, skill_from = _s._skill_for(role, prompt)
    if private or _s._has_any(text, _s.PRIVATE_WORDS):
        local_model = _s.choose_local_model(need_tools=False) or "local"
        return {"order": ["local"], "picks": [{"provider": "local", "model": local_model}], "tier": c["tier"], "effort": c["effort"],
                "private": True, "skill": skill, "reason": "Private data, so it stays on this machine."}

    tier = "frontier" if god else c["tier"]
    need = _s.TIER_STRENGTH[tier]
    if bulk:
        need = 1
    providers = providers if providers is not None else _s.discover_connected_providers()
    usable = {p["id"] for p in providers if (p.get("client") or {}).get("dispatchable")}
    stats = _s._load_model_stats()
    raw_weights = _s.load_routing_weights() if hasattr(_s, "load_routing_weights") else {}
    learned_edges = raw_weights.get("edges", {}) if isinstance(raw_weights, dict) else {}
    raw_copilot = _s._copilot_raw()
    copilot_limited = False
    copilot_exhausted = False
    copilot_chat_low = False
    shed_pct = float(_s._load_json("config.json").get("shed_at_pct", 20))
    if isinstance(raw_copilot, dict):
        snaps = raw_copilot.get("quota_snapshots") or {}
        chat = snaps.get("chat") or {}
        prem = snaps.get("premium_interactions") or {}
        chat_ent = chat.get("entitlement", 0)
        chat_unl = bool(chat.get("unlimited", False))
        chat_rem = chat.get("remaining")
        chat_pct = chat.get("percent_remaining")
        if chat_pct is None and chat_ent > 0 and chat_rem is not None:
            chat_pct = (chat_rem / chat_ent) * 100.0

        if not chat_unl and (chat_rem == 0 or (chat_rem is not None and chat_rem <= 0) or (chat_pct is not None and chat_pct <= 0)):
            copilot_exhausted = True

        chat_finite = (not chat_unl) and (chat_ent > 0)
        chat_little_left = (not chat_unl) and (chat_pct is not None and chat_pct <= shed_pct)
        if chat_little_left:
            copilot_chat_low = True

        prem_ent = prem.get("entitlement", 0)
        prem_unl = prem.get("unlimited", False)
        prem_pct = prem.get("percent_remaining")
        prem_little_left = (not prem_unl) and (prem_pct is not None and prem_pct <= shed_pct)
        if chat_finite and not chat_little_left and not copilot_exhausted and ((prem_ent == 0 and not prem_unl) or prem_little_left):
            copilot_limited = True
    is_code_edit = (skill == "coding" and not _s._has_any(text, ["review", "audit"]))
    ranked = []
    for m in _s.model_catalog():
        if m["provider"] not in usable:
            continue
        model_name = (m.get("model") or "").strip()
        if m["provider"] == "antigravity" and (not model_name or model_name == "inherit"):
            model_name = _s._resolve_agy_model(model_name)
        elif not model_name:
            if m["provider"] == "local":
                model_name = _s.choose_local_model(need_tools=False) or "local"
            elif m["provider"] == "opencode":
                oc_cand = [om for om in _s._opencode_models() if om and not _s._model_is_bad(om) and not _s._is_opencode_model_exhausted(om, providers)]
                model_name = oc_cand[0] if oc_cand else ""
            elif m["provider"] == "copilot":
                cp_cand = [cm["model"] for cm in _s._copilot_models() if cm.get("model") and not _s._model_is_bad(cm["model"]) and not _s.is_copilot_cli_refused(cm["model"])]
                model_name = cp_cand[0] if cp_cand else "auto"
            else:
                model_name = m["provider"]
        if not model_name or _s._model_is_bad(model_name):
            continue
        if m["provider"] == "opencode" and _s._is_opencode_model_exhausted(model_name, providers):
            continue
        if m["provider"] == "copilot" and _s.is_copilot_cli_refused(model_name):
            continue
        if m["provider"] == "copilot" and copilot_exhausted:
            continue
        if m["provider"] == "copilot" and copilot_limited:
            usable_cp = {cm["model"] for cm in _s._copilot_models() if cm.get("model") and not _s.is_copilot_cli_refused(cm["model"])}
            if model_name not in usable_cp and model_name != "auto":
                continue
        if m["provider"] == "local" and model_name:
            fit_ok, _ = _s.local_fit(model_name)
            if not fit_ok:
                continue
        # For pi/DeepAI: models with theos_cost > 0 consume the user's finite daily 50 Theos pot.
        # Autonomous routing strictly picks from the 0-Theos free models unless god/frontier mode is enabled.
        if m["provider"] == "pi" and (m.get("theos_cost") or 0) > 0 and not god:
            continue
        m_entry = dict(m, model=model_name)
        strength = float(m_entry.get(skill) if m_entry.get(skill) is not None else 1)
        left = _s._pool_left(providers, m_entry["provider"], m_entry["model"])
        if left is not None and left <= 0:
            continue
        is_over_threshold = bool(left is not None and left <= shed_pct)
        if is_over_threshold:
            m_entry["over_threshold"] = True
        if m["provider"] == "copilot" and is_over_threshold:
            m_entry["copilot_low"] = True

        # Weigh recent landed rate for this job type (if >= 5 records; benchmarks stay as prior otherwise)
        m_recs = [r for r in stats if r.get("provider") == m_entry["provider"]
                  and (not r.get("model") or r.get("model") == m_entry["model"])
                  and _s._matches_job_type(r, tier, role)]
        if len(m_recs) >= 5:
            landed = sum(1 for r in m_recs if r.get("outcome") == "landed")
            no_change = sum(1 for r in m_recs if r.get("outcome") == "no_change")
            landed_rate = landed / len(m_recs)
            no_change_rate = no_change / len(m_recs)
            strength = round(strength * landed_rate, 2)
            m_entry["landed_rate"] = landed_rate
            m_entry["no_change_rate"] = no_change_rate
            if is_code_edit and no_change_rate >= 0.2:
                m_entry["high_no_change"] = True

        # Adaptive weight from learned routing_weights.json
        ad_weight = 1.0
        ad_expl = ""
        ad_runs = 0
        if learned_edges:
            m_prov = m_entry["provider"]
            m_mod = m_entry["model"]
            key_arrow = f"{from_role.lower()}->{role.lower()}|{m_prov}/{m_mod}" if from_role else ""
            key_slash = f"{role.lower()}/{tier.lower()}/{m_prov}:{m_mod}"
            key_fallback_arrow = f"*->{role.lower()}|{m_prov}/{m_mod}"
            edge_match = learned_edges.get(key_arrow) or learned_edges.get(key_slash) or learned_edges.get(key_fallback_arrow)
            if not edge_match:
                for ev in learned_edges.values():
                    if isinstance(ev, dict) and ev.get("provider") == m_prov and ev.get("model") == m_mod:
                        ev_to = (ev.get("to_role") or ev.get("role_category") or "").lower()
                        if not role or ev_to == role.lower():
                            edge_match = ev
                            break
            if edge_match:
                r_count = int(edge_match.get("runs") or edge_match.get("run_count") or 0)
                ad_runs = r_count
                if r_count >= 2:
                    ad_weight = float(edge_match.get("weight", 1.0))
                    ad_expl = str(edge_match.get("last_explanation") or "")

        m_entry["adaptive_weight"] = ad_weight
        m_entry["adaptive_explanation"] = ad_expl
        m_entry["adaptive_runs"] = ad_runs

        ranked.append((strength, left if left is not None else 25.0, m_entry, is_over_threshold))
    enough = [r for r in ranked if r[0] >= need and not r[3]]
    if not enough:
        enough = [r for r in ranked if not r[3]]
    if not enough:
        enough = [r for r in ranked if r[0] >= need]
    if not enough:
        enough = sorted(ranked, key=lambda r: -r[0])[:3]  # Nothing is strong enough: offer the strongest available.

    # Which models have ever actually delivered. Cost preference used to be unconditional:
    # a model stamped free (or ending -free) scored 0 and was chosen first for everyday
    # work even with no successful run, so a cost heuristic could become a correctness bug.
    proven = {(str(r.get("provider") or ""), str(r.get("model") or ""))
              for r in stats if str(r.get("outcome") or "") == "landed"}

    def _pref(m_info: Dict[str, Any]) -> int:
        prov = m_info.get("provider", "")
        m_name = m_info.get("model", "")
        is_free = bool(m_info.get("free") or m_name.endswith("-free") or prov == "local")
        # Free stays cheap to spend, but only wins outright once it has landed something.
        # On-device models are the user's own machine and keep their preference.
        free_unproven = is_free and prov != "local" and (prov, m_name) not in proven
        if bulk:
            if prov == "local" or (is_free and not free_unproven):
                return 0
            return 1
        if is_code_edit:
            if m_info.get("high_no_change"):
                return 4
            if free_unproven:
                return 4  # same penalty as a model known to change nothing
            if prov == "local" or (is_free and not free_unproven):
                return 3
            if prov == "copilot":
                return 2 if (copilot_chat_low or m_info.get("copilot_low")) else (0 if (copilot_limited and need <= 2) else 2)
            if prov == "antigravity":
                return 0
            if prov == "claude":
                return 1
            return 2
        else:
            if prov == "local" or (is_free and not free_unproven):
                return 0
            if free_unproven:
                return 5  # cheap and unproven goes last among good-enough, never last-resort
            if prov == "copilot":
                return 2 if (copilot_chat_low or m_info.get("copilot_low")) else (0 if (copilot_limited and need <= 2) else 2)
            if prov == "antigravity":
                return 1
            if prov == "claude":
                return 3
            return 2

    if god:
        # God mode: prefer the strongest model that still has quota, reaching for frontier models
        enough.sort(key=lambda r: (r[3], -float(r[2].get("adaptive_weight") or 1.0), -r[0], bool(r[2].get("scarce")), -r[1]))
    elif need >= 4:
        # Hard work: the strongest model, and a scarce one only if nothing else is as strong. Free models last on code edits.
        enough.sort(key=lambda r: (
            r[3],
            -float(r[2].get("adaptive_weight") or 1.0),
            2 if (r[2]["provider"] == "copilot" and (copilot_limited or copilot_chat_low or r[2].get("copilot_low"))) else (
                2 if is_code_edit and r[2].get("high_no_change") else (1 if is_code_edit and (r[2].get("free") or r[2]["model"].endswith("-free") or r[2]["provider"] == "local") else 0)
            ),
            -r[0],
            bool(r[2].get("scarce")),
            -r[1]
        ))
    else:
        # Everyday work: prefer provider tier according to task type, then weakest good enough, saving strong allowances.
        enough.sort(key=lambda r: (r[3], -float(r[2].get("adaptive_weight") or 1.0), _pref(r[2]), r[0], -r[1]))

    picks = [{"provider": r[2]["provider"], "model": r[2]["model"]} for r in enough if (r[2].get("model") or "").strip() and not _s._model_is_bad(r[2].get("model") or "")]
    first = picks[0] if picks else {}
    label = first.get("model") or first.get("provider", "no agent")
    first_entry = next((r[2] for r in enough if r[2].get("provider") == first.get("provider") and r[2].get("model") == first.get("model")), {})
    ad_expl = first_entry.get("adaptive_explanation")
    ad_runs = int(first_entry.get("adaptive_runs") or 0)

    if ad_expl and ad_runs >= 2:
        if from_role:
            reason = f"Picked {first.get('provider')}/{first.get('model')} for {from_role}->{role or 'Worker'}: {ad_expl}."
        else:
            reason = f"{tier.capitalize()} {skill} task, with skill taken from {skill_from}. Picked {first.get('provider')}/{first.get('model')}: {ad_expl}."
    elif bulk:
        reason = f"Repetitive work over about {scale:,} items, so the cheapest capable model: {label}. Use spin_batch so it can resume."
    else:
        reason = f"{tier.capitalize()} {skill} task, with skill taken from {skill_from}. {label} is " + \
                 ("the strongest available." if need >= 4 else "the cheapest good enough, saving stronger allowances.")
    return {"order": list(dict.fromkeys(p["provider"] for p in picks)), "picks": picks, "tier": tier, "effort": c["effort"],
            "private": False, "skill": skill, "reason": reason}

def t_routing_weights(role: str = "", complexity: str = "") -> str:
    """Return learned routing weights, run counts, and last explanation for matching roles and tiers."""
    data = _s.load_routing_weights() if hasattr(_s, "load_routing_weights") else {}
    edges = data.get("edges", {}) if isinstance(data, dict) else {}
    if not isinstance(edges, dict) or not edges:
        return "No learned routing weights recorded yet."

    role_filter = (role or "").strip().lower()
    comp_filter = (complexity or "").strip().lower()

    lines = []
    seen = set()
    for k, v in edges.items():
        if not isinstance(v, dict):
            continue
        edge_id = id(v)
        if edge_id in seen:
            continue
        seen.add(edge_id)

        to_r = (v.get("to_role") or v.get("role_category") or v.get("role") or "").lower()
        tier_val = (v.get("complexity") or v.get("tier") or "").lower()

        if role_filter and role_filter != to_r and role_filter not in to_r:
            continue
        if comp_filter and comp_filter != tier_val:
            continue

        from_r = v.get("from_role") or "*"
        prov = v.get("provider") or ""
        mod = v.get("model") or ""
        w = v.get("weight", 1.0)
        runs = v.get("runs") or v.get("run_count") or 0
        ema_rew = v.get("ema_reward") if v.get("ema_reward") is not None else v.get("reward", 0.5)
        expl = v.get("last_explanation") or ""

        route_str = f"{from_r}->{to_r}" if from_r != "*" else to_r
        lines.append(f"Route: {route_str} ({tier_val or 'standard'})\n  Target: {prov}:{mod}\n  Weight: {w} | Runs: {runs} | EMA Reward: {ema_rew}\n  Explanation: {expl}")

    if not lines:
        return f"No learned routing weights matching role='{role}' complexity='{complexity}'."
    return "\n\n".join(lines)

def _safe_prompt(prompt: str, provider: str = "") -> str:
    """A prompt must not parse as a CLI flag, and sanitizes false-positive policy triggers."""
    p = prompt.strip()
    if p.startswith("-"):
        p = f"Task: {p}"
    if provider == "antigravity":
        # Sanitize false-positive Google policy trigger combinations
        p = re.sub(r"\bkill\b(?=.*\b(?:children|child)\b)", "stop", p, flags=re.IGNORECASE)
        p = re.sub(r"(\b(?:children|child)\b.*?)\bkill\b", r"\g<1>stop", p, flags=re.IGNORECASE)
        p = re.sub(r"\b(?:children|child)\b", "sub-workers", p, flags=re.IGNORECASE)
        p = p.replace("--dangerously-skip-permissions", "")
    return p

def _worker_command(provider: str, bin_path: str, model: str, effort: str, god: bool, caps: List[str],
                    permission_mode: str, prompt: str, max_budget_usd: Optional[float],
                    thread_id: str, worker_id: str = "", workdir: str = "",
                    isolate: bool = False, isolated: bool = False, session_id: str = "") -> List[str]:
    """Translate a worker spec into the real flags of each agent CLI."""
    if provider == "antigravity" and effort == "max":
        effort = "high"  # Antigravity models stop at high, and refuse max outright.
    skip_permissions = god or permission_mode == "auto_approve" or "terminal:full" in caps
    safe = permission_mode == "auto_safe"
    text = _s._safe_prompt(prompt, provider=provider)

    if provider == "copilot":
        m = model if (model and model != "inherit") else "auto"
        wd = workdir or os.getcwd()
        cmd = [bin_path or "copilot", "--model", m, "--add-dir", wd, "--sync-session=off", "--no-ask-user"]
        if max_budget_usd is not None:
            # Copilot AI credits are cent-denominated (1 credit = $0.01); round up so a fractional USD cap is not weakened.
            cmd.extend(["--max-ai-credits", str(math.ceil(max_budget_usd * 100))])
        if permission_mode == "ask":
            cmd.append("--acp")
        else:
            # --stream on keeps -p output flowing; the session id lets the stall watchdog follow the events file.
            cmd.extend(["-p", text, "--output-format", "json", "--stream", "on"])
            if session_id:
                cmd.extend(["--session-id", session_id])
        if skip_permissions:
            cmd.append("--allow-all-tools")
        elif safe:
            # OS sandbox: shell commands can only write inside the allowed folders, so python3 is safe to allow below.
            cmd.append("--sandbox")
            if "files:write" in caps:
                safe_workdir = os.path.join(_s.SUBWAY_DIR, "worktrees", worker_id) if (isolate or isolated) and worker_id else wd
                # Copilot matches write(<dir>) only as that exact path; <dir>/** covers files inside it.
                cmd.append(f"--allow-tool=write({os.path.realpath(safe_workdir)}/**)")
            if "terminal:safe" in caps:
                cmd.extend(f"--allow-tool=shell({command}:*)" for command in _s.AGY_SAFE_COMMANDS + _s.COPILOT_SANDBOXED_COMMANDS)
        return cmd

    if provider == "claude":
        cmd = [bin_path, "--model", model, "--effort", effort, "--output-format", "stream-json", "--verbose"]
        if thread_id:
            cmd.extend(["--resume", thread_id])
        if "browser" in caps:
            cmd.append("--chrome")
        if max_budget_usd is not None:
            cmd.extend(["--max-budget-usd", str(max_budget_usd)])
        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        else:
            allowed: List[str] = []
            if "files:read" in caps:
                allowed.extend(["Read", "Glob", "Grep"])
            if safe and "files:write" in caps:
                allowed.extend(["Edit", "Write"])
            if safe and "terminal:safe" in caps:
                allowed.extend(["Bash(git status*)", "Bash(git diff*)", "Bash(git log*)", "Bash(pytest*)",
                                "Bash(npm test*)", "Bash(python3 -m pytest*)", "Bash(python3 -m unittest*)"])
            if allowed:
                cmd.extend(["--allowedTools", ",".join(allowed)])
            # Anything outside that list is asked of the user through the permission relay.
            cmd.extend(["--permission-prompt-tool", "mcp__subway_permit__request", "--mcp-config", json.dumps(
                {"mcpServers": {"subway_permit": {"command": sys.executable,
                                                  "args": [os.path.abspath(_s.__file__), "__permit_server", worker_id]}}})])
        return cmd + ["--output-format", "stream-json", "--verbose", "-p", text]

    if provider == "pi":
        # A path inside a macOS .app is the DeepAI Code GUI, which rejects -p/--mode. This module is
        # live-reloaded, so the guard holds even while a long-running server has a stale finder.
        if bin_path and (".app/Contents/" in bin_path or bin_path.rstrip("/").endswith(".app")):
            wrapper = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "pi-deep")
            bin_path = wrapper if os.path.isfile(wrapper) and os.access(wrapper, os.X_OK) else ""
        cmd = [bin_path or "pi", "-p", text, "--mode", "json"]
        if model and model not in ("inherit", "auto", "default"):
            # Zero-Theos Protection Guard:
            # Bare 'glm-5.3' spends 3 Theos/req, while 'glm-5.3-flash' is 0 Theos (free/sponsored).
            m = model
            if m in ("glm-5.3", "deepai/glm-5.3"):
                m = "deepai/glm-5.3-flash"
            elif m in ("deepseek-v4", "deepai/deepseek-v4", "deepseek-v4.1", "deepai/deepseek-v4.1"):
                m = "deepai/deepseek-v4.1-flash"
            elif m in ("mimo-2.6", "deepai/mimo-2.6"):
                m = "deepai/mimo-2.6-flash"
            elif m in ("solar-mini", "deepai/solar-mini"):
                m = "deepai/solar-mini-4"
            cmd.extend(["--model", m])
        if effort and effort != "auto":
            cmd.extend(["--thinking", effort])
        return cmd

    if provider == "opencode":
        return [bin_path, "acp"]

    cp = next((p for p in _s.load_custom_providers() if p["id"] == provider), None)
    if cp:
        exe = bin_path or shutil.which(cp.get("bin", "")) or cp.get("bin", provider)
        raw_args = list(cp.get("args") or [])
        if model and cp.get("model_args"):
            raw_args.extend(cp.get("model_args") or [])
        cmd = [exe]
        for a in raw_args:
            cmd.append(str(a).replace("{prompt}", text).replace("{model}", model or ""))
        return cmd

    cmd = [bin_path, "--print", text, "--output-format", "stream-json", "--print-timeout", "0"]
    if model and model != "inherit":
        cmd.extend(["--model", model])
    if effort and not re.search(r"-(low|medium|high)$", model or ""):  # Those model names already set the effort.
        cmd.extend(["--effort", effort])
    if thread_id:
        cmd.extend(["--conversation", thread_id])
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    elif safe:
        if "files:write" in caps:
            cmd.extend(["--mode", "accept-edits"])
        if "terminal:safe" in caps:  # Anything else pauses the worker and asks the user.
            cmd.extend(["--settings", json.dumps({"permissions": {"allow": [f"command({c})" for c in _s.AGY_SAFE_COMMANDS]}})])
    return cmd

