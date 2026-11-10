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


def _parse_trigger_steps(raw: Any, label: str = "then") -> List[Dict[str, Any]]:
    """Parse and validate worker follow-up steps. Bad input is refused before anything is queued."""
    if raw in (None, "", []):
        return []
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            raise ValueError(f"{label} must be valid JSON.")
    else:
        data = raw
    if not isinstance(data, list):
        raise ValueError(f"{label} must be a JSON list of steps.")
    out = []
    for i, step in enumerate(data):
        if not isinstance(step, dict):
            raise ValueError(f"{label}[{i}] must be an object.")
        unknown = sorted(set(step) - _s._TRIGGER_STEP_FIELDS)
        if unknown:
            raise ValueError(f"unknown field(s) in {label}[{i}]: {', '.join(unknown)}.")
        role = str(step.get("role") or "").strip()
        prompt = str(step.get("prompt") or "").strip()
        if not role or not prompt:
            raise ValueError(f"{label}[{i}] needs both role and prompt.")
        when = str(step.get("when") or "completed").lower()
        if when not in _s._TRIGGER_WHEN:
            raise ValueError(f"{label}[{i}] has unknown when '{when}'. Use {', '.join(_s._TRIGGER_WHEN)}.")
        include = str(step.get("include") or "patch").lower()
        if include not in _s._TRIGGER_INCLUDE:
            raise ValueError(f"{label}[{i}] has unknown include '{include}'. Use {', '.join(_s._TRIGGER_INCLUDE)}.")
        other_provider = step.get("other_provider", False)
        verdict = step.get("verdict", False)
        if not isinstance(other_provider, bool) or not isinstance(verdict, bool):
            raise ValueError(f"{label}[{i}] other_provider and verdict must be true or false.")
        max_tokens = step.get("max_tokens")
        if max_tokens is not None:
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 0:
                raise ValueError(f"{label}[{i}] max_tokens must be a non-negative integer.")
        step_prov = str(step.get("provider") or "auto").lower()
        step_prov = {"claude-code": "claude", "opencode2": "opencode", "agy": "antigravity", "ollama": "local", "github-copilot": "copilot"}.get(step_prov, step_prov)
        step_model = str(step.get("model") or "")
        if step_prov == "copilot" and step_model and step_model != "auto" and _s.is_copilot_cli_refused(step_model):
            step_model = "auto"
        if step_prov != "auto" and step_model:
            ok, valid_m, err = _s._validate_model_for_provider(step_prov, step_model)
            if not ok:
                raise ValueError(err)
            step_model = valid_m
        if "capabilities" in step:
            caps = step["capabilities"]
            if not isinstance(caps, list) or any(not isinstance(c, str) for c in caps):
                raise ValueError(f"{label}[{i}] capabilities must be a list of strings.")
            for c in caps:
                if c not in _s.VALID_CAPABILITIES:
                    raise ValueError(f"{label}[{i}] has unknown capability '{c}'. Use {', '.join(sorted(_s.VALID_CAPABILITIES))}.")
        if "effort" in step:
            effort_raw = step["effort"]
            if not isinstance(effort_raw, str):
                raise ValueError(f"{label}[{i}] effort must be a string.")
            effort_val = effort_raw.lower()
            if effort_val not in _s._TRIGGER_EFFORTS:
                raise ValueError(f"{label}[{i}] has unknown effort '{effort_val}'. Use {', '.join(_s._TRIGGER_EFFORTS)}.")
        step_dict = {
            "role": role,
            "prompt": prompt,
            "provider": step_prov,
            "model": step_model,
            "when": when,
            "include": include,
            "other_provider": other_provider,
            "max_tokens": int(max_tokens or 0),
            "verdict": verdict,
            "then": _s._parse_trigger_steps(step.get("then"), f"{label}[{i}].then"),
        }
        if "capabilities" in step:
            step_dict["capabilities"] = list(step["capabilities"])
        if "effort" in step:
            step_dict["effort"] = step["effort"].lower()
        out.append(step_dict)
    return out


def _review_trigger_step(review: str) -> Optional[Dict[str, Any]]:
    review = (review or "").strip()
    if not review:
        return None
    provider, model = "auto", ""
    if review.lower() != "auto":
        if "/" not in review:
            raise ValueError("review must be '', 'auto', or 'provider/model'.")
        provider, model = (part.strip() for part in review.split("/", 1))
        if not provider or not model:
            raise ValueError("review must be '', 'auto', or 'provider/model'.")
    raw = [{
        "role": "Reviewer", "prompt": _s._REVIEW_PROMPT, "provider": provider.lower(), "model": model,
        "when": "completed", "include": "patch", "other_provider": True, "max_tokens": 0,
        "verdict": True, "then": [], "effort": "low",
    }]
    return _s._parse_trigger_steps(raw, label="review")[0]


def _resolve_review_step(step: Dict[str, Any], meta: Dict[str, Any], child_prompt: str) -> Tuple[str, str]:
    """Dynamically select a capable reviewer respecting remaining allowance, shed_at_pct, and other_provider."""
    provider = str(step.get("provider") or "auto").lower()
    model = str(step.get("model") or "")
    if provider == "copilot" and model and model != "auto" and _s.is_copilot_cli_refused(model):
        model = "auto"
    if provider == "auto":
        role = str(step.get("role") or "Reviewer")
        try:
            route = _s.route_task(child_prompt, role=role)
        except Exception:
            route = {}
        picks = list(route.get("picks") or [])
        if step.get("other_provider") and meta.get("provider"):
            diff_prov = [pick for pick in picks if pick.get("provider") != meta.get("provider")]
            picks = diff_prov or picks
        if picks:
            provider = picks[0].get("provider") or "auto"
            if not model:
                model = picks[0].get("model") or ""
    return provider, model


def _run_triggers(worker_id: str, result: Dict[str, Any]) -> None:
    """Start a worker's follow-up steps from its own record, so no orchestrator has to be watching."""
    meta = _s._load_worker(worker_id)
    if not meta:
        return
    status = str(result.get("status") or meta.get("status") or "")
    response = str(result.get("response") or meta.get("response") or meta.get("report") or "")
    verdict, verdict_reason = "none", ""
    is_reviewer = meta.get("verdict") is True or bool(meta.get("is_reviewer")) or "review" in str(meta.get("role", "")).lower()
    if is_reviewer:
        first = next((line.strip() for line in response.splitlines() if line.strip()), "")
        match = re.match(r"^(APPROVE|REJECT)\b[:\s\-]*(.*)$", first, re.IGNORECASE)
        if match:
            verdict = "approve" if match.group(1).upper() == "APPROVE" else "reject"
            verdict_reason = match.group(2).strip()
    reviewer_model = meta.get("model") or meta.get("provider") or "reviewer"
    with _s._state_lock("workers"):
        current = _s._load_worker(worker_id) or meta
        steps = list(current.get("then") or [])
        current["then"] = []
        if is_reviewer:
            current["verdict"] = verdict
            current["verdict_reason"] = verdict_reason
            current["verdict_model"] = reviewer_model
        _s._save_worker(current)
    meta = current
    parent_id = meta.get("triggered_by") or ""
    if is_reviewer and parent_id:
        with _s._state_lock("workers"):
            parent = _s._load_worker(parent_id)
            if parent:
                parent["verdict"] = verdict
                parent["verdict_reason"] = verdict_reason
                parent["verdict_model"] = reviewer_model
                _s._save_worker(parent)
    if is_reviewer and verdict != "none":
        word = "APPROVE" if verdict == "approve" else "REJECT"
        _s._broadcast_track_internal(f"Review of {worker_id} by {reviewer_model}: {word}: {verdict_reason}".rstrip(),
                                  track=meta.get("track", "main"), station_id=worker_id)
    for step in steps:
        when = step.get("when") or "completed"
        if when == "always":
            pass
        elif when == "completed":
            if status != "completed":
                continue
        elif when == "failed":
            if status not in ("failed", "budget_stopped", "timed_out"):
                continue
        elif when == "approved":
            if verdict != "approve":
                continue
        elif when == "rejected":
            if verdict != "reject":
                continue
        else:
            continue
        want = step.get("include") or "patch"
        patch_text = ""
        if want in ("patch", "both"):
            patch_file = (meta.get("patch_path") or meta.get("review_diff_path")
                          or os.path.join(_s.WORKERS_DIR, f"{worker_id}.patch"))
            with contextlib.suppress(OSError):
                with open(patch_file, "r", encoding="utf-8", errors="replace") as f:
                    patch_text = f.read(_s._REVIEW_DIFF_LIMIT)
        real_resp = result.get("response") or (meta.get("response") if "Awaiting" not in str(meta.get("response", "")) else "")
        # The agent's own report first; the supervisor's note on how it ended is added, never substituted.
        report_text = str(real_resp or meta.get("report") or "").strip()
        end_note = str(result.get("reason") or "").strip()
        if end_note and end_note not in report_text:
            report_text = f"{report_text}\n\nSupervisor note: {end_note}".strip()
        if not patch_text and meta.get("review_diff_note"):
            patch_text = str(meta["review_diff_note"])
        if want in ("patch", "both") and not patch_text:
            patch_text = report_text[:40000]
        pieces = []
        if want in ("patch", "both") and patch_text:
            pieces.append(patch_text)
        if want in ("report", "both") and report_text:
            pieces.append(report_text[:40000])
        included = "\n\n".join(piece for piece in pieces if piece)
        child_prompt = str(step.get("prompt") or "").rstrip()
        if step.get("verdict"):  # Reviewers judge the patch against the task, whatever provider runs them.
            task_text = _s._reviewer_task_text(meta.get("prompt") or "")
            if task_text:
                child_prompt += f"\n\nTASK:\n{task_text}"
        child_prompt += f"\n\nPREVIOUS WORKER {worker_id} ({meta.get('role') or 'worker'}, {status}):\n{included}"
        provider, model = _s._resolve_review_step(step, meta, child_prompt)
        step_max_tokens = int(step.get("max_tokens") or 0)
        if not step_max_tokens:
            step_max_tokens = 160000 if provider == "claude" else 80000
        try:
            with _s._thread_parent(worker_id, "followed", {"role": step.get("role"), "verdict": bool(step.get("verdict"))}):
                spin_kwargs = dict(
                    role=str(step["role"]),
                    prompt=child_prompt,
                    provider=provider,
                    model=model,
                    track=meta.get("track") or "main",
                    project_dir=meta.get("project_dir") or "",
                    max_tokens=step_max_tokens,
                    isolate=True,
                    then=json.dumps(step.get("then") or []),
                )
                if step.get("capabilities") is not None:
                    spin_kwargs["capabilities"] = step["capabilities"]
                if step.get("effort") is not None:
                    spin_kwargs["effort"] = step["effort"]
                res = _s.t_spin_worker(**spin_kwargs)
        except Exception:
            continue
        if res.startswith("Error:"):
            continue
        child_match = re.search(r"worker_[0-9a-f]{6}", res)
        if not child_match:
            continue
        child_id = child_match.group(0)
        child = _s._load_worker(child_id)
        if child:
            child["triggered_by"] = worker_id
            child["verdict"] = bool(step.get("verdict"))
            child["is_reviewer"] = bool(step.get("verdict"))
            _s._save_worker(child)
        with _s._state_lock("workers"):
            parent = _s._load_worker(worker_id) or meta
            parent.setdefault("triggered", []).append(child_id)
            _s._save_worker(parent)
        _s._broadcast_track_internal(f"{worker_id} finished; started {child_id} ({step.get('role')})",
                                  track=meta.get("track", "main"), station_id=worker_id)
        with contextlib.suppress(Exception):
            _s._launch_worker(child_id)


def t_on_worker_done(worker_id: str, then: str = "") -> str:
    """Attach follow-up steps to a worker already running, or fire them now if it has finished."""
    meta = _s._load_worker(worker_id)
    if not meta:
        return f"Error: worker `{worker_id}` not found."
    try:
        steps = _s._parse_trigger_steps(then)
    except ValueError as exc:
        return f"Error: {exc}"
    if not steps:
        return "Error: then must contain at least one step."
    active = ("pending_approval", "approved", "launching", "running", "stopping", "queued")
    if meta.get("status") in active:
        with _s._state_lock("workers"):
            current = _s._load_worker(worker_id) or meta
            current["then"] = list(current.get("then") or []) + steps
            _s._save_worker(current)
        return f"[TRIGGERS ADDED] Worker `{worker_id}` will start {len(steps)} step(s) when it finishes."
    with _s._state_lock("workers"):
        current = _s._load_worker(worker_id) or meta
        current["then"] = list(current.get("then") or []) + steps
        _s._save_worker(current)
    result: Dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        with open(_s._exit_file(worker_id), "r", encoding="utf-8") as f:
            result = json.load(f)
    if not result:
        result = {"status": meta.get("status"), "exit_code": meta.get("exit_code"),
                  "reason": meta.get("report") or "", "response": meta.get("report") or ""}
    _s._run_triggers(worker_id, result)
    return f"[TRIGGERS FIRED] Worker `{worker_id}` already finished; started its follow-up steps."



def _originating_chat_for_worker(meta: Dict[str, Any]) -> str:
    chat = str((meta or {}).get("launched_by") or (meta or {}).get("thread") or (meta or {}).get("thread_root") or "").strip()
    if (not chat or chat == "hud" or _s.WORKER_ID_RE.match(chat)) and (meta or {}).get("iteration_id"):
        with contextlib.suppress(Exception):
            import iteration
            st = iteration._load_state((meta or {}).get("iteration_id"))
            if st:
                chat = iteration._originating_chat_for_iteration(st)
    return chat


def _wake_worker_milestone(worker_id: str, meta: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Milestone wake for originating chat after completion/follow-up completion."""
    iter_id = (meta or {}).get("iteration_id") or (_s._load_worker(worker_id) or {}).get("iteration_id")
    phase = (meta or {}).get("iteration_phase") or (_s._load_worker(worker_id) or {}).get("iteration_phase")
    if iter_id and phase != "overseer":
        return
    chat_id = _originating_chat_for_worker(meta)
    if not chat_id or chat_id == "hud" or _s.WORKER_ID_RE.match(chat_id):
        return
    status = str((result or {}).get("status") or (meta or {}).get("status") or "unknown")
    reason = str((result or {}).get("reason") or "").strip()
    summary = f"[SUBWAY MILESTONE] worker {worker_id} {status}" + (f": {reason[:160]}" if reason else "")
    try:
        import wake_driver
        wake_driver.wake_chat(chat_id, wake_driver.WakeEvent(kind="worker_milestone", worker_id=worker_id,
                                                              chat_id=chat_id, text=summary,
                                                              data={"status": status, "reason": reason[:500]}),
                              project_dir=str((meta or {}).get("project_dir") or ""))
    except Exception as e:
        print(f"[wake] worker_milestone for {chat_id} failed: {e!r}", file=sys.stderr)
    with contextlib.suppress(Exception):
        _s._broadcast_track_internal(summary, track=str((meta or {}).get("track") or "main"), station_id=worker_id)
    with contextlib.suppress(Exception):
        from notify_driver import dispatch_notification
        dispatch_notification("Subway: Worker Milestone", summary, kind="worker_milestone",
                              data={"worker_id": worker_id, "chat_id": chat_id, "status": status, "track": (meta or {}).get("track", "main")})
