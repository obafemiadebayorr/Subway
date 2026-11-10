"""Worker records, spawn, launch, verification, batch and swarm handlers, worker status tools.

Moved from server.py (Subway 1.3.5). Rule: server-level names are read late through `_s.NAME` so tests that patch `server.X` still apply. Import this module through server, not directly."""

from __future__ import annotations

import base64
import concurrent.futures
import contextlib
import contextvars
import datetime as _dt
import difflib
import glob
import gzip
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

from .prompts import _clip_prompt_summary


def route_task(prompt: str, role: str = "", private: bool = False, god: bool = False,
               providers: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
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
        m_entry = dict(m, model=model_name)
        skill_val = m_entry.get(skill)
        strength = float(skill_val) if skill_val is not None else 1.0
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

        ranked.append((strength, left if left is not None else 25.0, m_entry, is_over_threshold))
    enough = [r for r in ranked if r[0] >= need and not r[3]]
    if not enough:
        enough = [r for r in ranked if not r[3]]
    if not enough:
        enough = [r for r in ranked if r[0] >= need]
    if not enough:
        enough = sorted(ranked, key=lambda r: -r[0])[:3]  # Nothing is strong enough: offer the strongest available.

    def _pref(m_info: Dict[str, Any]) -> int:
        prov = m_info.get("provider", "")
        m_name = m_info.get("model", "")
        is_free = bool(m_info.get("free") or m_name.endswith("-free") or prov == "local")
        if bulk:
            return 0 if (is_free or prov == "local") else 1
        if is_code_edit:
            if m_info.get("high_no_change"):
                return 4
            if is_free or prov == "local":
                return 3
            if prov == "copilot":
                return 2 if (copilot_chat_low or m_info.get("copilot_low")) else (0 if (copilot_limited and need <= 2) else 2)
            if prov == "antigravity":
                return 0
            if prov == "claude":
                return 1
            return 2
        else:
            if is_free or prov == "local":
                return 0
            if prov == "copilot":
                return 2 if (copilot_chat_low or m_info.get("copilot_low")) else (0 if (copilot_limited and need <= 2) else 2)
            if prov == "antigravity":
                return 1
            if prov == "claude":
                return 3
            return 2

    if god:
        # God mode: prefer the strongest model that still has quota, reaching for frontier models
        enough.sort(key=lambda r: (r[3], -r[0], bool(r[2].get("scarce")), -r[1]))
    elif need >= 4:
        # Hard work: the strongest model, and a scarce one only if nothing else is as strong. Free models last on code edits.
        enough.sort(key=lambda r: (
            r[3],
            2 if (r[2]["provider"] == "copilot" and (copilot_limited or copilot_chat_low or r[2].get("copilot_low"))) else (
                2 if is_code_edit and r[2].get("high_no_change") else (1 if is_code_edit and (r[2].get("free") or r[2]["model"].endswith("-free") or r[2]["provider"] == "local") else 0)
            ),
            -r[0],
            bool(r[2].get("scarce")),
            -r[1]
        ))
    else:
        # Everyday work: prefer provider tier according to task type, then weakest good enough, saving strong allowances.
        enough.sort(key=lambda r: (r[3], _pref(r[2]), r[0], -r[1]))

    picks = [{"provider": r[2]["provider"], "model": r[2]["model"]} for r in enough if (r[2].get("model") or "").strip() and not _s._model_is_bad(r[2].get("model") or "")]
    first = picks[0] if picks else {}
    label = first.get("model") or first.get("provider", "no agent")
    if bulk:
        reason = f"Repetitive work over about {scale:,} items, so the cheapest capable model: {label}. Use spin_batch so it can resume."
    else:
        reason = f"{tier.capitalize()} {skill} task, with skill taken from {skill_from}. {label} is " + \
                 ("the strongest available." if need >= 4 else "the cheapest good enough, saving stronger allowances.")
    return {"order": list(dict.fromkeys(p["provider"] for p in picks)), "picks": picks, "tier": tier, "effort": c["effort"],
            "private": False, "skill": skill, "reason": reason}



_ROLE_PATTERNS = (
    ("orchestrator", re.compile(r"orchestrat|\blead\b|coordinator", re.I)),
    ("research", re.compile(r"research|measure|investigat|analys", re.I)),
    ("reviewer", re.compile(r"review|audit|verif", re.I)),
)
# Tools each role must NOT have. Fewer tools = smaller prompt each call, and no accidental edits.
ROLE_DISALLOWED_TOOLS = {
    "builder": ["WebFetch", "WebSearch"],
    "reviewer": ["Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch"],
    "research": ["Edit", "Write", "MultiEdit", "NotebookEdit"],
    "orchestrator": ["Edit", "Write", "MultiEdit", "NotebookEdit"],
}

def role_kind(role: str, caps: List[str]) -> str:
    """builder | reviewer | research | orchestrator, from the role text and capabilities."""
    for kind, rx in _ROLE_PATTERNS:
        if rx.search(role or ""):
            return kind
    return "builder" if "files:write" in (caps or []) else "orchestrator"


def _worker_command(provider: str, bin_path: str, model: str, effort: str, god: bool, caps: List[str],
                    permission_mode: str, prompt: str, max_budget_usd: Optional[float],
                    thread_id: str, worker_id: str = "", workdir: str = "",
                    isolate: bool = False, isolated: bool = False, session_id: str = "",
                    role: str = "") -> List[str]:
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
        _deny = ROLE_DISALLOWED_TOOLS.get(role_kind(role, caps), [])
        if _deny:
            cmd.extend(["--disallowedTools", ",".join(_deny)])
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
        # `pi` on PATH can resolve to the DeepAI Code Electron launcher, which is a GUI and
        # rejects -p/--mode ("bad option: --mode"), so every pi worker died at exec. Prefer
        # the repo's headless wrapper, which pins PI_CODING_AGENT_DIR and runs the real CLI.
        repo_dir = os.path.dirname(os.path.abspath(_s.__file__))
        wrapper = os.path.join(repo_dir, "bin", "pi-deep")
        found = shutil.which("pi")
        if found and "Contents/MacOS" in found:
            found = None  # app launcher, not the CLI
        if os.path.isfile(wrapper) and os.access(wrapper, os.X_OK):
            pi_cmd = [wrapper]
        elif found:
            pi_cmd = [found]
        else:
            pi_cmd = ["npx", "-y", "@earendil-works/pi-coding-agent"]
        cmd = pi_cmd + ["-p", text, "--mode", "json"]
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
        # pi --thinking accepts off|minimal|low|medium|high|xhigh|max; fixed if docs disagree (cli.md --thinking <level>).
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


_s._worker_command = _worker_command
for _mod in ("workers", "workers.routing", "server"):
    if _mod in sys.modules:
        setattr(sys.modules[_mod], "_worker_command", _worker_command)




def _thread_finished(worker_id: str, meta: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Record a worker's finish once per run, using the freshest stored meta. Never breaks the caller."""
    try:
        finished_meta = _s._load_worker(worker_id) or meta or {}
        finished_at = _dt.datetime.now().astimezone()
        duration_s = None
        started_at = str(finished_meta.get("started_at") or "")
        if started_at:
            with contextlib.suppress(ValueError, TypeError):
                duration_s = round((finished_at - _dt.datetime.fromisoformat(started_at)).total_seconds(), 1)
        _s._thread_record(finished_meta.get("thread_root") or _s._thread_root(finished_meta), "finished", worker_id, data={
            "status": (result or {}).get("status"), "exit_code": (result or {}).get("exit_code"),
            "reason": _s.one_line(str((result or {}).get("reason") or ""), 300),
            "rerouted_to": (result or {}).get("rerouted_to"),
            "usage": dict(finished_meta.get("usage") or {}),
            "model_used": finished_meta.get("model_used"),
            "created_at": finished_meta.get("created_at"),
            "completed_at": finished_at.isoformat(), "duration_s": duration_s})
    except Exception:
        pass



def _overshoot_warning(provider: str, model: str, effort: str, max_tokens: int) -> str:
    if provider == "antigravity" and max_tokens and max_tokens < 200000 and (str(model).endswith("-high") or effort in ("high", "max")):
        return f"Warning: {model} can think for 120K+ tokens in one step and the cap is only checked between steps, so a {max_tokens:,} cap can be passed by a single step. Use a -low or -medium model, or a cap of at least 200,000.\n"
    return ""


_AUTO_ITEM_RE = re.compile(r"(?<![\w/.-])([\w./-]+\.(?:py|js|mjs|cjs|ts|tsx)):([A-Za-z_][\w.]*|\d+(?:-\d+)?)\b")

def _auto_code_items(prompt: str, cwd: str, max_items: int = 6, max_lines: int = 400) -> List[str]:
    """Turn path:Symbol / path:LINE mentions in a brief into code packets, so builders don't search."""
    items, total, seen = [], 0, set()
    for m in _AUTO_ITEM_RE.finditer(prompt):
        item = f"{m.group(1)}:{m.group(2)}"
        if item in seen:
            continue
        seen.add(item)
        try:
            r = _s._extract_code_item(item, cwd)
        except Exception:
            r = None
        if not r:
            continue
        span = int(r[2]) - int(r[1]) + 1
        if total + span > max_lines:
            continue
        items.append(item); total += span
        if len(items) >= max_items:
            break
    return items


def _thin_brief_warning(prompt: Optional[str], capabilities: Optional[List[str]]) -> str:
    named_files = re.findall(r"[\w./-]+\.(?:py|js|ts|tsx|css|html|md)\b", prompt or "")
    return ("Warning: this brief names files but carries no code. Builders then spend their budget reading; "
            "put the exact functions and the exact edit in the prompt.\n"
            if "files:write" in (capabilities or []) and named_files and "```" not in (prompt or "")
            and "=== CODE (all you need" not in (prompt or "") else "")


def t_spin_worker(role: str, prompt: str, provider: str = "auto", track: str = "main",
                  project_dir: str = "", timeout_seconds: int = _s.DEFAULT_WORKER_TIMEOUT, effort: str = "auto",
                  mode: str = "standard", model: str = "", permission_mode: str = "",
                  capabilities: Optional[List[str]] = None,
                  budget_session_pct: Optional[float] = None, budget_weekly_pct: Optional[float] = None,
                  max_budget_usd: Optional[float] = None, thread: str = "", private: bool = False,
                  max_tokens: int = 0, max_seconds: int = 0, swarm_id: str = "", isolate: bool = False, route_reason: str = "",
                  priority: str = "normal", code: Optional[List[str]] = None,
                  attachments: Optional[List[str]] = None,
                  then: str = "", review: str = "",
                  wait: bool = False, wait_timeout_seconds: int = 0) -> str:
    """Queue a worker for user approval. Agents can request workers, but only the user can launch them.

    wait=True makes this the native subagent shape: the call returns when the worker is done,
    with the worker's own report, instead of leaving the caller to look for it later.
    """
    if not role or not prompt:
        return "Error: role and prompt are required."
    original_prompt = prompt
    try:
        steps = _s._parse_trigger_steps(then)
        review_step = _s._review_trigger_step(review)
    except ValueError as exc:
        return f"Error: {exc}"
    if review_step:
        steps.append(review_step)
    priority = (priority or "normal").lower()
    if priority not in ("urgent", "normal", "low"):
        return f"Error: unknown priority '{priority}'. Must be 'urgent', 'normal', or 'low'."
    _s._ensure_subway_dir()

    cwd = os.path.realpath(os.path.expanduser(project_dir)) if project_dir else os.getcwd()
    if not os.path.isdir(cwd):
        return f"Error: project_dir '{project_dir}' does not exist."

    file_hashes: Dict[str, str] = {}
    code_ranges: Dict[str, List[List[int]]] = {}
    if not code and "files:write" in (capabilities or []):
        code = _auto_code_items(prompt or "", cwd) or None
    if code is not None:
        packet_pieces = []
        failed_names = []
        for item in code:
            resolved = _s._extract_code_item(item, cwd) if item else None
            if not resolved:
                failed_names.append(item)
            else:
                rel_path, start_l, end_l, content, abs_path = resolved
                packet_pieces.append(f"--- {rel_path}:{start_l}-{end_l} ---\n{content.rstrip()}\n")
                if abs_path not in file_hashes:
                    file_hashes[abs_path] = _s._file_sha256(abs_path)
                code_ranges.setdefault(abs_path, []).append([int(start_l), int(end_l)])

        if failed_names:
            return f"Error: could not resolve code names: {', '.join(str(f) for f in failed_names)}."

        if packet_pieces:
            packet_text = "\n".join(packet_pieces).strip()
            if len(packet_text) > 60000:
                return f"Error: code packet exceeds 60000 characters ({len(packet_text)} chars)."
            prompt = f"{prompt}\n\n=== CODE (all you need: do not search) ===\n\n{packet_text}"

    attach_warn = ""
    if attachments:
        from hud_server import attachments as _attach
        paths, err = _attach.validate(attachments)
        if err:
            return f"Error: {err}"
        prompt = prompt + _attach.section(paths)
        if any(_attach.is_image(p) for p in paths) and not _attach.model_has_vision(provider, model):
            attach_warn = "Warning: an image is attached but this model may not read images.\n"

    lineage_parent = _s._lineage_parent()
    permission_mode, mode = _s._inherit_from_parent(lineage_parent, _s._MODE_ALIASES.get(permission_mode, permission_mode),
                                                 (mode or "standard").lower())
    permission_mode = permission_mode or _s.get_permission_mode(_s._self_id())
    if permission_mode not in _s.PERMISSION_MODES:
        return f"Error: unknown permission_mode '{permission_mode}'. Must be one of {', '.join(_s.PERMISSION_MODES)}."
    selected_mode = (mode or "standard").lower()
    if selected_mode not in ("standard", "god"):
        return f"Error: unknown mode '{mode}'. Must be 'standard' or 'god'."
    selected_provider = (provider or "auto").lower()
    if selected_provider == "god":
        selected_provider, selected_mode = "auto", "god"
    selected_provider = {"claude-code": "claude", "opencode2": "opencode", "agy": "antigravity", "ollama": "local", "github-copilot": "copilot"}.get(selected_provider, selected_provider)
    custom_map = {p["id"]: p for p in _s.load_custom_providers()}
    valid_providers = ("auto", "claude", "opencode", "antigravity", "local", "copilot", "pi") + tuple(custom_map.keys())
    if selected_provider not in valid_providers:
        # Derive the message from the list actually accepted, so a provider can never be
        # valid but invisible (pi was, which made the error actively misleading).
        choices = [p for p in valid_providers if p != "auto"] + ["god"]
        return (f"Error: unknown provider '{provider}'. Must be 'auto', " +
                ", ".join(f"'{c}'" for c in choices[:-1]) + f", or '{choices[-1]}'.")
    if selected_provider != "auto" and model:
        ok, valid_model, err = _s._validate_model_for_provider(selected_provider, model)
        if not ok:
            return err
        model = valid_model
    cfg = _s._load_json("config.json")
    is_god_mode = selected_mode == "god" or cfg.get("god_mode") is True
    if is_god_mode:
        selected_mode = "god"

    # Permissions come from an explicit request, never from words in a prompt.
    if capabilities is None:
        capabilities = ["files:read", "files:write", "terminal:safe"]
        p_low = f"{role} {prompt}".lower()
        if _s._has_any(p_low, ["browser", "chrome", "scrape", "dom", "ui test", "e2e"]):
            capabilities.append("browser")
        if _s._has_any(p_low, ["mcp", "database", "sql"]):
            capabilities.append("mcp")
    is_lead = "subagents" in (capabilities or []) or re.search(r"\b(?:lead|orchestrator)\b", str(role or "").lower())
    if is_lead:
        if "mcp" not in capabilities:
            capabilities.append("mcp")
        if "subagents" in capabilities and "files:write" in capabilities:
            # An orchestrator/lead delegates to sub-workers; strip files:write so it cannot bypass delegation
            capabilities = [c for c in capabilities if c != "files:write"]
    unknown = [c for c in capabilities if c not in _s.VALID_CAPABILITIES]
    if unknown:
        return f"Error: unknown capabilities {unknown}. Valid: {sorted(_s.VALID_CAPABILITIES)}."
    capabilities = list(capabilities)
    if is_god_mode and "terminal:full" not in capabilities:
        capabilities.append("terminal:full")

    if _s._load_json("config.json").get("worker_rules", True) is not False:
        reviewer = (prompt.lstrip().lower().startswith(("read-only", "read only"))
                    or re.search(r"\b(?:review|reviewer|research|researcher|read[- ]?only)\b", str(role or "").lower()))
        if reviewer:
            if _s._WORKER_READING_RULE not in prompt:
                prompt = f"{prompt.rstrip()}\n\n{_s._WORKER_READING_RULE}"
        elif is_lead:
            orch_rules = getattr(_s, "_ORCHESTRATOR_RULES", None)
            if orch_rules and orch_rules not in prompt:
                prompt = f"{prompt.rstrip()}\n\n{orch_rules}"
        elif _s._WORKER_STANDING_RULES not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{_s._WORKER_STANDING_RULES}"
        if capabilities and "subagents" in capabilities:
            lead_rule = "Leads: never run shell wait or poll loops; wait with await_worker, or one collect_reports(wait=True) or watch_workers call; never paste large code or logs into your own context."
            if lead_rule not in prompt:
                prompt = f"{prompt.rstrip()}\n{lead_rule}"

    complexity = _s.estimate_complexity(prompt, role)
    chosen_effort = (effort or "auto").lower()
    if chosen_effort == "auto":
        chosen_effort = complexity["effort"]
    if chosen_effort not in _s.VALID_EFFORTS:
        return f"Error: unknown effort '{effort}'. Must be 'auto', 'low', 'medium', 'high', or 'max'."
    if is_god_mode:
        chosen_effort = "max"

    opencode_bin = _s.find_opencode_binary()
    copilot_bin = _s.find_copilot_binary()
    cursor_bin = getattr(_s, "find_cursor_binary", lambda: None)()
    devin_bin = getattr(_s, "find_devin_binary", lambda: None)()
    binaries = {
        "claude": _s.find_claude_binary(),
        "opencode": opencode_bin,
        "antigravity": _s.find_agy_binary(),
        "local": opencode_bin,
        "copilot": copilot_bin,
        "pi": getattr(_s, "find_pi_binary", lambda: None)(),
        "cursor": cursor_bin,
        "devin": devin_bin,
    }
    for cid, cp in custom_map.items():
        binaries[cid] = shutil.which(cp.get("bin", ""))
    route = _s.route_task(prompt, role, private=private, god=is_god_mode)
    if selected_provider != "auto" and not model:
        model = next((p["model"] for p in route["picks"] if p["provider"] == selected_provider), "")
    if route["private"] and selected_provider not in ("auto", "local"):
        return f"Error: this task is private, so it can't go to {selected_provider}. Use provider='local'."
    if selected_provider == "auto":
        candidates = [p for p in route["picks"] if binaries.get(p["provider"])]
        if not candidates:
            return "Error: no agent available for this task. " + route["reason"]
    else:
        candidates = [{"provider": selected_provider, "model": model}]
        if not binaries.get(selected_provider):
            return f"Error: could not locate the {selected_provider} CLI. Ensure it is installed and on PATH."
    explicit_model = bool(model) and selected_provider == "auto"

    # The budget is checked against the agent that will really run, so 'auto' skips exhausted ones.
    ok, budget_msg = False, ""
    first_failure = ""
    for cand in candidates:
        ok, budget_msg = _s.check_budget_preflight(model=model if explicit_model else cand["model"], provider=cand["provider"],
                                                budget_session_pct=budget_session_pct, budget_weekly_pct=budget_weekly_pct,
                                                max_budget_usd=max_budget_usd)
        if ok:
            selected_provider = cand["provider"]
            model = model if explicit_model else cand["model"]
            break
        first_failure = first_failure or budget_msg
    if not ok:
        _s._broadcast_track_internal(f"[BUDGET GUARD HALT] Worker '{role}' ({model or provider}) blocked: {first_failure}",
                                  track=track, station_id="budget-guard")
        return first_failure

    thread_id = ""
    if thread:
        try:
            thread_chat = _s.resolve(thread, strict=True)
        except ValueError as e:
            return f"Error: {e}"
        if thread_chat["source"] != selected_provider:
            return (f"Error: thread '{thread}' belongs to {thread_chat['source']}, "
                    f"but this worker would run on {selected_provider}.")
        thread_id = thread_chat["id"]
        if selected_provider == "antigravity" and not glob.glob(
                os.path.join(_s.HOME, ".gemini", "antigravity-cli", "conversations", f"{thread_id}*")):
            return (f"Error: thread '{thread}' is an Antigravity IDE chat, and the agy CLI can only continue its own "
                    f"conversations. Leave thread empty, and use send_note to reach that chat.")

    target_model = (model or "").strip()
    if selected_provider == "claude":
        target_model = target_model or ("opus" if chosen_effort == "max" else "haiku" if chosen_effort == "low" else "sonnet")
    elif selected_provider == "local":
        target_model = target_model or _s.choose_local_model(need_tools=True) or ""
        if not target_model:
            return "[MEMORY GUARD] No installed local model that can use tools fits in the memory that's free right now."
    elif selected_provider == "opencode":
        target_model = target_model or "default"
    elif selected_provider == "copilot":
        target_model = target_model or "auto"
    elif selected_provider == "pi":
        if target_model in ("glm-5.3", "deepai/glm-5.3"):
            target_model = "deepai/glm-5.3-flash"
        elif target_model in ("deepseek-v4", "deepai/deepseek-v4", "deepseek-v4.1", "deepai/deepseek-v4.1"):
            target_model = "deepai/deepseek-v4.1-flash"
        elif target_model in ("mimo-2.6", "deepai/mimo-2.6"):
            target_model = "deepai/mimo-2.6-flash"
        elif target_model in ("solar-mini", "deepai/solar-mini"):
            target_model = "deepai/solar-mini-4"
        target_model = target_model or "auto"
    elif selected_provider in custom_map:
        target_model = target_model or ""
    else:
        target_model = target_model or "inherit"

    if target_model and target_model not in ("inherit", "auto", "default"):
        ok, valid_model, err = _s._validate_model_for_provider(selected_provider, target_model)
        if not ok:
            return err
        target_model = valid_model

    worker_id = f"worker_{uuid.uuid4().hex[:6]}"
    copilot_session_id = str(uuid.uuid4()) if selected_provider == "copilot" else ""
    cmd = _s._worker_command("opencode" if selected_provider == "local" else selected_provider, binaries[selected_provider] or "",
                          f"ollama/{target_model}" if selected_provider == "local" else target_model, chosen_effort,
                          is_god_mode, capabilities, permission_mode, prompt, max_budget_usd, thread_id, worker_id,
                          workdir=cwd, isolate=bool(isolate), session_id=copilot_session_id, role=role)
    preflight_lines: List[str] = []
    try:
        import preflight as _preflight
        preflight_lines = _preflight.run(prompt, cwd, selected_provider, target_model)
    except Exception:
        preflight_lines = []
    meta = {
        "id": worker_id,
        "role": role,
        "provider": selected_provider,
        **({"copilot_session_id": copilot_session_id} if copilot_session_id else {}),
        "model": target_model,
        "preflight": preflight_lines,
        "effort": chosen_effort,
        "mode": "god" if is_god_mode else "standard",
        "permission_mode": permission_mode,
        "capabilities": capabilities,
        "role_kind": role_kind(role, capabilities or []),
        "disallowed_tools": ROLE_DISALLOWED_TOOLS.get(role_kind(role, capabilities or []), []),
        "skip_permissions": any(f in cmd for f in ("--dangerously-skip-permissions", "--auto", "--allow-all-tools")),
        "budget": {
            "session_pct": budget_session_pct,
            "weekly_pct": budget_weekly_pct,
            "max_usd": max_budget_usd,
            "max_tokens": int(max_tokens or 0),
            "max_seconds": int(max_seconds or 0),
            "preflight": budget_msg,
        },
        "track": track,
        "project_dir": cwd,
        "thread": thread_id,
        "launched_by": _s._self_id(),
        "swarm_id": swarm_id,
        "isolate": bool(isolate),
        "priority": priority,
        "private": bool(route.get("private")),
        "requested_by": _s._self_id() or os.environ.get("USER") or "user",
        "paid_by": selected_provider,
        "spin_args": {
            "role": role,
            "prompt": original_prompt,
            "provider": selected_provider,
            "track": track,
            "project_dir": cwd,
            "timeout_seconds": int(timeout_seconds or 0),
            "effort": chosen_effort,
            "mode": "god" if is_god_mode else "standard",
            "model": target_model,
            "permission_mode": permission_mode,
            "capabilities": list(capabilities),
            "budget_session_pct": budget_session_pct,
            "budget_weekly_pct": budget_weekly_pct,
            "max_budget_usd": max_budget_usd,
            "thread": thread,
            "private": bool(route.get("private")),
            "max_tokens": int(max_tokens or 0),
            "max_seconds": int(max_seconds or 0),
            "swarm_id": swarm_id,
            "isolate": bool(isolate),
            "route_reason": route_reason,
            "priority": priority,
            "code": list(code) if code is not None else None,
            "attachments": list(attachments) if attachments is not None else None,
            "then": then,
            "review": review,
        },
        "code_hashes": file_hashes,
        "code_ranges": code_ranges,
        "route": route_reason or (route["reason"] if provider in ("auto", "god") else f"You chose {selected_provider}."),
        "then": steps,
        "review": review,
        "usage": {"input": 0, "output": 0, "cache_read": 0},
        "status": "pending_approval",
        "cmd": cmd,
        "timeout_seconds": int(timeout_seconds or 0),
        "pid": None,
        "exit_code": None,
        "created_at": _dt.datetime.now().astimezone().isoformat(),
        "completed_at": None,
        "prompt": prompt,
        "log_file": _s._log_file(worker_id),
        "report": "Awaiting user approval before launching.",
        "secret": secrets.token_hex(16),
    }
    if selected_provider == "copilot":
        meta["input_excludes_cache"] = True
    if lineage_parent:
        # The user approved the parent, so its sub-workers start without a second approval.
        meta["parent_id"] = lineage_parent["id"]
        meta["approved_by"] = f"lineage:{lineage_parent['id']}"
    thread_override = _s._THREAD_PARENT.get()
    thread_extra: Dict[str, Any] = {}
    thread_kind: str = "spawned"
    thread_from: str = "hud"
    if thread_override:
        thread_parent = _s._load_worker(thread_override[0]) or {"id": thread_override[0]}
        meta["thread_root"] = _s._thread_root(thread_parent)
        thread_kind, thread_from = str(thread_override[1]), str(thread_override[0])
        thread_extra = dict(thread_override[2] or {})
    elif lineage_parent:
        meta["thread_root"] = _s._thread_root(lineage_parent)
        thread_kind, thread_from = "spawned", str(lineage_parent["id"])
    else:
        launched_by_str = str(meta.get("launched_by") or "")
        meta["thread_root"] = _s._thread_root_for_caller(launched_by_str) or meta["id"]
        thread_kind, thread_from = "spawned", launched_by_str or "hud"
    _s._save_worker(meta)
    _s._record_activity_spun(meta)
    _s._thread_record(str(meta.get("thread_root") or meta["id"]), thread_kind, thread_from, worker_id, {
        "settings": _s._settings_snapshot(meta), "prompt_head": prompt[:200],
        "approved_by": meta.get("approved_by", ""), **thread_extra})
    if lineage_parent:
        launched = _s._launch_worker(worker_id)
        _s._broadcast_track_internal(f"{lineage_parent['id']} started sub-worker `{worker_id}` ({role}, "
                                  f"{selected_provider.upper()}, {_s.PERMISSION_MODES[permission_mode]}).",
                                  track=track, station_id="guard")
        return _await_block(
            (f"{attach_warn}{launched}\n- Sub-worker of `{lineage_parent['id']}`: approved with its parent, "
             f"permissions: {_s.PERMISSION_MODES[permission_mode]} (never broader than the parent's)."),
            worker_id, wait, wait_timeout_seconds)

    god_warning = ("[WARNING: GOD MODE] This worker runs with permission prompts off and maximum effort.\n"
                   if is_god_mode else "")
    _s._broadcast_track_internal(
        f"[PENDING APPROVAL] Worker `{worker_id}` queued ({role}, {selected_provider.upper()} "
        f"model:{target_model} effort:{chosen_effort}). User authorization required.",
        track=track, station_id="guard")
    import sse_stream
    with contextlib.suppress(Exception):
        sse_stream.publish("worker", {"id": worker_id, "status": "pending_approval", "role": role, "provider": selected_provider})
    _s.notify_if_still_waiting(f"worker_waiting|{worker_id}", f"Worker {worker_id} waiting on your approval ({role})",
                            lambda: (_s._load_worker(worker_id) or {}).get("status") == "pending_approval", delay=0.0)
    budget_line = "" if (budget_session_pct or budget_weekly_pct or max_budget_usd or max_tokens) else (
        "No budget set for this worker. Ask your user whether they want one before it starts.\n")
    thin_brief = _thin_brief_warning(prompt, capabilities)
    overshoot = _overshoot_warning(selected_provider, target_model, chosen_effort, int(max_tokens or 0))
    queued = (f"{attach_warn}{god_warning}{overshoot}{thin_brief}[APPROVAL REQUIRED] Worker `{worker_id}` is queued in PENDING APPROVAL state.\n"
            f"- Role: **{role}**\n"
            f"- Target Provider: **{selected_provider.upper()}**\n"
            f"- Why this agent: {meta['route']}\n"
            f"- Model: **{target_model}** | Effort: **{chosen_effort}** | Mode: **{meta['mode']}**\n"
            f"- Capabilities: `[{', '.join(capabilities)}]`\n"
            f"- Permissions: {'NOT ASKED (prompts off)' if meta['skip_permissions'] else _s.PERMISSION_MODES[permission_mode]}\n"
            f"- Budget: {budget_msg}\n\n"
            f"{budget_line}"
            f"Only your user can launch this worker. They can approve it in the Live HUD, run "
            f"`subway approve {worker_id}`, or give you the approval code shown in those places "
            f"for approve_worker(worker_id='{worker_id}', approval_code=...).")
    if preflight_lines:
        queued += "\n\n- Preflight:\n" + "\n".join(f"  - {line}" for line in preflight_lines)
    return _await_block(queued, worker_id, wait, wait_timeout_seconds)


def _await_block(text: str, worker_id: str, wait: bool, wait_timeout_seconds: int) -> str:
    """Hold the call until the worker finishes, then hand back its own report.

    This is what makes a spawn behave like a native subagent call: one request, one result.
    It stays opt-in, because a client that cannot hold a call open must keep the old
    fire-and-forget behaviour.
    """
    if not wait:
        return text
    timeout = int(wait_timeout_seconds or 0) or int(_s.DEFAULT_WORKER_TIMEOUT)
    meta = _s.wait_for_worker(worker_id, timeout_seconds=timeout)
    status = str(meta.get("status") or "unknown")
    report = str(meta.get("report") or "").strip()
    lines = [text, "", "---", f"[WAITED {timeout}s] Status: {status}"]
    if meta.get("exit_code") is not None:
        lines.append(f"Exit code: {meta['exit_code']}")
    if meta.get("reason"):
        lines.append(f"Reason: {meta['reason']}")
    if meta.get("wait_timed_out"):
        if status == "pending_approval":
            lines.append("Still waiting on your user's approval. Nothing has run yet; call await_worker to keep waiting.")
        else:
            lines.append("Still running. Call await_worker to keep waiting, or worker_status to look in.")
    lines.append("")
    lines.append(report if report else "No final report: the worker did not write one.")
    return "\n".join(lines)


def capacity() -> Dict[str, Any]:
    """How many workers this machine should run at once. Override with max_workers in config.json."""
    cfg = _s._load_json("config.json")
    mem = _s.memory_snapshot()
    avail = mem["available"]
    cpus = os.cpu_count() or 1
    limit = max(1, min(int(avail / (500 * 1024 * 1024)), cpus * 2))
    custom = cfg.get("max_workers")
    custom_int: Optional[int] = int(custom) if isinstance(custom, (int, float, str)) and str(custom).isdigit() and int(custom) > 0 else None
    machine = custom_int if custom_int is not None else limit
    chats = {w.get("launched_by") for w in _s._all_workers() if w.get("status") in ("running", "launching", "stopping")}
    worker_limits = cfg.get("worker_limits", True) is not False
    loc_cap = _s.local_capacity() if hasattr(_s, "local_capacity") else {}
    res = {
        "machine": machine,
        "auto_limit": limit,
        "custom_limit": custom_int,
        "chat": max(1, machine // max(1, len(chats))),
        "chats": len(chats),
        "available": avail,
        "free_ram_gb": round(avail / _s.GB, 2),
        "worker_limits": worker_limits,
        "max_workers": custom_int,
        "local": loc_cap,
        "local_memory_guard": loc_cap.get("local_memory_guard", True),
        "local_concurrency_limits": loc_cap.get("local_concurrency_limits", True),
        "max_local_workers": loc_cap.get("max_local_workers"),
        "auto_local_workers": loc_cap.get("auto_local_workers", 1),
        "effective_local_workers": loc_cap.get("effective_local_workers", 1),
    }
    if not worker_limits:
        res["limits_off"] = True
    return res


def _capacity_block(meta: Dict[str, Any]) -> str:
    cfg = _s._load_json("config.json")
    is_god = cfg.get("god_mode") is True or str(meta.get("mode") or "").lower() == "god"
    cap = _s.capacity()
    if is_god:  # God mode has no worker count: only free memory can hold it back.
        return f"[CAPACITY] Only {cap['available'] / _s.GB:.1f} GB of memory is free. It stays queued." if cap["available"] < _s.WORKER_RAM else ""

    # Check local model concurrency and memory guard if local provider
    if meta.get("provider") == "local":
        running_local = [w for w in _s._all_workers() if w.get("status") in ("running", "launching", "stopping") and w.get("provider") == "local" and w.get("id") != meta.get("id")]
        loc_cap = cap.get("local") or (_s.local_capacity() if hasattr(_s, "local_capacity") else {})
        if loc_cap.get("local_concurrency_limits", True) is not False:
            allowed_local = loc_cap.get("effective_local_workers", 1)
            if len(running_local) >= allowed_local:
                return f"[CAPACITY] {len(running_local)} local model workers are already running (limit: {allowed_local}). It stays queued."
        if loc_cap.get("local_memory_guard", True) is not False:
            fit, reason = _s.local_fit(meta.get("model") or "")
            if not fit:
                return f"[CAPACITY] {reason} It stays queued."

    if cfg.get("worker_limits") is False:
        return ""
    running = [w for w in _s._all_workers() if w.get("status") in ("running", "launching", "stopping")]
    if len(running) >= cap["machine"]:
        return f"[CAPACITY] {len(running)} workers are running, and this machine's limit is {cap['machine']}. It stays queued."
    chats = {w.get("launched_by") for w in running}
    chats.add(meta.get("launched_by"))
    num_chats = max(1, len(chats))
    chat_share = max(1, cap["machine"] // num_chats)
    mine = [w for w in running if w.get("launched_by") == meta.get("launched_by")]
    if len(mine) >= chat_share:
        return f"[CAPACITY] This chat already has {len(mine)} workers running, and the limit is {chat_share}. It stays queued."
    if cap["available"] < _s.WORKER_RAM:
        return f"[CAPACITY] Only {cap['available'] / _s.GB:.1f} GB of memory is free. It stays queued."
    return ""


def _git(repo: str, *args: str) -> str:
    return subprocess.check_output(["git", "-C", repo, *args], stderr=subprocess.STDOUT, text=True, timeout=60).strip()


def _worktree_base(repo: str) -> str:
    """Create a temporary commit holding all tracked, modified, and untracked (non-ignored) files.
    Falls back to `git stash create -u` or HEAD on error."""
    tmp_fd, tmp_idx = tempfile.mkstemp(prefix="subway_idx_")
    os.close(tmp_fd)
    try:
        env = dict(os.environ, GIT_INDEX_FILE=tmp_idx, GIT_WORK_TREE=repo)
        subprocess.check_call(["git", "-C", repo, "read-tree", "HEAD"], env=env, stderr=subprocess.DEVNULL)
        subprocess.check_call(["git", "-C", repo, "add", "-A"], env=env, stderr=subprocess.DEVNULL)
        tree = subprocess.check_output(["git", "-C", repo, "write-tree"], env=env, text=True, stderr=subprocess.DEVNULL).strip()
        head = _s._git(repo, "rev-parse", "HEAD")
        return subprocess.check_output(
            ["git", "-C", repo, "commit-tree", "-p", head, "-m", "subway worktree base", tree],
            env=env, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return _s._git(repo, "stash", "create", "-u") or _s._git(repo, "rev-parse", "HEAD")
    finally:
        with contextlib.suppress(OSError):
            if os.path.exists(tmp_idx):
                os.remove(tmp_idx)


def _prepare_worktree(meta: Dict[str, Any]) -> None:
    """Give a worker its own copy of the repo, including uncommitted work, so workers can't overwrite each other."""
    repo = _s._git(meta["project_dir"], "rev-parse", "--show-toplevel")
    base = _worktree_base(repo)
    path = os.path.join(_s.SUBWAY_DIR, "worktrees", meta["id"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cfg = _s._load_json("config.json")
    branch_mode = bool(cfg.get("git_branch_mode") or meta.get("branch_mode"))
    if os.path.exists(path):
        raise OSError(f"{path} already exists from an earlier launch of {meta['id']}. "
                      f"If no worker runs there, remove it with `git worktree remove --force {path}`.")
    if branch_mode:
        role_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(meta.get("role") or "worker")).strip("-")[:20]
        branch_name = f"subway/{meta['id']}-{role_slug}"
        if _s._git(repo, "branch", "--list", branch_name):
            raise OSError(f"branch {branch_name} already exists from an earlier launch of {meta['id']}; "
                          f"reject this worker or delete the branch.")
        _s._git(repo, "worktree", "add", "-b", branch_name, path, base)
        meta["branch_name"] = branch_name
    else:
        _s._git(repo, "worktree", "add", "--detach", path, base)
    shared_nm = os.path.join(repo, "node_modules")  # Ignored by git, so link it instead of copying it.
    if os.path.isdir(shared_nm) and not os.path.lexists(os.path.join(path, "node_modules")):
        with contextlib.suppress(OSError):
            os.symlink(shared_nm, os.path.join(path, "node_modules"))
    if meta.get("seed_patch"):
        seed_patch = str(meta["seed_patch"])
        err = ""
        if not os.path.isfile(seed_patch):
            err = "missing file"
        elif os.path.getsize(seed_patch) == 0:
            err = "empty file"
        else:
            try:
                proc = subprocess.run(
                    ["git", "-C", path, "apply", "--whitespace=nowarn", seed_patch],
                    capture_output=True, text=True, timeout=60,
                )
                if proc.returncode != 0:
                    lines = (proc.stderr or "").strip().splitlines()
                    err = lines[0].strip() if lines else f"git apply exited with code {proc.returncode}"
            except Exception as e:
                lines = str(e).strip().splitlines()
                err = lines[0].strip() if lines else "apply failed"
        if not err:
            meta["seed_note"] = "Started from your previous round's work."
        else:
            meta["seed_note"] = f"Could not carry over your previous round's work ({err}); start from the main tree."
    meta.update({"repo": repo, "base": base, "worktree": path})
    proj = meta.get("project_dir") or ""
    old_prompt = meta.get("prompt") if isinstance(meta.get("prompt"), str) else None
    if proj:
        if "prompt" in meta and isinstance(meta["prompt"], str):
            meta["prompt"] = meta["prompt"].replace(proj, path)
    dir_msg = f"\nYour working directory is your private worktree {path}; edit files only there."
    if "prompt" in meta and isinstance(meta["prompt"], str) and dir_msg not in meta["prompt"]:
        meta["prompt"] = f"{meta['prompt']}{dir_msg}"
    seed_note = meta.get("seed_note")
    if seed_note:
        if "prompt" in meta and isinstance(meta["prompt"], str):
            if seed_note not in meta["prompt"]:
                meta["prompt"] = f"{meta['prompt']}\n{seed_note}"
        else:
            meta["prompt"] = f"\n{seed_note}"
    if "cmd" in meta and isinstance(meta["cmd"], list):
        new_cmd = []
        for arg in meta["cmd"]:
            if isinstance(arg, str):
                new_arg = arg
                if old_prompt and old_prompt in new_arg:
                    new_arg = new_arg.replace(old_prompt, meta["prompt"])
                if proj and proj in new_arg:
                    new_arg = new_arg.replace(proj, path)
                if dir_msg not in new_arg and (path in new_arg or (old_prompt and old_prompt in arg)):
                    # ensure dir_msg is included if this arg corresponds to prompt
                    pass
                new_cmd.append(new_arg)
            else:
                new_cmd.append(arg)
        meta["cmd"] = new_cmd


def _fallback_copilot_acp(meta: Dict[str, Any], workdir: str) -> bool:
    """Replace a failed Copilot ask-mode ACP start with the safe-command allow list."""
    if (meta.get("provider") != "copilot" or meta.get("permission_mode") != "ask"
            or "--acp" not in (meta.get("cmd") or [])):
        return False

    budget = meta.get("budget") or {}
    cmd = _s._worker_command(
        "copilot", (meta.get("cmd") or ["copilot"])[0], str(meta.get("model") or "auto"),
        str(meta.get("effort") or "auto"), meta.get("mode") == "god",
        list(meta.get("capabilities") or []), "auto_safe", str(meta.get("prompt") or ""),
        budget.get("max_usd"), str(meta.get("thread") or ""), str(meta.get("id") or ""),
        workdir=workdir, isolate=bool(meta.get("isolate")), session_id=str(meta.get("copilot_session_id") or ""),
        role=str(meta.get("role") or ""),
    )
    note = "Copilot ACP unavailable; running with safe-command auto-approval"
    with _s._state_lock("workers"):
        current = _s._load_worker(str(meta.get("id") or "")) or meta
        current.update({"cmd": cmd, "start_note": note})
        _s._save_worker(current)
    meta.update({"cmd": cmd, "start_note": note})
    _s._ping_launcher(str(meta.get("launched_by") or ""), f"[WORKER START NOTE] {note}.", from_id=str(meta.get("id") or ""))
    return True


def _repo_has_uncommitted_changes(repo: str) -> bool:
    """True when the target repo has edits that are not committed.

    Merging a worker's branch into a dirty tree mixes its change with somebody else's
    uncommitted work. That is how a worker merge (or a later `git add -A`) sweeps up and
    reverts unrelated edits: it happened on 2026-11-08, when a commit meant to fix an
    install path carried 468 lines of another agent's HUD work and reverted
    modelProvider.memory. A dirty tree is a reason to keep the branch, not to merge.
    """
    try:
        out = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return True  # can't tell: treat as dirty and leave the branch alone
    return bool(out.strip())


def _collect_worktree(meta: Dict[str, Any], partial: bool = False) -> str:
    """Bring a worker's change back as a patch or merge commit. A clash or partial is left for review instead of corrupting main."""
    repo, path, base = meta["repo"], meta["worktree"], meta["base"]
    branch_name = meta.get("branch_name")
    if branch_name:
        _s._git(path, "add", "-A")
        diff = subprocess.run(["git", "-C", path, "diff", "--cached", "--binary", base], capture_output=True, text=True, timeout=60).stdout
        note = "No files changed."
        if diff.strip():
            role = meta.get("role") or "worker"
            _s._git(path, "commit", "-m", f"{role}: finished worker {meta['id']}")
            if partial:
                meta["partial_patch"] = True
                note = f"Partial branch kept: {branch_name}."
            elif _repo_has_uncommitted_changes(repo):
                # Never merge into a dirty tree: the worker's work is safe on its branch,
                # and mixing it with uncommitted edits is how unrelated code gets reverted.
                note = (f"Branch '{branch_name}' preserved: {repo} has uncommitted changes, "
                        "so it was not merged. Commit or stash them, then merge the branch.")
            else:
                try:
                    _s._git(repo, "merge", "--no-ff", "-m", f"subway: merge worker '{role}' ({meta['id']})", branch_name)
                    note = f"Branch '{branch_name}' merged into working tree."
                except Exception as e:
                    with contextlib.suppress(Exception):
                        _s._git(repo, "merge", "--abort")
                    note = f"Merge conflict on '{branch_name}': {e}. Preserved branch for review."
        else:
            with contextlib.suppress(Exception):
                _git(repo, "branch", "-D", branch_name)
            meta["branch_deleted"] = True
        with contextlib.suppress(Exception):
            _s._git(repo, "worktree", "remove", "--force", path)
        return note

    _s._git(path, "add", "-A")
    diff = subprocess.run(["git", "-C", path, "diff", "--cached", "--binary", base], capture_output=True, text=True, timeout=60).stdout
    note = "No files changed."
    if diff.strip():
        patch = os.path.join(_s.WORKERS_DIR, f"{meta['id']}.patch")
        with open(patch, "w", encoding="utf-8") as f:
            f.write(diff)
        if partial:
            note = f"Partial patch kept: {patch}."
            meta["partial_patch"] = True
        else:
            applied = subprocess.run(["git", "-C", repo, "apply", "--whitespace=nowarn", patch], capture_output=True, text=True, timeout=60)
            note = "Patch applied to the working tree." if applied.returncode == 0 else f"Patch clashes, not applied: {patch}. {_s.one_line(applied.stderr, 160)}"
    with contextlib.suppress(Exception):
        _s._git(repo, "worktree", "remove", "--force", path)
    return note


def prune_worker_branches(repo: str, days: int = 14, dry_run: bool = True) -> Dict[str, Any]:
    """Delete stale finished worker branches and their worktrees.

    Order matters: live workers and recent work are never touched, and anything with
    real unmerged changes (unless explicitly rejected) is kept for review.
    """
    deleted: List[str] = []
    applied: List[str] = []
    worktrees_removed: List[str] = []
    kept: List[Dict[str, str]] = []
    cutoff = time.time() - days * 86400
    wt_by_branch: Dict[str, str] = {}
    path: Optional[str] = None
    for line in _git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):].strip()
        elif line.startswith("branch ") and path:
            wt_by_branch[line[len("branch "):].strip().replace("refs/heads/", "")] = path
            path = None
    for line in _git(repo, "for-each-ref", "--format=%(refname:short) %(committerdate:unix)", "refs/heads/subway/").splitlines():
        parts = line.split()
        if len(parts) != 2 or "worker_" not in parts[0]:
            continue
        branch, committed = parts[0], float(parts[1])
        m = re.search(r"worker_[0-9a-f]{6}", branch)
        worker_id = m.group(0) if m else ""
        meta = _s._load_worker(worker_id) if worker_id else None
        status = (meta or {}).get("status")
        if status in ("pending", "pending_approval", "approved", "queued", "running"):
            kept.append({"branch": branch, "reason": "active"})
            continue
        if committed > cutoff:
            kept.append({"branch": branch, "reason": "recent"})
            continue
        wt = wt_by_branch.get(branch)
        if wt and _git(wt, "status", "--porcelain"):
            kept.append({"branch": branch, "reason": "checked_out_dirty"})
            continue
        base = _git(repo, "log", "--format=%H", "--grep=subway worktree base", "-1", branch)
        if not base:
            kept.append({"branch": branch, "reason": "no_base"})
            continue
        differs = subprocess.run(["git", "-C", repo, "diff", "--quiet", base, branch],
                                  capture_output=True, timeout=60).returncode != 0
        if differs and status != "rejected":
            diff_proc = subprocess.run(["git", "-C", repo, "diff", base, branch],
                                       capture_output=True, timeout=60)
            apply_check = subprocess.run(["git", "-C", repo, "apply", "-R", "--check"],
                                         input=diff_proc.stdout, capture_output=True, timeout=60)
            if apply_check.returncode == 0:
                applied.append(branch)
            else:
                kept.append({"branch": branch, "reason": "unmerged"})
                continue
        if dry_run:
            if wt:
                worktrees_removed.append(wt)
            deleted.append(branch)
        else:
            if wt:
                with contextlib.suppress(Exception):
                    _git(repo, "worktree", "remove", wt)
                worktrees_removed.append(wt)
            _git(repo, "branch", "-D", branch)
            deleted.append(branch)
    if not dry_run:
        with contextlib.suppress(Exception):
            _git(repo, "worktree", "prune")
    return {"deleted": deleted, "applied": applied, "worktrees_removed": worktrees_removed, "kept": kept, "dry_run": dry_run}


def t_prune_branches(days: int = 14, dry_run: bool = True, project_dir: str = "") -> str:
    repo = os.path.realpath(project_dir or os.getcwd())
    res = prune_worker_branches(repo, days=days, dry_run=dry_run)
    verb = "would delete" if dry_run else "deleted"
    out = ([f"[PRUNE] dry run: {verb} {len(res['deleted'])} branches, "
            f"remove {len(res['worktrees_removed'])} worktrees, keep {len(res['kept'])}"]
           if dry_run else
           [f"[PRUNE] deleted {len(res['deleted'])} branches, "
            f"removed {len(res['worktrees_removed'])} worktrees, keep {len(res['kept'])}"])
    if res["deleted"]:
        out.append("deleted: " + ", ".join(res["deleted"][:20]))
    if res["worktrees_removed"]:
        out.append("worktrees: " + ", ".join(res["worktrees_removed"][:20]))
    if res["kept"]:
        counts: Dict[str, int] = {}
        for k in res["kept"]:
            counts[k["reason"]] = counts.get(k["reason"], 0) + 1
        out.append("kept: " + ", ".join(f"{r}={n}" for r, n in sorted(counts.items())))
        out.append("kept branches: " + ", ".join(k["branch"] for k in res["kept"][:20]))
    return "\n".join(out)


def _verify_result(status: str, reasons: List[str]) -> Dict[str, Any]:
    if status == "PASS":
        line = "PASS"
    else:
        line = f"{status}: {'; '.join(reasons)}"
    line = line.replace("\n", " ").strip()
    return {
        "status": status,
        "reasons": reasons,
        "line": line,
        "ok": status == "PASS",
    }


def verify_worker(worker_id: Any) -> Dict[str, Any]:
    """Fail-closed checklist for worker patch verification."""
    meta: Dict[str, Any]
    if isinstance(worker_id, dict):
        meta = dict(worker_id)
        wid = str(meta.get("id") or "worker")
    else:
        wid = str(worker_id)
        meta_file = os.path.join(_s.WORKERS_DIR, f"{wid}.json")
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception as e:
                return _s._verify_result("FAIL", [f"failed to read worker metadata: {e}"])
        else:
            meta = {"id": wid}

    def _finish(res: Dict[str, Any]) -> Dict[str, Any]:
        if wid:
            with contextlib.suppress(Exception):
                w_meta: Dict[str, Any] = _s._load_worker(wid) or dict(meta)
                w_meta["verify"] = {"status": res["status"], "line": res["line"], "at": time.time()}
                _s._save_worker(w_meta)
        return res

    def _vr(status: str, reasons: List[str]) -> Dict[str, Any]:
        return _finish(_s._verify_result(status, reasons))  # A local of the same name would hide the global one.

    repo = meta.get("repo") or os.getcwd()
    base = meta.get("base") or "HEAD"
    patch_file = meta.get("patch_path") or os.path.join(_s.WORKERS_DIR, f"{wid}.patch")
    if not os.path.exists(patch_file) and os.path.isfile(wid):
        patch_file = wid

    if not os.path.exists(patch_file):
        return _vr("FAIL", [f"patch file not found: {patch_file}"])

    try:
        with open(patch_file, "r", encoding="utf-8", errors="replace") as f:
            patch_text = f.read()
    except Exception as e:
        return _vr("FAIL", [f"cannot read patch: {e}"])

    if not patch_text.strip():
        return _vr("FAIL", ["empty patch"])

    # 1) Patch applies (git apply --check)
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "apply", "--check", "--whitespace=nowarn", patch_file],
            capture_output=True, text=True, timeout=30
        )
        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip() or "git apply --check failed"
            return _vr("FAIL", [f"patch does not apply: {_s.one_line(err, 120)}"])
    except subprocess.TimeoutExpired:
        return _vr("FAIL", ["timeout running git apply --check"])
    except Exception as e:
        return _vr("FAIL", [f"git apply --check failed: {e}"])

    # Parse changed files, deleted lines, and added python lines
    changed_files: List[str] = []
    deleted_lines: List[str] = []
    added_py_lines: Dict[str, Set[int]] = {}
    current_file: Optional[str] = None
    current_new_line = 0

    def is_exec_line(s: str) -> bool:
        t = s.strip()
        if not t or t.startswith("#"):
            return False
        if t in ('"""', "'''", '):', ']:', '}', ']', ')'):
            return False
        return True

    for raw_line in patch_text.splitlines():
        if raw_line.startswith("diff --git "):
            parts = raw_line.split()
            if len(parts) >= 4 and parts[3].startswith("b/"):
                current_file = parts[3][2:]
                if current_file not in changed_files:
                    changed_files.append(current_file)
        elif raw_line.startswith("+++ b/"):
            current_file = raw_line[6:].strip()
            if current_file and current_file != "/dev/null" and current_file not in changed_files:
                changed_files.append(current_file)
        elif raw_line.startswith("@@ "):
            m = re.search(r"\+(\d+)(?:,(\d+))?", raw_line)
            if m:
                current_new_line = int(m.group(1))
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            if current_file and current_file.endswith(".py"):
                if is_exec_line(raw_line[1:]):
                    added_py_lines.setdefault(current_file, set()).add(current_new_line)
            current_new_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            deleted_lines.append(raw_line[1:])
        else:
            if not raw_line.startswith("\\"):
                current_new_line += 1

    fail_reasons: List[str] = []
    flag_reasons: List[str] = []

    # 4) Flag, never pass, if the patch deletes tests or removes assert lines
    for dl in deleted_lines:
        s = dl.strip()
        if re.search(r"^\s*assert\b", s) or "self.assert" in s:
            if "patch removes assert line" not in flag_reasons:
                flag_reasons.append("patch removes assert line")
        if re.search(r"^\s*def\s+test_", s) or re.search(r"^\s*class\s+Test", s):
            if "patch deletes test" not in flag_reasons:
                flag_reasons.append("patch deletes test")

    # 5) Flag files outside the brief's files when meta lists them
    brief_files = meta.get("files")
    if brief_files is not None:
        brief_set = {os.path.normpath(f) for f in brief_files}
        for cf in changed_files:
            if os.path.normpath(cf) not in brief_set:
                flag_reasons.append(f"file outside brief: {cf}")

    # Set up isolated worktree for compilation and test execution
    tmp_worktree = tempfile.mkdtemp(prefix="subway_verify_")
    worktree_created = False
    try:
        wt_cmd = subprocess.run(
            ["git", "-C", repo, "worktree", "add", "--detach", tmp_worktree, base],
            capture_output=True, text=True, timeout=30
        )
        if wt_cmd.returncode != 0:
            return _vr("FAIL", [f"failed to create temporary worktree: {_s.one_line(wt_cmd.stderr, 120)}"])
        worktree_created = True

        app_cmd = subprocess.run(
            ["git", "-C", tmp_worktree, "apply", "--whitespace=nowarn", patch_file],
            capture_output=True, text=True, timeout=30
        )
        if app_cmd.returncode != 0:
            return _vr("FAIL", [f"failed to apply patch in worktree: {_s.one_line(app_cmd.stderr, 120)}"])

        # 2) Every changed .py compiles and every changed .js passes node --check if node exists
        import py_compile
        for cf in changed_files:
            target_path = os.path.join(tmp_worktree, cf)
            if not os.path.exists(target_path):
                continue
            if cf.endswith(".py"):
                try:
                    py_compile.compile(target_path, doraise=True)
                except py_compile.PyCompileError as e:
                    fail_reasons.append(f"compilation error in {cf}: {e}")
            elif cf.endswith(".js") and shutil.which("node"):
                nproc = subprocess.run(["node", "--check", target_path], capture_output=True, text=True, timeout=15)
                if nproc.returncode != 0:
                    fail_reasons.append(f"node --check failed for {cf}: {_s.one_line(nproc.stderr, 120)}")

        # Check JS imports in hud/*.js
        import glob
        hud_dir = os.path.join(tmp_worktree, "hud")
        if os.path.isdir(hud_dir):
            for js_file in sorted(glob.glob(os.path.join(hud_dir, "*.js"))):
                try:
                    with open(js_file, "r", encoding="utf-8", errors="replace") as f:
                        js_code = f.read()
                except Exception:
                    continue
                for m in re.finditer(r'import\s*\{([^}]+)\}\s*from\s*[\'"]\.\/([^\'"]+)[\'"]', js_code):
                    names_part = m.group(1)
                    target_rel = m.group(2)
                    target_name = os.path.basename(target_rel)
                    target_path = os.path.normpath(os.path.join(os.path.dirname(js_file), target_rel))
                    if not os.path.exists(target_path):
                        fail_reasons.append(f"{target_name} not found")
                        continue
                    try:
                        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                            tcode = f.read()
                    except Exception as e:
                        fail_reasons.append(f"cannot read {target_name}: {e}")
                        continue
                    exported: Set[str] = set()
                    for em in re.finditer(r'export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([a-zA-Z0-9_$]+)', tcode):
                        exported.add(em.group(1))
                    for eb in re.finditer(r'export\s*\{([^}]+)\}', tcode):
                        for item in eb.group(1).split(","):
                            item = item.split("//")[0].strip()
                            if item:
                                exported.add(item.split()[-1])
                    for item in names_part.split(","):
                        item = item.split("//")[0].strip()
                        if not item:
                            continue
                        imported_name = item.split()[0]
                        if imported_name not in exported:
                            fail_reasons.append(f"{target_name} doesn't export {imported_name}")

        if fail_reasons:
            return _vr("FAIL", fail_reasons)

        # 3) Both test suites run, and pass only if tests ran > 0, none failed, and the count is not lower than the base commit's
        test_suites = meta.get("test_suites") or ["tests.test_subway", "tests.test_budgets"]

        # Base commit test count
        base_cmd = [
            sys.executable, "-c",
            f"import unittest; suites={json.dumps(test_suites)}; s=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromName(t) for t in suites]); print(s.countTestCases())"
        ]
        base_proc = subprocess.run(base_cmd, cwd=repo, capture_output=True, text=True, timeout=30)
        base_count = int(base_proc.stdout.strip()) if (base_proc.returncode == 0 and base_proc.stdout.strip().isdigit()) else 0

        # Run test suites in worktree with stdlib trace
        runner_code = (
            "import sys, unittest, trace, json, os\n"
            f"test_suites = {json.dumps(test_suites)}\n"
            "loader = unittest.defaultTestLoader\n"
            "suite = unittest.TestSuite()\n"
            "for s in test_suites:\n"
            "    try:\n"
            "        suite.addTests(loader.loadTestsFromName(s))\n"
            "    except Exception as e:\n"
            "        print(json.dumps({'error': f'Failed loading {s}: {e}'}))\n"
            "        sys.exit(2)\n"
            "total_loaded = suite.countTestCases()\n"
            "tracer = trace.Trace(count=1, trace=0)\n"
            "runner = unittest.TextTestRunner(stream=open(os.devnull, 'w'), verbosity=0)\n"
            "res = tracer.runfunc(runner.run, suite)\n"
            "traced = tracer.results().counts\n"
            "executed = {}\n"
            "for (fname, lineno), cnt in traced.items():\n"
            "    if cnt > 0:\n"
            "        executed.setdefault(fname, []).append(lineno)\n"
            "print(json.dumps({\n"
            "    'tests_run': res.testsRun,\n"
            "    'failures': len(res.failures),\n"
            "    'errors': len(res.errors),\n"
            "    'total_loaded': total_loaded,\n"
            "    'was_successful': res.wasSuccessful(),\n"
            "    'executed': executed\n"
            "}))\n"
        )

        test_run = subprocess.run(
            [sys.executable, "-c", runner_code],
            cwd=tmp_worktree, capture_output=True, text=True, timeout=120
        )
        if test_run.returncode != 0:
            err = test_run.stderr.strip() or test_run.stdout.strip()
            return _vr("FAIL", [f"test runner crashed: {_s.one_line(err, 120)}"])

        try:
            test_data = json.loads(test_run.stdout.strip())
        except Exception as e:
            return _vr("FAIL", [f"invalid test runner output: {e}"])

        if "error" in test_data:
            return _vr("FAIL", [test_data["error"]])

        tests_run = test_data.get("tests_run", 0)
        failures = test_data.get("failures", 0)
        errors = test_data.get("errors", 0)
        successful = test_data.get("was_successful", False)

        if tests_run <= 0:
            fail_reasons.append("tests ran <= 0")
        if failures > 0 or errors > 0 or not successful:
            fail_reasons.append(f"tests failed ({failures} failures, {errors} errors)")
        if base_count > 0 and tests_run < base_count:
            fail_reasons.append(f"test count lower than base ({tests_run} < {base_count})")

        if fail_reasons:
            return _vr("FAIL", fail_reasons)

        # 6) Flag 'untested change' when changed Python lines never execute during tests
        executed_map = test_data.get("executed", {})
        for cf, added_lines in added_py_lines.items():
            full_cf = os.path.realpath(os.path.join(tmp_worktree, cf))
            exec_set: Set[int] = set()
            for fname, lnos in executed_map.items():
                if os.path.realpath(fname) == full_cf or fname.endswith("/" + cf) or fname == cf:
                    exec_set.update(lnos)
            untested = added_lines - exec_set
            if untested:
                flag_reasons.append(f"untested change in {cf} (lines {sorted(list(untested))[:5]})")

    except subprocess.TimeoutExpired:
        return _vr("FAIL", ["operation timed out during verification"])
    except Exception as e:
        return _vr("FAIL", [f"unexpected error: {e}"])
    finally:
        if worktree_created:
            subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", tmp_worktree], capture_output=True, timeout=30)
            shutil.rmtree(tmp_worktree, ignore_errors=True)

    if fail_reasons:
        return _vr("FAIL", fail_reasons)
    if flag_reasons:
        return _vr("FLAG", flag_reasons)
    return _vr("PASS", [])


def start_approved_workers() -> None:
    """Start approved workers that were waiting for a slot, oldest first."""
    waiting = [w for w in _s._all_workers() if w.get("status") == "pending_approval" and w.get("approved")]
    for w in sorted(waiting, key=lambda x: str(x.get("created_at") or "")):
        if _s._capacity_block(w):
            return
        _s._launch_worker(w["id"])


def _launch_worker(worker_id: str) -> str:
    """Start a queued worker. Callers must have established that the user approved it."""
    with contextlib.suppress(Exception):
        _s.sync_caffeinate()  # starting a worker should keep the machine awake
    with _s._state_lock("workers"):  # The transition is atomic, so a double click can't launch twice.
        meta = _s._load_worker(worker_id)
        if not meta:
            return f"Error: worker `{worker_id}` not found."
        if meta.get("status") != "pending_approval":
            return f"Worker `{worker_id}` is not pending approval (current status: {meta.get('status')})."
        if not meta.get("cmd"):
            return f"Error: no command stored for worker `{worker_id}`."
        for fpath, expected_hash in (meta.get("code_hashes") or {}).items():
            current_hash = _s._file_sha256(fpath) if os.path.exists(fpath) else None
            if current_hash != expected_hash:
                return f"Error: source file '{fpath}' has changed since worker was queued."
        blocked = _s._capacity_block(meta)
        if blocked:
            meta["approved"] = True  # The user said yes: it starts by itself when a slot opens.
            _s._save_worker(meta)
            return f"{blocked} It's approved, and will start by itself when a slot opens."
        meta["status"] = "launching"
        _s._save_worker(meta)  # Claim it now: a second approval sees "launching" and is refused.
    # Worktree and git-state work can take longer than the lock's 10 s timeout, so it runs unlocked.
    # Holding the lock here let a second approval time out, proceed, and clobber the running record.
    if meta.get("isolate"):
        try:
            _s._prepare_worktree(meta)
        except (OSError, subprocess.SubprocessError) as exc:
            in_repo = subprocess.run(["git", "-C", str(meta.get("project_dir")), "rev-parse", "--is-inside-work-tree"],
                                     capture_output=True).returncode == 0
            if in_repo:
                meta["status"] = "pending_approval"
                _s._save_worker(meta)
                detail = (getattr(exc, "stderr", "") or "").strip().splitlines()
                msg = detail[-1] if detail else str(exc)
                return f"Error: couldn't create a worktree: {msg}"
            meta["isolate"] = False  # Not a git folder, so there's nothing to copy: it works in place.
        if meta.get("isolate") and meta.get("worktree"):
            path = meta["worktree"]
            proj = meta.get("project_dir") or ""
            dir_msg = f"\nYour working directory is your private worktree {path}; edit files only there."
            old_prompt = meta.get("prompt") if isinstance(meta.get("prompt"), str) else None
            if "prompt" in meta and isinstance(meta["prompt"], str) and dir_msg not in meta["prompt"]:
                meta["prompt"] = f"{meta['prompt']}{dir_msg}"
            if "cmd" in meta and isinstance(meta["cmd"], list):
                new_cmd = []
                for arg in meta["cmd"]:
                    if isinstance(arg, str):
                        new_arg = arg
                        if old_prompt and old_prompt in new_arg:
                            new_arg = new_arg.replace(old_prompt, meta["prompt"])
                        if proj and proj in new_arg:
                            new_arg = new_arg.replace(proj, path)
                        new_cmd.append(new_arg)
                    else:
                        new_cmd.append(arg)
                meta["cmd"] = new_cmd
    if not meta.get("isolate") and _s._worker_expected_to_edit(meta):
        try:
            git_state = _s._git_change_fingerprint(meta.get("project_dir") or _s.HOME)
            git_dirty_before = _s._git_dirty_paths(meta.get("project_dir") or _s.HOME)
        except (OSError, subprocess.SubprocessError) as exc:
            meta["status"] = "pending_approval"
            _s._save_worker(meta)
            return f"Error: couldn't record starting git state: {exc}"
        if git_state is not None:
            meta["git_state_before"] = git_state
            meta["git_dirty_before"] = git_dirty_before or []
    _s._save_worker(meta)

    # A supervisor that dies leaves the worker reporting "supervisor disappeared before
    # reporting" with no evidence, because its stderr went to DEVNULL. Keep it in a file so
    # the cause is readable instead of guessed.
    sup_log = _s._supervisor_log_file(worker_id)
    with contextlib.suppress(OSError):
        _s._ensure_subway_dir()
        os.makedirs(os.path.dirname(sup_log), exist_ok=True)
    try:
        err_handle = open(sup_log, "ab", buffering=0)
    except OSError:
        err_handle = subprocess.DEVNULL
    try:
        proc = subprocess.Popen([sys.executable, os.path.abspath(_s.__file__), "__supervise", worker_id],
                                cwd=meta.get("project_dir") or _s.HOME, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=err_handle, start_new_session=True)
    except OSError as exc:
        meta.update({"status": "failed", "report": f"Could not start supervisor: {exc}"})
        _s._save_worker(meta)
        if not meta.get("iteration_id"):
            _s.notify_desktop_event(f"worker_ended|{worker_id}|failed", "Subway",
                                 f"Worker {worker_id} failed to start: {exc}", route="workers")
        return f"Error spawning approved worker process: {exc}"
    close_fn = getattr(err_handle, "close", None)
    if callable(close_fn):
        with contextlib.suppress(Exception):
            close_fn()  # the child keeps its own descriptor
    threading.Thread(target=proc.wait, daemon=True).start()  # Reap the child, so no zombie lingers.

    meta.update({"status": "running", "pid": proc.pid, "report": "",
                 "started_at": _dt.datetime.now().astimezone().isoformat()})
    _s._save_worker(meta)
    _s._thread_record(meta.get("thread_root") or _s._thread_root(meta), "started", worker_id, data={"pid": proc.pid})
    _s._broadcast_track_internal(
        f"[WORKER APPROVED & LAUNCHED] Worker `{worker_id}` [{meta.get('provider', '').upper()}] "
        f"(PID {proc.pid}) started for role: '{meta.get('role')}'",
        track=meta.get("track", "main"), station_id=worker_id)
    import sse_stream
    with contextlib.suppress(Exception):
        sse_stream.publish("worker", {"id": worker_id, "status": "running", "role": meta.get("role"), "provider": meta.get("provider")})
    return (f"[APPROVED & LAUNCHED] Worker `{worker_id}` [{meta.get('provider', '').upper()}] (PID {proc.pid}) is now running.\n"
            f"Live Log: `{meta.get('log_file')}`\n"
            f"Call watch_workers now and again: its permission requests come to this chat.")


def notify_if_still_waiting(key: str, message: str, still_waiting: Callable[[], bool], delay: float = 20.0,
                            route: str = "workers") -> None:
    """Alert only for what's really waiting on the user: most requests are answered or launched within seconds."""
    def later() -> None:
        time.sleep(delay)
        try:
            if still_waiting():
                _s.notify_desktop_event(key, "Subway", message, route=route)
        except Exception as exc:
            _s._log_notification_failure("waiting check", exc)
    threading.Thread(target=later, daemon=True).start()


def _active_subworkers(worker_id: str) -> List[str]:
    """Ids of queued or running workers that this worker started."""
    if not worker_id:
        return []
    active = []
    for fp in glob.glob(os.path.join(_s.WORKERS_DIR, "worker_*.json")):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                child = json.load(f)
        except (ValueError, OSError):
            continue
        if (isinstance(child, dict) and worker_id in (child.get("launched_by"), child.get("parent_id")) and child.get("id") != worker_id
                and child.get("status") in _s._SUBWORKER_ACTIVE_STATES):
            active.append(str(child.get("id")))
    return sorted(active)


def t_spin_batch(role: str, prompt: str, input_glob: str, output_file: str, model: str = "", project_dir: str = "",
                 track: str = "main", timeout_seconds: int = 0, merge_prompt: str = "") -> str:
    """Queue a big job for a local model: split into chunks that fit, saved one at a time so it can resume."""
    if not role or not prompt or not input_glob or not output_file:
        return "Error: role, prompt, input_glob and output_file are required."
    cwd = os.path.realpath(os.path.expanduser(project_dir)) if project_dir else os.getcwd()
    if not os.path.isdir(cwd):
        return f"Error: project_dir '{project_dir}' does not exist."
    files = sorted(p for p in glob.glob(os.path.join(cwd, input_glob), recursive=True) if os.path.isfile(p))
    if not files:
        return f"Error: no files match '{input_glob}' in {cwd}."
    data = _s._source("local", 5.0).get("data") or {}
    if not data.get("running"):
        return "Error: Ollama isn't running."
    chosen = model or _s.choose_local_model(need_tools=False, data=data)
    if not chosen:
        return "[MEMORY GUARD] No installed local model fits in the memory that's free right now."
    ok, msg = _s.local_fit(chosen, data)
    if not ok:
        return f"[MEMORY GUARD] {msg}"
    out_path = output_file if os.path.isabs(output_file) else os.path.join(cwd, output_file)
    worker_id = f"worker_{uuid.uuid4().hex[:6]}"
    meta = {
        "id": worker_id, "kind": "batch", "role": role, "provider": "local", "model": chosen, "effort": "batch", "mode": "standard",
        "permission_mode": "ask", "capabilities": [], "skip_permissions": False, "budget": {"preflight": msg}, "track": track,
        "project_dir": cwd, "thread": "", "launched_by": _s._self_id(), "route": "Batch run on a local model, one chunk at a time.",
        "usage": {"input": 0, "output": 0, "cache_read": 0}, "status": "pending_approval", "cmd": ["batch"],
        "timeout_seconds": int(timeout_seconds or 0), "pid": None, "exit_code": None,
        "created_at": _dt.datetime.now().astimezone().isoformat(), "completed_at": None, "prompt": prompt, "log_file": _s._log_file(worker_id),
        "report": "Awaiting user approval before launching.", "files": files, "output_file": out_path,
        "progress": {"files_done": 0, "files_total": len(files), "chunks_done": 0, "failed": 0},
        "merge_prompt": merge_prompt,
        "secret": secrets.token_hex(16),
    }
    launched_by_str = str(meta.get("launched_by") or "")
    meta["thread_root"] = _s._thread_root_for_caller(launched_by_str) or meta["id"]
    _s._save_worker(meta)
    _s._thread_record(str(meta["thread_root"]), "spawned", launched_by_str or "hud", worker_id, {
        "settings": _s._settings_snapshot(meta), "prompt_head": str(prompt or "")[:200],
        "approved_by": meta.get("approved_by", "")})
    _s._broadcast_track_internal(f"[PENDING APPROVAL] Batch `{worker_id}` queued ({role}, {len(files)} files, model {chosen}).", track=track, station_id="guard")
    return (f"[APPROVAL REQUIRED] Batch `{worker_id}` is queued in PENDING APPROVAL state.\n"
            f"- Files: {len(files)}\n- Model: **{chosen}** ({msg})\n- Results: `{out_path}`\n\n"
            f"Only your user can launch this. It resumes where it stopped if it's interrupted. They can approve it in the Live HUD, "
            f"run `subway approve {worker_id}`, or give you the approval code.")


def _chunks(text: str, size: int) -> List[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def t_approve_worker(worker_id: str, approval_code: str = "") -> str:
    """Launch a pending worker, given the code only the user can see."""
    if not _s.WORKER_ID_RE.match(worker_id or ""):
        return f"Error: worker `{worker_id}` not found."
    if not hmac.compare_digest((approval_code or "").strip().upper(), vars(_s)["approval_code"](worker_id)):
        return (f"[APPROVAL REFUSED] Worker `{worker_id}` needs the approval code shown to your user in the "
                f"Live HUD or by `subway workers`. Ask them for it, or ask them to approve it there.")
    _s._thread_record(_s._thread_root(_s._load_worker(worker_id) or {"id": worker_id}), "approved", worker_id,
                   data={"by": "approval code"})
    return _s._launch_worker(worker_id)


def t_stop_worker(worker_id: str, reason: str = "") -> str:
    """Stop a running worker's process."""
    meta = _s._load_worker(worker_id)
    if not meta:
        return f"Error: worker `{worker_id}` not found."
    meta = _s._update_worker_status(meta)
    if meta.get("status") != "running":
        return f"Worker `{worker_id}` is not running (current status: {meta.get('status')})."
    with _s._state_lock("workers"):
        meta["status"] = "stopping"
        meta["stop_reason"] = reason or "Stopped on request"
        _s._save_worker(meta)
    _s._thread_record(meta.get("thread_root") or _s._thread_root(meta), "stopped", worker_id,
                   data={"reason": meta["stop_reason"]})
    with contextlib.suppress(OSError):
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(meta["pid"]), "/T", "/F"],
                           capture_output=True, timeout=5)
        else:
            os.kill(int(meta["pid"]), signal.SIGTERM)  # The supervisor forwards it and records the result.
    for child in _s._all_workers():
        if child.get("parent_id") == worker_id and child.get("status") in ("running", "launching"):
            _s.t_stop_worker(child["id"], reason=f"Parent worker `{worker_id}` stopped.")
    _s._broadcast_track_internal(f"[WORKER STOPPED] Worker `{worker_id}` stop requested. Reason: {meta['stop_reason']}",
                              track=meta.get("track", "main"), station_id="guard")
    return f"[STOPPING] Worker `{worker_id}` was told to stop."


def t_reject_worker(worker_id: str, reason: str = "") -> str:
    """Reject a pending worker. A running worker is stopped instead of just being relabelled."""
    meta = _s._load_worker(worker_id)
    if not meta:
        return f"Error: worker `{worker_id}` not found."
    meta = _s._update_worker_status(meta)
    status = meta.get("status")
    if status == "running":
        return _s.t_stop_worker(worker_id, reason)
    if status != "pending_approval":
        return f"Worker `{worker_id}` already finished (status: {status})."
    with _s._state_lock("workers"):
        meta["status"] = "rejected"
        meta["rejection_reason"] = reason or "Cancelled by user"
        _s._save_worker(meta)
    _s._thread_record(meta.get("thread_root") or _s._thread_root(meta), "rejected", worker_id,
                   data={"reason": meta["rejection_reason"]})
    _s._broadcast_track_internal(f"[WORKER REJECTED] Worker `{worker_id}` rejected. Reason: {meta['rejection_reason']}",
                              track=meta.get("track", "main"), station_id="guard")
    import sse_stream
    with contextlib.suppress(Exception):
        sse_stream.publish("worker", {"id": worker_id, "status": "rejected", "role": meta.get("role"), "provider": meta.get("provider")})
    return f"[REJECTED] Worker `{worker_id}` was rejected and cancelled."


def t_worker_unarchive(worker_id: str) -> str:
    """Restore an archived worker to the active workers dir."""
    if not _s.WORKER_ID_RE.match(worker_id or ""):
        return f"Error: invalid worker id `{worker_id}`."
    arch = _s._archive_dir()
    arch_json = os.path.join(arch, f"{worker_id}.json")
    if not os.path.exists(arch_json):
        return f"Error: worker `{worker_id}` is not in the archive."
    _s._ensure_subway_dir()
    os.makedirs(_s.WORKERS_DIR, exist_ok=True)
    moved = []
    # Move metadata and exit marker.
    for ext in (".json", ".exit"):
        src = os.path.join(arch, worker_id + ext)
        if os.path.exists(src):
            dst = os.path.join(_s.WORKERS_DIR, worker_id + ext)
            os.replace(src, dst)
            moved.append(ext.lstrip("."))
    # Restore log: gunzip back if compressed.
    gz_src = os.path.join(arch, worker_id + ".log.gz")
    log_src = os.path.join(arch, worker_id + ".log")
    log_dst = os.path.join(_s.WORKERS_DIR, worker_id + ".log")
    if os.path.exists(gz_src):
        tmp = log_dst + f".tmp.{os.getpid()}"
        try:
            with gzip.open(gz_src, "rb") as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
            os.replace(tmp, log_dst)
            with contextlib.suppress(OSError):
                os.remove(gz_src)
            moved.append("log")
        except OSError:
            with contextlib.suppress(OSError):
                if os.path.exists(tmp):
                    os.remove(tmp)
            return f"Error: failed to restore compressed log for `{worker_id}`."
    elif os.path.exists(log_src):
        os.replace(log_src, log_dst)
        moved.append("log")
    return f"[UNARCHIVED] Worker `{worker_id}` restored to active workers ({', '.join(moved) or 'no files'})."


def _track_only_workers(known: Set[str]) -> Dict[str, str]:
    """Workers the ledger remembers but whose metadata is gone, so nothing silently disappears."""
    found: Dict[str, str] = {}
    for tf in glob.glob(os.path.join(_s.TRACKS_DIR, "*.jsonl")):
        with contextlib.suppress(OSError):
            with open(tf, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    for wid in re.findall(r"worker_[0-9a-f]{6}", line):
                        if wid not in known:
                            found[wid] = os.path.basename(tf)[:-6]
    return found


def _worker_detail(m: Dict[str, Any]) -> str:
    code = "n/a" if m.get("exit_code") is None else m.get("exit_code")
    out = [
        f"# Worker Detail: `{m['id']}`",
        f"- **Role**: {m.get('role')}",
        f"- **Provider**: {m.get('provider', '').upper()}",
        f"- **Mode**: {m.get('mode', 'standard').upper()}",
        f"- **Effort**: {m.get('effort', 'auto')}",
        f"- **Status**: [{m.get('status', 'unknown').upper()}]",
        *([f"- **Start note**: {m.get('start_note')}"] if m.get("start_note") else []),
        *([f"- **Reason**: {m.get('status_reason')}"] if m.get("status_reason") else []),
        f"- **Exit code**: {code}",
        f"- **PID**: {m.get('pid')}",
        f"- **Track**: {m.get('track')}",
        f"- **Directory**: `{m.get('project_dir')}`",
        f"- **Created At**: {m.get('created_at')}",
        f"- **Completed At**: {m.get('completed_at') or 'in progress'}",
        "",
        "## Task Prompt",
        f"> {_clip_prompt_summary(m.get('prompt') or '', max_chars=200)}",
        "",
        "## Output Log Tail",
    ]
    provider = str(m.get("provider") or "").lower()
    if provider == "pi":
        log_file = m.get("log_file")
        tail = ""
        if log_file and os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                # Parse JSONL and extract last few assistant texts / tool names
                lines = content.splitlines()
                snippets = []
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("{"):
                        try:
                            ev = json.loads(line)
                        except Exception:
                            continue
                        if ev.get("type") == "message_end":
                            msg = ev.get("message") or {}
                            if msg.get("role") == "assistant":
                                text_parts = []
                                c = msg.get("content")
                                if isinstance(c, str):
                                    text_parts.append(c)
                                elif isinstance(c, list):
                                    for item in c:
                                        if isinstance(item, dict):
                                            if item.get("type") == "text" and item.get("text"):
                                                text_parts.append(str(item.get("text")))
                                            elif item.get("text"):
                                                text_parts.append(str(item.get("text")))
                                if text_parts:
                                    t = "\n".join([p for p in text_parts if p]).strip()
                                    if t:
                                        snippets.append(t)
                                        if len(snippets) >= 5:
                                            break
                                        continue
                        # Also look for tool calls
                        if ev.get("type") in ("tool_call", "tool_call_end") or ev.get("tool"):
                            tool_name = ev.get("tool") or ev.get("name") or ev.get("toolName")
                            if tool_name:
                                snippets.append(f"[tool] {tool_name}")
                                if len(snippets) >= 5:
                                    break
                                continue
                    else:
                        # non-JSON line, might be useful
                        snippets.append(line)
                        if len(snippets) >= 5:
                            break
                if snippets:
                    tail = "\n\n".join(reversed(snippets))
            except Exception:
                tail = _s._log_tail(log_file, n=15)
        else:
            tail = _s._log_tail(log_file, n=15)
        if tail:
            tail = "\n".join(tail.splitlines()[-15:])
        if tail and len(tail) > 4000:
            tail = tail[-4000:]
        out.append(f"```text\n{tail}\n```" if tail else "*(no output yet)*")
        return "\n".join(out)
    tail = _s._log_tail(m.get("log_file"), n=15)
    if tail:
        tail = "\n".join(tail.splitlines()[-15:])
    if tail and len(tail) > 4000 and provider == "pi":  # extra safety
        tail = tail[-4000:]
    out.append(f"```text\n{tail}\n```" if tail else "*(no output yet)*")
    return "\n".join(out)


def worker_listing(track: str = "", show_codes: bool = False, all: bool = False) -> str:
    """Worker overview as a compact pipe table."""
    records = _s._all_workers(include_archive=all)
    if not all:
        records = [r for r in records if not r.get("archived")]
    all_known_ids = {r["id"] for r in records if "id" in r}
    if track:
        records = [r for r in records if (r.get("track") or "main") == track]
    orphan_logs = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(_s.WORKERS_DIR, "worker_*.log"))
                         if os.path.basename(p)[:-4] not in all_known_ids)
    if track and track != "main":
        orphan_logs = []
    remembered = _s._track_only_workers(all_known_ids | set(orphan_logs))
    if track:
        remembered = {wid: tr for wid, tr in remembered.items() if (tr or "main") == track}
    if not records and not orphan_logs and not remembered:
        return "No workers have been spawned yet."

    records.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    if not all:
        n = int((_s._load_json("config.json") or {}).get("worker_status_rows", 15) or 15)
        selected = []
        selected_ids = set()
        for r in records[:n]:
            selected.append(r)
            selected_ids.add(r.get("id"))
        for r in records[n:]:
            st = str(r.get("status") or "").lower()
            if st in ("running", "pending_approval", "pending", "launching", "stopping"):
                if r.get("id") not in selected_ids:
                    selected.append(r)
                    selected_ids.add(r.get("id"))
        records = selected

    from tables import table, format_id, truncate_text, format_age, format_tokens_k

    cols = ["id", "prov", "model", "state", "tok_k", "age", "role", "note"]
    rows = []
    for r in records:
        tok = r.get("tokens") or (r.get("usage", {}).get("input", 0) + r.get("usage", {}).get("output", 0)) or 0
        wid = format_id(r.get("id", ""))
        prov = str(r.get("provider") or "").lower()
        model = str(r.get("model") or "")
        state = str(r.get("status") or "")
        tok_k = format_tokens_k(tok)
        age = format_age(r.get("created_at"))
        role = truncate_text(r.get("role") or "", 40)
        raw_note = r.get("start_note") or r.get("reason") or r.get("report") or r.get("error") or ""
        if (not raw_note or raw_note == "supervisor disappeared before reporting") and r.get("log_file") and os.path.exists(r.get("log_file")):
            with contextlib.suppress(OSError):
                with open(r.get("log_file"), "r", encoding="utf-8", errors="replace") as _lf:
                    _content = _lf.read().strip()
                    if _content:
                        raw_note = _content.splitlines()[-1]
        note = "" if state in ("running", "pending_approval") else truncate_text(raw_note, 100)
        rows.append([wid, prov, model, state, tok_k, age, role, note])
    for wid in orphan_logs:
        rows.append([format_id(wid), "", "", "log_only", "", "", "Log only, metadata deleted", ""])
    for wid, tr in sorted(remembered.items()):
        rows.append([format_id(wid), "", "", "track_only", "", "", truncate_text(f"Track {tr}", 40), ""])
    return table(f"workers {len(rows)}", cols, rows)


def estimate_workers(prompt: str) -> Dict[str, Any]:
    """How many workers a task can really use, from its breadth. One narrow task never needs a crowd."""
    text = (prompt or "").lower()
    named = len(re.findall(r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s+\S", prompt or ""))  # Listed items.
    commas = text.count(",") + len(re.findall(r"\band\b", text))
    breadth = sum(1 for w in _s.BREADTH_WORDS if re.search(rf"\b{w}\b", text))
    deep = _s._has_any(text, _s.DEPTH_WORDS)
    count = 1
    if named >= 2:
        count = named
    elif deep or breadth:
        count = 1 + breadth + (1 if deep else 0) + min(commas, 8) // 2
    count = max(count, min(15, _s._scale_of(text) // 1000 + 1))
    limit = _s.capacity()["machine"]
    final = max(1, min(count, 15, limit))
    reason = f"{'Broad' if final > 1 else 'Narrow'} task: suggests {min(count, 15)} worker(s)."
    if final < min(count, 15):
        reason += f" This machine's limit is {limit} right now."
    return {"count": final, "reason": reason}


_ARCHIVE_DAY_CHOICES = {"off", 3, 7, 30, 60, 90, "3", "7", "30", "60", "90"}


def _archive_days(key: str) -> Optional[int]:
    """Return retention days for an archive config key, or None when off/invalid."""
    try:
        cfg = _s._load_json("config.json")
    except Exception:
        return None
    if not isinstance(cfg, dict):
        return None
    val = cfg.get(key, "off")
    if val in ("off", None, "", False):
        return None
    if val in _ARCHIVE_DAY_CHOICES:
        try:
            days = int(val)
            return days if days in (3, 7, 30, 60, 90) else None
        except (TypeError, ValueError):
            return None
    return None


_ARCHIVE_SETTING_KEYS = (("archive_compress_days", "compress"), ("archive_delete_days", "delete"))


def t_archive_settings(compress_days=None, delete_days=None, branch_days=None, action: str = "") -> str:
    """Read or set archive retention, or run maintenance now.

    These config keys were read by archive_maintain() but nothing could write them: no
    tool, no CLI, no endpoint, no UI. The retention feature existed and was unreachable.
    """
    cfg = _s._load_json("config.json")
    if not isinstance(cfg, dict):
        cfg = {}
    current = {
        "compress_days": cfg.get("archive_compress_days", "off"),
        "delete_days": cfg.get("archive_delete_days", "off"),
        "branch_days": cfg.get("archive_branch_days", "off"),
    }
    if str(action or "").strip().lower() in ("maintain", "run", "now"):
        stats = archive_maintain()
        return (f"[ARCHIVE] Maintenance ran: compressed {stats.get('compressed', 0)}, "
                f"deleted {stats.get('deleted', 0)}.")
    supplied = compress_days is not None or delete_days is not None or branch_days is not None
    if not supplied:
        return (f"[ARCHIVE] Compress archived workers after {current['compress_days']} days; "
                f"delete after {current['delete_days']} days; "
                f"delete finished worker branches after {current['branch_days']} days. "
                f"Set with compress_days/delete_days/branch_days: one of off, 3, 7, 30, 60, 90. "
                f"action='maintain' runs it now.")

    def norm(value, label):
        if value is None:
            return None, ""
        text = str(value).strip().lower()
        if text in ("off", "none", "0", "false", ""):
            return "off", ""
        if text.isdigit() and int(text) in (3, 7, 30, 60, 90):
            return int(text), ""
        return None, f"Error: {label} must be off, 3, 7, 30, 60 or 90 (got '{value}')."

    new_compress, err = norm(compress_days, "compress_days")
    if err:
        return err
    new_delete, err = norm(delete_days, "delete_days")
    if err:
        return err
    new_branch, err = norm(branch_days, "branch_days")
    if err:
        return err
    if new_compress is not None:
        cfg["archive_compress_days"] = new_compress
    if new_delete is not None:
        cfg["archive_delete_days"] = new_delete
    if new_branch is not None:
        cfg["archive_branch_days"] = new_branch
    _s._save_json("config.json", cfg)
    return (f"[ARCHIVE] Compress archived workers after {cfg.get('archive_compress_days', 'off')} days; "
            f"delete after {cfg.get('archive_delete_days', 'off')} days; "
            f"delete finished worker branches after {cfg.get('archive_branch_days', 'off')} days.")


WAKE_POLICIES = ("all", "outcomes", "failures", "off")


def t_wake_settings(chat_id: str = "", policy: str = "") -> str:
    """Read or set wake-up event policies (all, outcomes, failures, off)."""
    cfg = _s._load_json("config.json")
    if not isinstance(cfg, dict):
        cfg = {}
    default_policy = cfg.get("wake_events", "all")
    if default_policy not in WAKE_POLICIES:
        default_policy = "all"

    cid = str(chat_id or "").strip()
    pol = str(policy or "").strip().lower()

    if pol and pol not in WAKE_POLICIES:
        return f"Error: policy must be one of {', '.join(WAKE_POLICIES)} (got '{policy}')."

    if cid:
        ts = _s._load_json("thread_settings.json")
        if not isinstance(ts, dict):
            ts = {}
        if pol:
            entry = ts.get(cid)
            entry = dict(entry) if isinstance(entry, dict) else {}
            entry["wake_events"] = pol
            ts[cid] = entry
            _s._save_json("thread_settings.json", ts)
            chat_policy = pol
        else:
            entry = ts.get(cid)
            val = entry.get("wake_events") if isinstance(entry, dict) else None
            chat_policy = str(val).strip().lower() if val in WAKE_POLICIES else default_policy
        return f"[WAKE] Default wake-ups: {default_policy}; {cid}: {chat_policy}."

    if pol:
        cfg["wake_events"] = pol
        _s._save_json("config.json", cfg)
        return f"[WAKE] Default wake-ups: {pol}."

    return f"[WAKE] Default wake-ups: {default_policy}."


def archive_maintain() -> Dict[str, int]:
    """Compress/delete old archived workers per config. Never touches active workers."""
    compress_days = _archive_days("archive_compress_days")
    delete_days = _archive_days("archive_delete_days")
    branch_days = _archive_days("archive_branch_days")
    stats = {"compressed": 0, "deleted": 0}
    if compress_days is None and delete_days is None and branch_days is None:
        return stats
    arch = _s._archive_dir()
    if branch_days is not None:
        repos = set()
        all_fps = []
        if os.path.isdir(arch):
            all_fps.extend(glob.glob(os.path.join(arch, "worker_*.json")))
        if hasattr(_s, "WORKERS_DIR") and os.path.isdir(_s.WORKERS_DIR):
            all_fps.extend(glob.glob(os.path.join(_s.WORKERS_DIR, "worker_*.json")))
        for fp in all_fps:
            meta = _s._load_json(fp)
            if isinstance(meta, dict) and meta.get("repo"):
                repo_path = str(meta["repo"])
                if os.path.isdir(repo_path):
                    repos.add(repo_path)
        branches_deleted = 0
        for repo in sorted(repos):
            res = prune_worker_branches(repo, days=branch_days, dry_run=False)
            if isinstance(res, dict) and "deleted" in res:
                branches_deleted += len(res["deleted"])
        stats["branches_deleted"] = branches_deleted
    if not os.path.isdir(arch):
        return stats
    now = time.time()
    for fp in glob.glob(os.path.join(arch, "worker_*.json")):
        worker_id = os.path.basename(fp)[:-5]
        if not _s.WORKER_ID_RE.match(worker_id):
            continue
        try:
            age = now - os.path.getmtime(fp)
        except OSError:
            continue
        # Deletion takes precedence: if past delete threshold, remove all artifacts.
        if delete_days is not None and age >= delete_days * 86400:
            for ext in (".json", ".exit", ".log", ".log.gz"):
                path = os.path.join(arch, worker_id + ext)
                with contextlib.suppress(OSError):
                    if os.path.exists(path):
                        os.remove(path)
            stats["deleted"] += 1
            continue
        if compress_days is not None and age >= compress_days * 86400:
            log_path = os.path.join(arch, worker_id + ".log")
            gz_path = os.path.join(arch, worker_id + ".log.gz")
            if os.path.exists(log_path) and not os.path.exists(gz_path):
                tmp = gz_path + f".tmp.{os.getpid()}"
                try:
                    with open(log_path, "rb") as src, gzip.open(tmp, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    os.replace(tmp, gz_path)
                    with contextlib.suppress(OSError):
                        os.remove(log_path)
                    stats["compressed"] += 1
                except OSError:
                    with contextlib.suppress(OSError):
                        if os.path.exists(tmp):
                            os.remove(tmp)
            elif os.path.exists(log_path) and os.path.exists(gz_path):
                # Idempotent cleanup: compressed form exists, drop plain log.
                with contextlib.suppress(OSError):
                    os.remove(log_path)
    return stats


def archive_history() -> str:
    """Move finished workers aside. Nothing is deleted, so worker_status can still find them."""
    archive_maintain()
    os.makedirs(_s._archive_dir(), exist_ok=True)
    moved = 0
    for w in _s._all_workers():
        if w.get("status") in _s.ACTIVE_WORKER_STATES:
            continue
        for src in (_s._worker_file(w["id"]), _s._exit_file(w["id"]), _s._log_file(w["id"])):
            if os.path.exists(src):
                os.replace(src, os.path.join(_s._archive_dir(), os.path.basename(src)))
        moved += 1
    return f"Archived {moved} past worker sessions."


def t_worker_archive(worker_id: str) -> str:
    """Move a single finished worker's files from active workers to the archive."""
    if not _s.WORKER_ID_RE.match(worker_id or ""):
        return f"Error: invalid worker id `{worker_id}`."
    meta = _s._load_worker(worker_id)
    if meta is None:
        arch = _s._archive_dir()
        if os.path.exists(os.path.join(arch, f"{worker_id}.json")):
            return f"[ARCHIVED] Worker `{worker_id}` is already in the archive."
        return f"Error: worker `{worker_id}` not found."
    if (meta.get("status") or "") in _s.ACTIVE_WORKER_STATES:
        return f"Refused: worker `{worker_id}` is active ({meta.get('status')}); stop it before archiving."
    os.makedirs(_s._archive_dir(), exist_ok=True)
    moved = []
    for ext in (".json", ".exit", ".log", ".log.gz"):
        src = os.path.join(_s.WORKERS_DIR, worker_id + ext)
        if os.path.exists(src):
            dst = os.path.join(_s._archive_dir(), worker_id + ext)
            os.replace(src, dst)
            moved.append(ext.lstrip("."))
    if not moved:
        # Idempotent: metadata existed but files may already be gone; check archive.
        if os.path.exists(os.path.join(_s._archive_dir(), f"{worker_id}.json")):
            return f"[ARCHIVED] Worker `{worker_id}` is already in the archive."
        return f"Error: no files found to archive for worker `{worker_id}`."
    return f"[ARCHIVED] Worker `{worker_id}` moved to archive ({', '.join(moved)})."


def pending_worker_pings(chat: str, consume: bool = True) -> List[str]:
    """Workers launched by chat that finished, failed, or stopped and haven't been reported yet.

    consume=False peeks without marking them sent. The background notifier peeks
    (it can only push notifications/message, which many clients ignore); the MCP
    tool-result path consumes, because that text is guaranteed to reach the model.
    Without this split the notifier silently ate every ping and no chat ever saw one.
    """
    lines: List[str] = []
    if not chat:
        return lines
    with _s._state_lock("worker_pings"):
        sent = _s._load_json("worker_pings.json")
        updated = False
        for w in _s._all_workers(include_archive=True):
            if w.get("launched_by") != chat:
                continue
            if w.get("status") in _s.ACTIVE_WORKER_STATES:
                continue
            wid = w.get("id")
            if not wid or wid in sent:
                continue
            status = w.get("status") or "completed"
            role = w.get("role") or "worker"
            reason = w.get("reason") or w.get("report") or ""
            if len(str(reason)) > 160 or "\n" in str(reason):
                rdir = os.path.join(_s.SUBWAY_DIR, "reports"); os.makedirs(rdir, exist_ok=True)
                with contextlib.suppress(OSError):
                    with open(os.path.join(rdir, f"{wid}.md"), "w", encoding="utf-8") as fh:
                        fh.write(str(reason))
                reason = f"{_s.one_line(str(reason), 160)} (full: ~/{os.path.basename(_s.SUBWAY_DIR)}/reports/{wid}.md)"
            reason_str = f": {reason}" if reason else ""
            prefix = "[URGENT] " if w.get("priority") == "urgent" else ""
            lines.append(f"{prefix}[WORKER] {wid} ({role}) {status}{reason_str}")
            if consume:
                sent[wid] = time.time()
                updated = True
        if updated:
            _s._save_json("worker_pings.json", sent)
    return lines


def _worker_ping_loop() -> None:
    """Notify clients immediately when a launched worker finishes, fails, or stops."""
    while True:
        try:
            cid = _s._self_id()
            if cid:
                for line in _s.pending_worker_pings(cid, consume=False):
                    _s.send({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": line}})
        except Exception:
            pass
        time.sleep(1.0)
