"""Iteration mode: the same task given to 2-4 participants who work, then review one
another's work round over round until a goal threshold is met (roadmap item 20).

Not a polling lead: driven entirely by on_participant_done, called from the worker
supervisor's own completion path (worker_supervision/supervise.py) when a finished
worker's meta carries an iteration_id. See docs/design/20-delegation-shapes.md:85-130
for the persisted state shape this module extends.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

sys.modules.setdefault("server", sys.modules[__name__])
import server as _s

_ITERATION_BLOCK_RE = re.compile(r"```iteration\s*\n(.*?)\n```", re.DOTALL)
_WORKER_ID_RE = re.compile(r"worker_[0-9a-f]{6}")

_TERMINAL_STATUSES = ("completed", "failed", "stopped", "timed_out", "budget_stopped")
_CAP_STATUSES = ("timed_out", "budget_stopped")
_STOP_STATUSES = ("converged", "stalled", "max_rounds", "insufficient_participants", "budget_exceeded", "stopped")


def _iterations_dir() -> str:
    return os.path.join(_s.SUBWAY_DIR, "iterations")


def _state_path(iteration_id: str) -> str:
    return os.path.join(_iterations_dir(), f"{iteration_id}.json")


def _blackboard_path(iteration_id: str) -> str:
    return os.path.join(_iterations_dir(), f"{iteration_id}.blackboard.md")


def _append_blackboard(iteration_id: str, text: str) -> None:
    if not iteration_id:
        return
    os.makedirs(_iterations_dir(), exist_ok=True)
    with open(_blackboard_path(iteration_id), "a", encoding="utf-8") as f:
        f.write(text)


_IMAGE_EXT_RE = re.compile(r'(?:[^\s()<>"\'`]+?\.(?:png|jpg|jpeg|webp|gif))', re.IGNORECASE)


def _extract_screenshots(text: str) -> List[str]:
    return list(dict.fromkeys(_IMAGE_EXT_RE.findall(text or "")))


def _originating_chat_for_iteration(state: Dict[str, Any]) -> str:
    chat_id = str(state.get("origin_chat") or "").strip()
    if chat_id and chat_id != "hud" and not _s.WORKER_ID_RE.match(chat_id):
        return chat_id
    for pid in state.get("participants", []):
        wid = state.get("participant_records", {}).get(pid, {}).get("worker_ids", {}).get("1_work")
        if wid:
            wmeta = _s._load_worker(wid)
            if wmeta:
                c = _s._originating_chat_for_worker(wmeta) if hasattr(_s, "_originating_chat_for_worker") else ""
                if c and c != "hud" and not _s.WORKER_ID_RE.match(c):
                    return c
    return ""


def _check_agreement(state: Dict[str, Any], leader: str, rnd: str) -> bool:
    active = _active_pids(state)
    critics = [p for p in active if p != leader]
    if not critics:
        return True
    for c in critics:
        c_scores: Dict[str, float] = {}
        for p in active:
            if p == c:
                continue
            rec = state["participant_records"][p]
            score = rec.get("peer_scores_received", {}).get(rnd, {}).get(c)
            if score is not None:
                c_scores[p] = score
        if leader not in c_scores:
            return False
        leader_score = c_scores[leader]
        for other, s in c_scores.items():
            if other != leader and s >= leader_score:
                return False
    return True


def _load_state(iteration_id: str) -> Optional[Dict[str, Any]]:
    path = _state_path(iteration_id)
    with contextlib.suppress(OSError, ValueError):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_state(state: Dict[str, Any]) -> None:
    os.makedirs(_iterations_dir(), exist_ok=True)
    state["updated_at"] = time.time()
    _s._write_json_atomic(_state_path(state["id"]), state)


def _broadcast(state: Dict[str, Any], message: str) -> None:
    with contextlib.suppress(Exception):
        _s._broadcast_track_internal(message, track=state.get("track", "main"), station_id=state["id"])


def _parse_iteration_block(text: str) -> Tuple[Dict[str, Any], bool]:
    """Pull the trailing ```iteration fenced JSON block out of a participant's output.

    Missing or malformed blocks are not errors: callers treat them as a 0 score with a note,
    so one broken participant can't wedge the barrier.
    """
    text = text or ""
    matches = _ITERATION_BLOCK_RE.findall(text)
    if not matches:
        return {}, False
    with contextlib.suppress(ValueError):
        data = json.loads(matches[-1])
        if isinstance(data, dict):
            return data, True
    return {}, False


def _clamp_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def _active_pids(state: Dict[str, Any]) -> List[str]:
    return [pid for pid in state["participants"] if state["participant_records"][pid]["status"] == "active"]


def _clip(text: str, limit: int = 8000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


def _harvest_worktree_diff(worktree_dir: str) -> str:
    if not worktree_dir or not os.path.isdir(worktree_dir):
        return ""
    import subprocess
    try:
        res = subprocess.run(["git", "diff", "HEAD"], cwd=worktree_dir, capture_output=True, text=True, timeout=10)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout[:40000]
        res2 = subprocess.run(["git", "diff"], cwd=worktree_dir, capture_output=True, text=True, timeout=10)
        if res2.returncode == 0 and res2.stdout.strip():
            return res2.stdout[:40000]
    except Exception:
        pass
    return ""


def _diff_text(worker_id: str, meta: Dict[str, Any]) -> str:
    patch_file = meta.get("patch_path") or os.path.join(_s.WORKERS_DIR, f"{worker_id}.patch")
    content = ""
    with contextlib.suppress(OSError):
        if os.path.isfile(patch_file):
            with open(patch_file, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(40000)
    if content.strip():
        return content
    # If patch_file isn't already populated, harvest partial diff from worktree
    worktree = meta.get("worktree") or meta.get("project_dir")
    harvested = _harvest_worktree_diff(worktree)
    if harvested:
        with contextlib.suppress(OSError):
            os.makedirs(os.path.dirname(patch_file), exist_ok=True)
            with open(patch_file, "w", encoding="utf-8") as f:
                f.write(harvested)
        meta["patch_path"] = patch_file
        meta["diff_text"] = harvested
        with contextlib.suppress(Exception):
            _s._save_worker(meta)
        return harvested
    return ""


def _round_label(state: Dict[str, Any]) -> str:
    rnd = state["current_round"]
    if state.get("max_rounds", 0) == 0:
        return f"round {rnd}"
    return f"round {rnd}/{state['max_rounds']}"


def _phase_caps(state: Dict[str, Any], phase: str) -> Tuple[int, int]:
    rs = int(state.get("round_seconds") or 0)
    rt = int(state.get("round_tokens") or 0)
    if phase == "work":
        sec = round(rs * 0.7) if rs else 0
        tok = round(rt * 0.8) if rt else 0
    else:
        sec = round(rs * 0.3) if rs else 0
        tok = round(rt * 0.2) if rt else 0
    return sec, tok


def _budget_announcement(state: Dict[str, Any], phase: str) -> str:
    sec, tok = _phase_caps(state, phase)
    tok_s = str(tok) if tok else "unlimited"
    sec_s = str(sec) if sec else "unlimited"
    return f"This round's budget: {tok_s} tokens and {sec_s} seconds for this phase. Efficiency is scored."


_EFFICIENCY_RUBRIC = "Efficiency counts: judge how much quality each participant produced for the tokens and time it spent. Doing more for less scores higher."


def _usage_for_round(rec: Dict[str, Any], rnd: Any) -> Dict[str, Any]:
    return (rec.get("usage") or {}).get(str(rnd)) or {}


def _record_usage(state: Dict[str, Any], pid: str, meta: Dict[str, Any]) -> None:
    rnd = str(state["current_round"])
    rec = state["participant_records"][pid]
    usage = meta.get("usage") or {}
    try:
        tokens = round(float(usage.get("input") or 0) + float(usage.get("output") or 0))
    except (TypeError, ValueError):
        tokens = 0
    seconds = 0
    started = meta.get("started_at")
    completed = meta.get("completed_at")
    try:
        if started and completed:
            import datetime as _dt
            seconds = round((_dt.datetime.fromisoformat(str(completed)) - _dt.datetime.fromisoformat(str(started))).total_seconds())
    except Exception:
        seconds = 0
    if not seconds:
        try:
            seconds = round(float(meta.get("duration_s") or 0))
        except (TypeError, ValueError):
            seconds = 0
    rec.setdefault("usage", {})[rnd] = {"tokens": tokens, "seconds": seconds}


def _spent_line(state: Dict[str, Any], rec: Dict[str, Any], rnd: Any, phase: str) -> str:
    u = _usage_for_round(rec, rnd)
    sec, tok = _phase_caps(state, phase)
    tok_cap = str(tok) if tok else "unlimited"
    sec_cap = str(sec) if sec else "unlimited"
    return f"Spent: {u.get('tokens', 0)} tokens of cap {tok_cap}, {u.get('seconds', 0)} s of cap {sec_cap}."


def _work_prompt(state: Dict[str, Any], pid: str) -> str:
    rec = state["participant_records"][pid]
    rnd = state["current_round"]
    parts = [
        _budget_announcement(state, "work"),
        f"ITERATION TASK ({_round_label(state)}):\n{state['task']}",
    ]
    rubric_text = (state.get("rubric") or "").strip()
    rubric_parts = [rubric_text] if rubric_text else []
    rubric_parts.append(_EFFICIENCY_RUBRIC)
    parts.append("RUBRIC:\n" + "\n".join(rubric_parts))
    if rnd > 1:
        prev = rnd - 1
        consensus = state.get("last_consensus_block") or state.get("consensus_blocks", {}).get(str(prev), "")
        if consensus:
            parts.append(f"BLACKBOARD (last round's consensus):\n{consensus}")
        critiques = rec.get("critiques_received", {}).get(str(prev), {})
        if critiques:
            crit_lines = "\n".join(f"- {state['participant_records'][c]['role']} ({c}): {t}"
                                    for c, t in critiques.items())
            parts.append(f"CRITIQUES OF YOUR LAST ROUND FROM PEERS:\n{crit_lines}")
        peer_summaries = []
        for other in state["participants"]:
            if other == pid:
                continue
            summary = state["participant_records"][other].get("summary", {}).get(str(prev))
            if summary:
                peer_summaries.append(f"- {other}: {_clip(summary, 1500)}")
        if peer_summaries:
            parts.append("WHAT YOUR PEERS DID LAST ROUND (adopt good ideas if useful):\n" + "\n".join(peer_summaries))
        parts.append(
            "Continue your own work in this same working directory (your own branch from last round). "
            "Revise based on the critiques above; say concretely what you did better than last round."
        )
    else:
        parts.append("This is round 1: do the task from scratch in this working directory.")
    parts.append(
        'End your reply with a fenced block exactly in this form:\n'
        '```iteration\n'
        '{"self_score": 0.0, "rubric": {}, "improved": "what you did better than last round (empty on round 1)", '
        '"summary": "what you did this round"}\n'
        '```'
    )
    return "\n\n".join(parts)


def _inspect_screenshots(target: Any, worktree: str = "") -> List[str]:
    """Check screenshots: under 10 KB (< 10240 bytes) or identical light/dark fail."""
    failures = []
    if not target and worktree and os.path.isdir(worktree):
        found = {}
        for f in os.listdir(worktree):
            low = f.lower()
            if low.endswith((".png", ".jpg", ".jpeg", ".webp")):
                if "light" in low:
                    found["light"] = os.path.join(worktree, f)
                elif "dark" in low:
                    found["dark"] = os.path.join(worktree, f)
                elif "screenshot" in low:
                    found.setdefault("default", os.path.join(worktree, f))
        if found:
            target = found

    if not target:
        return failures

    import base64

    def _get_bytes(val: Any) -> Optional[bytes]:
        if isinstance(val, bytes):
            return val
        if isinstance(val, str):
            path = val
            if not os.path.isabs(path) and worktree:
                path = os.path.join(worktree, path)
            if os.path.isfile(path):
                with contextlib.suppress(OSError):
                    with open(path, "rb") as f:
                        return f.read()
            try:
                decoded = base64.b64decode(val)
                if len(decoded) > 0:
                    return decoded
            except Exception:
                pass
            return val.encode("utf-8", errors="replace")
        return None

    if isinstance(target, dict):
        light_bytes = None
        dark_bytes = None
        for k, v in target.items():
            b = _get_bytes(v)
            if b is not None:
                if len(b) < 10240:
                    failures.append(f"Screenshot '{k}' is under 10 KB ({len(b)} bytes < 10240 bytes).")
                k_low = str(k).lower()
                if "light" in k_low:
                    light_bytes = b
                elif "dark" in k_low:
                    dark_bytes = b
        if light_bytes is not None and dark_bytes is not None:
            if light_bytes == dark_bytes:
                failures.append("Light screenshot is byte-identical (same bytes/SHA-256) to dark screenshot.")
    elif isinstance(target, list):
        items_bytes = []
        for i, item in enumerate(target):
            b = _get_bytes(item)
            if b is not None:
                if len(b) < 10240:
                    failures.append(f"Screenshot {i+1} is under 10 KB ({len(b)} bytes < 10240 bytes).")
                items_bytes.append(b)
        if len(items_bytes) >= 2:
            if items_bytes[0] == items_bytes[1]:
                failures.append("Light screenshot is byte-identical (same bytes/SHA-256) to dark screenshot.")

    return failures


def _inspect_text_artifacts(diff_text: str, report_text: str) -> List[str]:
    failures = []
    nan_re = re.compile(r"\bNaN\b")
    undefined_re = re.compile(r"\bundefined\b")
    if nan_re.search(diff_text):
        failures.append("Diff contains rendered text 'NaN'.")
    elif nan_re.search(report_text):
        failures.append("Worker report contains rendered text 'NaN'.")
    if undefined_re.search(diff_text):
        failures.append("Diff contains rendered text 'undefined'.")
    elif undefined_re.search(report_text):
        failures.append("Worker report contains rendered text 'undefined'.")
    return failures


def _run_objective_checks(state: Dict[str, Any], pid: str, rnd: Any) -> List[str]:
    rec = state["participant_records"][pid]
    rnd_str = str(rnd)
    failures = []
    shots = rec.get("screenshots", {}).get(rnd_str)
    worktree = rec.get("project_dir") or ""
    failures.extend(_inspect_screenshots(shots, worktree))
    diff = rec.get("diff_text", {}).get(rnd_str, "")
    summary = rec.get("summary", {}).get(rnd_str, "")
    failures.extend(_inspect_text_artifacts(diff, summary))
    rec.setdefault("objective_failures", {})[rnd_str] = failures
    return failures


def _has_objective_failure(state: Dict[str, Any], pid: str, rnd: Any) -> bool:
    rec = state["participant_records"].get(pid, {})
    rnd_str = str(rnd)
    if "objective_failures" in rec and rnd_str in rec["objective_failures"]:
        return len(rec["objective_failures"][rnd_str]) > 0
    return len(_run_objective_checks(state, pid, rnd)) > 0


def _review_prompt(state: Dict[str, Any], pid: str) -> str:
    rnd = state["current_round"]
    rec = state["participant_records"][pid]
    own_summary = rec.get("summary", {}).get(str(rnd), "")
    own_diff = rec.get("diff_text", {}).get(str(rnd), "")
    parts = [
        _budget_announcement(state, "review"),
        f"ITERATION REVIEW ({_round_label(state)}) of task:\n{state['task']}",
    ]
    rubric_text = (state.get("rubric") or "").strip()
    rubric_parts = [rubric_text] if rubric_text else []
    rubric_parts.append(_EFFICIENCY_RUBRIC)
    parts.append("RUBRIC:\n" + "\n".join(rubric_parts))
    parts.append(f"YOUR OWN WORK THIS ROUND:\nSummary: {_clip(own_summary, 4000)}\nDiff:\n{_clip(own_diff, 6000)}\n{_spent_line(state, rec, rnd, 'work')}")
    own_failures = rec.get("objective_failures", {}).get(str(rnd)) or _run_objective_checks(state, pid, rnd)
    if own_failures:
        parts.append("OBJECTIVE CHECK FAILURES (Score capped at 0.5):\n" + "\n".join(f"- {f}" for f in own_failures))
    for other in state["participants"]:
        if other == pid:
            continue
        orec = state["participant_records"][other]
        if orec["status"] != "active":
            continue
        o_summary = orec.get("summary", {}).get(str(rnd), "")
        o_diff = orec.get("diff_text", {}).get(str(rnd), "")
        peer_section = [
            f"PEER {other} ({orec.get('role')}) THIS ROUND:\nSummary: {_clip(o_summary, 4000)}\n"
            f"Diff:\n{_clip(o_diff, 6000)}\n{_spent_line(state, orec, rnd, 'work')}"
        ]
        other_failures = orec.get("objective_failures", {}).get(str(rnd)) or _run_objective_checks(state, other, rnd)
        if other_failures:
            peer_section.append(
                f"OBJECTIVE CHECK FAILURES for peer {other} (Score capped at 0.5):\n"
                + "\n".join(f"- {f}" for f in other_failures)
                + "\nNOTE: Candidate failed objective check; score must not exceed 0.5."
            )
        parts.append("\n".join(peer_section))
    parts.append(
        'Critique each peer and score every participant (including yourself) 0-1 against the rubric/task. '
        'End your reply with a fenced block exactly in this form:\n'
        '```iteration\n'
        '{"peer_scores": {"<peer_id>": 0.0}, "critiques": {"<peer_id>": "..."}, "self_score": 0.0}\n'
        '```'
    )
    return "\n\n".join(parts)


def _tag_worker(worker_id: str, iteration_id: str, pid: str, round_n: int, phase: str, seed_patch: str = "") -> None:
    with _s._state_lock("workers"):
        meta = _s._load_worker(worker_id)
        if not meta:
            return
        meta["iteration_id"] = iteration_id
        meta["iteration_participant"] = pid
        meta["iteration_round"] = round_n
        meta["iteration_phase"] = phase
        if seed_patch:
            meta["seed_patch"] = seed_patch
        if phase == "overseer":
            st = _load_state(iteration_id)
            if st:
                orig = _originating_chat_for_iteration(st)
                if orig and (not meta.get("launched_by") or meta.get("launched_by") == "hud" or _s.WORKER_ID_RE.match(str(meta.get("launched_by")))):
                    meta["launched_by"] = orig
        _s._save_worker(meta)


def _spin_phase_worker(state: Dict[str, Any], pid: str, phase: str, bypass_approval: bool = True) -> Optional[str]:
    rec = state["participant_records"][pid]
    prompt = _work_prompt(state, pid) if phase == "work" else _review_prompt(state, pid)
    role = rec.get("role") or ("Participant" if phase == "work" else "Reviewer")
    sec, tok = _phase_caps(state, phase)
    pdir = rec.get("project_dir")
    if not pdir or not os.path.isdir(pdir):
        pdir = state.get("project_dir") or os.getcwd()
    kwargs: Dict[str, Any] = dict(
        role=role, prompt=prompt, provider=rec.get("provider") or "auto", model=rec.get("model") or "",
        effort=rec.get("effort") or "auto", track=state.get("track", "main"),
        project_dir=pdir,
        priority="normal",
    )
    if sec:
        kwargs["timeout_seconds"] = sec
    if tok:
        kwargs["max_tokens"] = tok
    if state.get("budget_per_phase"):
        kwargs["max_budget_usd"] = state["budget_per_phase"]
    if phase == "work":
        kwargs["isolate"] = True
    else:
        kwargs["isolate"] = False
        kwargs["capabilities"] = ["files:read"]
    res = _s.t_spin_worker(**kwargs)
    match = _WORKER_ID_RE.search(res or "")
    if not match:
        return None
    worker_id = match.group(0)
    seed_patch = ""
    if phase == "work" and state.get("current_round", 1) > 1:
        prev_id = rec.get("worker_ids", {}).get(f"{state['current_round'] - 1}_work")
        if prev_id:
            seed_patch = (_s._load_worker(prev_id) or {}).get("patch_path") or os.path.join(_s.WORKERS_DIR, f"{prev_id}.patch")
    _tag_worker(worker_id, state["id"], pid, state["current_round"], phase, seed_patch=seed_patch)
    if bypass_approval:
        with contextlib.suppress(Exception):
            _s._launch_worker(worker_id)
    return worker_id


def _normalize_overseer(overseer: Any) -> Optional[Dict[str, Any]]:
    if not overseer:
        return None
    if isinstance(overseer, str):
        try:
            overseer = json.loads(overseer)
        except ValueError:
            return None
    if not isinstance(overseer, dict):
        return None
    out: Dict[str, Any] = {}
    if overseer.get("provider"):
        out["provider"] = str(overseer["provider"]).lower()
    if overseer.get("model"):
        out["model"] = str(overseer["model"])
    if overseer.get("effort"):
        out["effort"] = str(overseer["effort"]).lower()
    return out or None


def _spin_overseer(state: Dict[str, Any]) -> None:
    if state.get("status") == "stopped":
        return
    old_wid = state.get("overseer_worker")
    if old_wid:
        old_meta = _s._load_worker(old_wid) if hasattr(_s, "_load_worker") else None
        if old_meta and old_meta.get("status") not in _TERMINAL_STATUSES:
            return
    overseer = state.get("overseer")
    if not overseer and (state.get("overseer_model") or state.get("overseer_provider")):
        overseer = {
            "provider": state.get("overseer_provider") or "auto",
            "model": state.get("overseer_model") or "",
            "effort": state.get("overseer_effort") or "auto",
        }
    if not overseer:
        return
    overseer = _normalize_overseer(overseer)
    if not overseer:
        return
    lines = [
        f"ITERATION OVERSEER for `{state['id']}`",
        f"TASK:\n{state.get('task', '')}",
    ]
    if state.get("rubric"):
        lines.append(f"RUBRIC:\n{state['rubric']}")
    lines.append(f"FINAL REPORT:\n{state.get('final_report', '')}")
    lines.append("EVERY ROUND'S SCORES, USAGE AND CRITIQUES (history):")
    for h in state.get("history", []):
        rnd = h.get("round")
        author = h.get("author")
        rec = state["participant_records"].get(author, {})
        u = _usage_for_round(rec, rnd)
        lines.append(f"- round {rnd} author={author} critic={h.get('critic')} self={h.get('self_score')} "
                     f"critic_score={h.get('critic_score')} verdict={h.get('verdict')} "
                     f"usage={u.get('tokens', 0)}t/{u.get('seconds', 0)}s: {_clip(str(h.get('critique', '')), 400)}")
    lines.append("PARTICIPANTS' LAST WORK PATCH PATHS:")
    for pid in state.get("participants", []):
        rec = state["participant_records"].get(pid, {})
        work_keys = [k for k in rec.get("worker_ids", {}) if k.endswith("_work")]
        last = None
        if work_keys:
            last_key = sorted(work_keys, key=lambda x: int(x.split("_")[0]))[-1]
            last_id = rec["worker_ids"].get(last_key)
            if last_id:
                last_meta = _s._load_worker(last_id) or {}
                last = last_meta.get("patch_path") or os.path.join(_s.WORKERS_DIR, f"{last_id}.patch")
        lines.append(f"- {pid}: {last or 'none'}")
    lines.append(
        "Respond with: (1) confirm or overturn the winner with reasons, (2) what to merge, "
        "(3) each participant's strengths and weaknesses, (4) an efficiency ranking, "
        "(5) what the next iteration should change."
    )
    prompt = "\n\n".join(lines)
    kwargs: Dict[str, Any] = dict(
        role="Iteration Overseer", prompt=prompt,
        provider=overseer.get("provider") or "auto",
        model=overseer.get("model") or "",
        effort=overseer.get("effort") or "auto",
        track=state.get("track", "main"),
        project_dir=state.get("project_dir") or os.getcwd(),
        isolate=False,
        capabilities=["files:read"],
        priority="normal",
    )
    res = _s.t_spin_worker(**kwargs)
    match = _WORKER_ID_RE.search(res or "")
    if not match:
        return
    worker_id = match.group(0)
    state["overseer_worker"] = worker_id
    _tag_worker(worker_id, state["id"], "overseer", state["current_round"], "overseer")
    _save_state(state)
    with contextlib.suppress(Exception):
        _s._launch_worker(worker_id)
    _broadcast(state, f"[ITERATION] `{state['id']}` overseer started: {worker_id}.")


def _send_final_wake(state: Dict[str, Any]) -> None:
    if state.get("final_wake_sent"):
        return
    state["final_wake_sent"] = True
    chat_id = _originating_chat_for_iteration(state)
    if not chat_id:
        return
    rnd = str(state.get("current_round", 1))
    final_scores = {}
    score_summaries = []
    for pid in state.get("participants", []):
        rec = state.get("participant_records", {}).get(pid, {})
        pm = rec.get("peer_mean", {}).get(rnd, 0.0)
        ss = rec.get("self_score", {}).get(rnd, 0.0)
        final_scores[pid] = {"peer_mean": pm, "self_score": ss}
        score_summaries.append(f"{pid}: peer {pm:.2f}, self {ss:.2f}")
    scores_str = ", ".join(score_summaries)
    try:
        import wake_driver
        wake_driver.wake_chat(chat_id, wake_driver.WakeEvent(
            kind="iteration_done",
            chat_id=chat_id,
            text=f"[ITERATION {state['id']}] {state['status']}: winner {state.get('winner') or 'none'} ({scores_str})",
            data={
                "iteration_id": state["id"],
                "winner": state.get("winner"),
                "status": state["status"],
                "scores": final_scores,
            }
        ))
    except Exception:
        pass


def _finish_iteration(state: Dict[str, Any], status: str) -> None:
    state["status"] = status
    records = state["participant_records"]
    rnd = str(state["current_round"])
    ranked = sorted(
        state["participants"],
        key=lambda pid: (records[pid].get("peer_mean", {}).get(rnd, 0.0), records[pid].get("self_score", {}).get(rnd, 0.0)),
        reverse=True,
    )
    state["winner"] = ranked[0] if ranked else None
    state["final_report"] = _build_final_report(state)
    _broadcast(state, f"[ITERATION] `{state['id']}` stopped: {status}. Winner: {state['winner'] or 'none'}.")
    _send_final_wake(state)
    _save_state(state)
    _spin_overseer(state)


def t_stop_iteration(iteration_id: str) -> str:
    """Stop an ongoing iteration, halting pending workers, setting status to stopped without spinning overseer."""
    iteration_id = (iteration_id or "").strip()
    if not iteration_id:
        return "Error: iteration_id is required."
    with _s._state_lock(f"iteration:{iteration_id}"):
        state = _load_state(iteration_id)
        if not state:
            return f"Error: iteration '{iteration_id}' not found."
        state["status"] = "stopped"
        state["enabled"] = False
        pending = state.get("pending")
        to_stop = []
        if isinstance(pending, dict):
            to_stop.extend(pending.values())
        elif isinstance(pending, list):
            to_stop.extend(pending)
        rnd = state.get("current_round", 1)
        phase = state.get("current_phase", "work")
        for pid, rec in (state.get("participant_records") or {}).items():
            if isinstance(rec, dict):
                wids = rec.get("worker_ids", {})
                for k in (f"{rnd}_{phase}", f"{rnd}_work", f"{rnd}_review"):
                    wid = wids.get(k)
                    if wid and wid not in to_stop:
                        to_stop.append(wid)
        for wid in to_stop:
            with contextlib.suppress(Exception):
                _s.t_stop_worker(wid, reason="iteration_stopped")
        state["pending"] = []
        state["final_report"] = f"Iteration `{iteration_id}` stopped by user.\n" + _build_final_report(state)
        _save_state(state)
        _broadcast(state, f"[ITERATION] `{iteration_id}` stopped by user.")
        return f"[ITERATION STOPPED] `{iteration_id}` stopped."


def restart_iteration(iteration_id: str, **overrides) -> Dict[str, Any]:
    """Restart or continue an iteration, clearing stale final_report, winner, and overseer_worker."""
    with _s._state_lock(f"iteration:{iteration_id}"):
        state = _load_state(iteration_id)
        if not state:
            raise ValueError(f"Iteration '{iteration_id}' not found.")
        state["final_report"] = ""
        state["winner"] = None
        state["overseer_worker"] = None
        state["status"] = "active"
        state["enabled"] = True
        for k, v in overrides.items():
            if k == "overseer":
                state["overseer"] = _normalize_overseer(v)
            else:
                state[k] = v
        _save_state(state)
        return state


def t_spin_iteration(task: str, participants: Any, threshold: float = 0.85, max_rounds: int = 4,
                     rubric: str = "", track: str = "main", budget_per_phase: float = 0,
                     project_dir: str = "", stall_rounds: int = 2,
                     round_seconds: int = 600, round_tokens: int = 350000,
                     overseer: Any = None,
                     overseer_provider: str = "", overseer_model: str = "", overseer_effort: str = "") -> str:
    """Run the same task across 2-4 participants who critique and improve on one another's work."""
    task = (task or "").strip()
    if not task:
        return "Error: task is required."
    if isinstance(participants, str):
        try:
            participants = json.loads(participants)
        except ValueError:
            return "Error: participants must be a JSON list of {provider, model?, effort?, role?}."
    if not isinstance(participants, list) or not (2 <= len(participants) <= 4):
        return "Error: participants must be a list of 2-4 {provider, model?, effort?, role?} entries."
    for i, p in enumerate(participants):
        if not isinstance(p, dict) or not str(p.get("provider") or "").strip():
            return f"Error: participants[{i}] needs a provider."
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return "Error: threshold must be a number."
    if not (0 < threshold <= 10):
        return "Error: threshold must be between 0 (exclusive) and 10 (inclusive)."
    if threshold > 1:
        threshold = threshold / 10.0
    try:
        max_rounds = int(max_rounds)
    except (TypeError, ValueError):
        return "Error: max_rounds must be an integer."
    if max_rounds < 0:
        return "Error: max_rounds must be at least 0."
    try:
        stall_rounds = int(stall_rounds)
    except (TypeError, ValueError):
        return "Error: stall_rounds must be an integer."
    if stall_rounds < 0:
        return "Error: stall_rounds must be at least 0."
    try:
        round_seconds = int(round_seconds)
    except (TypeError, ValueError):
        return "Error: round_seconds must be an integer."
    if round_seconds < 0:
        return "Error: round_seconds must be at least 0."
    try:
        round_tokens = int(round_tokens)
    except (TypeError, ValueError):
        return "Error: round_tokens must be an integer."
    if round_tokens < 0:
        return "Error: round_tokens must be at least 0."
    try:
        budget_per_phase = float(budget_per_phase or 0)
    except (TypeError, ValueError):
        return "Error: budget_per_phase must be a number."
    overseer_norm = _normalize_overseer(overseer)
    if not overseer_norm and (overseer_provider or overseer_model):
        overseer_norm = _normalize_overseer({
            "provider": overseer_provider or "auto",
            "model": overseer_model or "",
            "effort": overseer_effort or "auto",
        })
    cwd = os.path.realpath(os.path.expanduser(project_dir)) if project_dir else os.getcwd()
    if not os.path.isdir(cwd):
        return f"Error: project_dir '{project_dir}' does not exist."

    iteration_id = f"iter_{uuid.uuid4().hex[:6]}"
    pids = [f"p{i + 1}" for i in range(len(participants))]
    participant_records = {}
    for pid, p in zip(pids, participants):
        participant_records[pid] = {
            "id": pid,
            "provider": str(p.get("provider") or "auto").lower(),
            "model": str(p.get("model") or ""),
            "effort": str(p.get("effort") or "auto").lower(),
            "role": str(p.get("role") or f"Participant {pid}"),
            "status": "active",
            "project_dir": cwd,
            "worker_ids": {},
            "self_score": {},
            "peer_scores_received": {},
            "peer_mean": {},
            "summary": {},
            "improved": {},
            "critiques_received": {},
            "diff_text": {},
            "parse_notes": {},
            "usage": {},
            "retries": {},
        }

    budget_cap = (budget_per_phase * len(pids) * max_rounds * 2) if (budget_per_phase > 0 and max_rounds > 0) else None
    caller = _s._self_id() if hasattr(_s, "_self_id") else ""
    origin_chat = _s._originating_chat_for_worker({"launched_by": caller}) if hasattr(_s, "_originating_chat_for_worker") else ""
    state: Dict[str, Any] = {
        "id": iteration_id,
        "version": 1,
        "track": track or "main",
        "origin_chat": origin_chat,
        "shape": "mesh",
        "enabled": True,
        "mode": "ping_pong" if len(pids) == 2 else "iteration",
        "task": task,
        "rubric": rubric or "",
        "project_dir": cwd,
        "goal_threshold": threshold,
        "max_rounds": max_rounds,
        "stall_rounds": stall_rounds,
        "round_seconds": round_seconds,
        "round_tokens": round_tokens,
        "overseer": overseer_norm,
        "current_round": 1,
        "current_phase": "work",
        "budget_per_phase": budget_per_phase,
        "budget_cap": budget_cap,
        "cumulative_cost": 0.0,
        "stall_streak": 0,
        "best_peer_mean_history": [],
        "latest_score": 0.0,
        "status": "active",
        "winner": None,
        "final_report": "",
        "overseer_worker": None,
        "participants": pids,
        "participant_records": participant_records,
        "pending": {},
        "history": [],
    }

    launched = []
    for pid in pids:
        worker_id = _spin_phase_worker(state, pid, "work", bypass_approval=False)
        if worker_id:
            state["participant_records"][pid]["worker_ids"]["1_work"] = worker_id
            state["pending"][pid] = worker_id
            launched.append(worker_id)
        else:
            state["participant_records"][pid]["status"] = "dropped"

    if len(_active_pids(state)) < 2:
        _finish_iteration(state, "insufficient_participants")
        return f"Error: fewer than 2 participants could be launched for iteration `{iteration_id}`."

    _save_state(state)
    _broadcast(state, f"[ITERATION] `{iteration_id}` round 1 work phase queued for "
                       f"{len(launched)} participant(s): {', '.join(launched)}.")
    mr = str(max_rounds) if max_rounds > 0 else "∞"
    return (f"[ITERATION STARTED] `{iteration_id}` round 1/{mr}, {len(launched)} participant(s) "
            f"queued for your approval: {', '.join(launched)}. Later rounds launch without re-approval.")


def _record_work_result(state: Dict[str, Any], pid: str, worker_id: str, meta: Dict[str, Any],
                         result: Dict[str, Any], cap_hit: bool = False) -> None:
    rnd = str(state["current_round"])
    rec = state["participant_records"][pid]
    _record_usage(state, pid, meta)
    response = str(result.get("response") or meta.get("response") or meta.get("report") or "")
    data, ok = _parse_iteration_block(response)
    if not ok:
        rec["parse_notes"][rnd] = "hit the round cap" if cap_hit else "missing or malformed iteration block; scored 0."
    rec["summary"][rnd] = str(data.get("summary") or "")[:4000] if ok else ""
    rec["improved"][rnd] = str(data.get("improved") or "")[:2000] if ok else ""
    rec["diff_text"][rnd] = _diff_text(worker_id, meta)
    worktree = meta.get("worktree") or meta.get("project_dir")
    if worktree:
        rec["project_dir"] = worktree
    # Check for screenshots passed in result/meta/iteration block
    shots = result.get("screenshots") or meta.get("screenshots") or data.get("screenshots")
    if shots:
        rec.setdefault("screenshots", {})[rnd] = shots
    _run_objective_checks(state, pid, rnd)
    self_score = _clamp_score(data.get("self_score")) if ok else 0.0
    if _has_objective_failure(state, pid, rnd):
        self_score = min(0.5, self_score)
    rec["self_score"][rnd] = self_score
    u = _usage_for_round(rec, rnd)
    self_score = rec["self_score"][rnd]
    summary = rec["summary"][rnd]
    improved = rec["improved"][rnd]
    screenshots = _extract_screenshots(summary)
    lines = [
        f"### Round {rnd} Work: {pid}",
        f"- Participant: {pid}",
        f"- Summary: {summary}",
        f"- Self score: {self_score:.2f}",
        f"- Improved: {improved}",
        f"- Usage: {u.get('tokens', 0)} tokens, {u.get('seconds', 0)} s",
    ]
    if screenshots:
        lines.append(f"- Screenshots: {', '.join(screenshots)}")
    lines.append("")
    _append_blackboard(state.get("id", ""), "\n".join(lines) + "\n")


def _record_review_result(state: Dict[str, Any], pid: str, meta: Dict[str, Any], result: Dict[str, Any]) -> None:
    rnd = str(state["current_round"])
    rec = state["participant_records"][pid]
    _record_usage(state, pid, meta)
    response = str(result.get("response") or meta.get("response") or meta.get("report") or "")
    data, ok = _parse_iteration_block(response)
    if not ok:
        rec["parse_notes"][rnd] = (rec["parse_notes"].get(rnd, "") + " | review block missing/malformed; scored 0.").strip(" |")
    if ok and isinstance(data.get("self_score"), (int, float, str)):
        s_score = _clamp_score(data.get("self_score"))
        if _has_objective_failure(state, pid, rnd):
            s_score = min(0.5, s_score)
        rec["self_score"][rnd] = s_score
    peer_scores = data.get("peer_scores") if ok and isinstance(data.get("peer_scores"), dict) else {}
    critiques = data.get("critiques") if ok and isinstance(data.get("critiques"), dict) else {}
    for other_pid, score in peer_scores.items():
        if other_pid not in state["participant_records"] or other_pid == pid:
            continue
        other = state["participant_records"][other_pid]
        clamped = _clamp_score(score)
        if _has_objective_failure(state, other_pid, rnd):
            clamped = min(0.5, clamped)
        other.setdefault("peer_scores_received", {}).setdefault(rnd, {})[pid] = clamped
    for other_pid, text in critiques.items():
        if other_pid not in state["participant_records"] or other_pid == pid:
            continue
        other = state["participant_records"][other_pid]
        other.setdefault("critiques_received", {}).setdefault(rnd, {})[pid] = str(text)[:2000]

    scores_list = [f"{p}: {s:.2f}" for p, s in peer_scores.items() if p in state["participant_records"]]
    if rnd in rec.get("self_score", {}):
        scores_list.append(f"self: {rec['self_score'][rnd]:.2f}")
    scores_str = ", ".join(scores_list) if scores_list else "none"

    rev_lines = [
        f"### Round {rnd} Review: {pid}",
        f"- Critic: {pid}",
        f"- Scores given: {scores_str}",
        "- Critiques:",
    ]
    crit_items = [f"  - {p}: {t}" for p, t in critiques.items() if p in state["participant_records"]]
    if crit_items:
        rev_lines.extend(crit_items)
    else:
        rev_lines.append("  - none")
    rev_lines.append("")
    _append_blackboard(state.get("id", ""), "\n".join(rev_lines) + "\n")


def _advance_to_review(state: Dict[str, Any]) -> None:
    state["current_phase"] = "review"
    state["pending"] = {}
    for pid in _active_pids(state):
        worker_id = _spin_phase_worker(state, pid, "review", bypass_approval=True)
        if worker_id:
            state["participant_records"][pid]["worker_ids"][f"{state['current_round']}_review"] = worker_id
            state["pending"][pid] = worker_id
        else:
            state["participant_records"][pid]["status"] = "dropped"
    _broadcast(state, f"[ITERATION] `{state['id']}` round {state['current_round']} review phase started "
                       f"for {len(state['pending'])} participant(s).")


def _advance_to_next_round(state: Dict[str, Any]) -> None:
    state["current_round"] += 1
    state["current_phase"] = "work"
    state["pending"] = {}
    state["winner"] = None
    state["final_report"] = ""
    state["overseer_worker"] = None
    state["status"] = "active"
    for pid in _active_pids(state):
        worker_id = _spin_phase_worker(state, pid, "work", bypass_approval=True)
        if worker_id:
            state["participant_records"][pid]["worker_ids"][f"{state['current_round']}_work"] = worker_id
            state["pending"][pid] = worker_id
        else:
            state["participant_records"][pid]["status"] = "dropped"
    _broadcast(state, f"[ITERATION] `{state['id']}` round {state['current_round']} work phase started "
                       f"for {len(state['pending'])} participant(s).")


def _build_final_report(state: Dict[str, Any]) -> str:
    lines = [f"Iteration `{state['id']}` finished: status={state['status']}, winner={state.get('winner') or 'none'}."]
    for pid in state["participants"]:
        rec = state["participant_records"][pid]
        scores = ", ".join(f"r{r}:self={rec['self_score'].get(r, 0):.2f}/peer={rec['peer_mean'].get(r, 0):.2f}"
                            for r in sorted(rec.get("self_score", {}), key=lambda x: int(x)))
        branch = rec.get("project_dir", "")
        lines.append(f"- {pid} ({rec.get('role')}, {rec.get('provider')}/{rec.get('model') or 'auto'}) "
                     f"[{rec.get('status')}]: {scores or 'no scores'}; branch dir: {branch}")
    return "\n".join(lines)


def _record_round_consensus(state: Dict[str, Any], rnd: str) -> None:
    active = _active_pids(state)
    if not active:
        return
    history = state.get("best_peer_mean_history", [])
    prev_best = history[-1] if history else 0.0

    scores = {}
    top_critiques = {}
    spreads = {}
    for pid in active:
        rec = state["participant_records"][pid]
        recv = rec.get("peer_scores_received", {}).get(rnd, {})
        pm = rec.get("peer_mean", {}).get(rnd, 0.0)
        ss = rec.get("self_score", {}).get(rnd, 0.0)
        scores[pid] = (pm, ss)
        spreads[pid] = (max(recv.values()) - min(recv.values())) if recv else 0.0
        crits = rec.get("critiques_received", {}).get(rnd, {})
        if crits:
            top_critiques[pid] = max(crits.values(), key=len)
        else:
            top_critiques[pid] = "none"

    leader = max(active, key=lambda p: (scores[p][0], scores[p][1]))
    leader_pm, leader_ss = scores[leader]
    gain = leader_pm - prev_best if history else 0.0
    agreement = _check_agreement(state, leader, rnd)
    agreement_str = "yes" if agreement else "no"

    lines = [
        f"## Round {rnd} consensus",
        f"- Leader: {leader} (peer {leader_pm:.2f}, gain {gain:+.2f})",
        f"- Agreement: {agreement_str}",
        "- Participant scores:",
    ]
    for pid in active:
        pm, ss = scores[pid]
        sp = spreads[pid]
        tc = top_critiques[pid].replace("\n", " ").strip()[:120]
        lines.append(f"  - {pid}: peer {pm:.2f}, self {ss:.2f}, spread {sp:.2f} (top critique: \"{tc}\")")
    lines.append("")
    consensus_block = "\n".join(lines)

    iter_id = state.get("id") or ""
    if iter_id:
        _append_blackboard(iter_id, consensus_block + "\n")
    state["last_consensus_block"] = consensus_block
    state.setdefault("consensus_blocks", {})[rnd] = consensus_block

    summary = f"leader {leader} (peer {leader_pm:.2f}, {gain:+.2f}), agreement {agreement_str}, next round started"
    state.setdefault("consensus_events", []).append({
        "round": int(rnd),
        "summary": summary,
    })
    _broadcast(state, f"[ITERATION] `{iter_id}` round {rnd} consensus: {summary}")

    chat_id = _originating_chat_for_iteration(state)
    if chat_id:
        try:
            import wake_driver
            wake_driver.wake_chat(chat_id, wake_driver.WakeEvent(
                kind="iteration_round",
                chat_id=chat_id,
                text=f"[ITERATION {iter_id}] round {rnd} consensus: {summary}",
                data={
                    "iteration_id": iter_id,
                    "round": int(rnd),
                    "leader": leader,
                    "gain": gain,
                    "agreement": agreement,
                }
            ))
        except Exception:
            pass


def _maybe_advance_after_review(state: Dict[str, Any]) -> None:
    rnd = str(state["current_round"])
    active = _active_pids(state)
    if len(active) < 2:
        _finish_iteration(state, "insufficient_participants")
        return
    converged = []
    best_mean = 0.0
    for pid in active:
        rec = state["participant_records"][pid]
        received = rec.get("peer_scores_received", {}).get(rnd, {})
        if _has_objective_failure(state, pid, rnd):
            rec["self_score"][rnd] = min(0.5, rec.get("self_score", {}).get(rnd, 0.0))
            for k in list(received.keys()):
                received[k] = min(0.5, received[k])
        peer_mean = (sum(received.values()) / len(received)) if received else 0.0
        rec["peer_mean"][rnd] = peer_mean
        best_mean = max(best_mean, peer_mean)
        self_score = rec.get("self_score", {}).get(rnd, 0.0)
        met = self_score >= state["goal_threshold"] and peer_mean >= state["goal_threshold"]
        for critic_pid, critique_text in rec.get("critiques_received", {}).get(rnd, {}).items():
            state["history"].append({
                "round": state["current_round"], "author": pid, "critic": critic_pid,
                "self_score": self_score, "critic_score": received.get(critic_pid, 0.0),
                "verdict": "met_threshold" if met else "revise", "critique": critique_text,
                "at": time.time(),
            })
        if met:
            converged.append(pid)

    _record_round_consensus(state, rnd)

    if converged:
        winners = sorted(converged, key=lambda pid: (
            state["participant_records"][pid]["peer_mean"][rnd],
            state["participant_records"][pid]["self_score"].get(rnd, 0.0),
        ), reverse=True)
        state["winner"] = winners[0]
        state["status"] = "converged"
        state["final_report"] = _build_final_report(state)
        _broadcast(state, f"[ITERATION] `{state['id']}` converged at round {state['current_round']}. "
                           f"Winner: {state['winner']}.")
        _send_final_wake(state)
        _save_state(state)
        _spin_overseer(state)
        return

    state["latest_score"] = max(state["latest_score"], best_mean)
    history = state["best_peer_mean_history"]
    if history and best_mean <= history[-1]:
        state["stall_streak"] = state.get("stall_streak", 0) + 1
    else:
        state["stall_streak"] = 0
    history.append(best_mean)

    if state.get("budget_cap") is not None:
        phase_cost = sum(float(p.get("max_budget_usd") or state.get("budget_per_phase") or 0)
                         for p in (state["participant_records"][pid] for pid in active))
        state["cumulative_cost"] = state.get("cumulative_cost", 0.0) + phase_cost
        if state["cumulative_cost"] >= state["budget_cap"]:
            _finish_iteration(state, "budget_exceeded")
            return

    stall_rounds = state.get("stall_rounds", 2)
    if stall_rounds > 0 and state.get("stall_streak", 0) >= stall_rounds:
        _finish_iteration(state, "stalled")
        return

    if state.get("max_rounds", 0) > 0 and state["current_round"] >= state["max_rounds"]:
        _finish_iteration(state, "max_rounds")
        return

    _advance_to_next_round(state)


def on_participant_done(worker_id: str) -> None:
    """Hook the supervisor calls (under the workers state lock) when a worker reaches a terminal status."""
    with contextlib.suppress(Exception):
        _on_participant_done_locked(worker_id)


def _on_participant_done_locked(worker_id: str) -> None:
    meta = _s._load_worker(worker_id)
    if not meta:
        return
    iteration_id = meta.get("iteration_id")
    if not iteration_id:
        return
    with _s._state_lock(f"iteration:{iteration_id}"):
        state = _load_state(iteration_id)
        if not state or state.get("status") in _STOP_STATUSES:
            return
        pid = meta.get("iteration_participant")
        if pid not in state["participant_records"]:
            return
        if state["pending"].get(pid) != worker_id:
            return
        rnd = state["current_round"]
        phase = state.get("current_phase", "work")
        state["pending"].pop(pid, None)
        status = str(meta.get("status") or "")
        result = {}
        exit_path = _s._exit_file(worker_id)
        with contextlib.suppress(OSError, ValueError):
            with open(exit_path, "r", encoding="utf-8") as f:
                result = json.load(f) or {}

        if status in _CAP_STATUSES:
            # Keep active; record with cap note for work, normal review record for review.
            if phase == "work":
                _record_work_result(state, pid, worker_id, meta, result, cap_hit=True)
            else:
                _record_review_result(state, pid, meta, result)
        elif status == "completed":
            if phase == "work":
                _record_work_result(state, pid, worker_id, meta, result)
            else:
                _record_review_result(state, pid, meta, result)
        else:
            rec = state["participant_records"][pid]
            if phase == "work":
                harvested = _diff_text(worker_id, meta)
                if harvested:
                    rec.setdefault("diff_text", {})[rnd] = harvested
            retry_key = f"{rnd}_{phase}"
            retries = rec.setdefault("retries", {})
            if retries.get(retry_key, 0) < 1:
                retries[retry_key] = retries.get(retry_key, 0) + 1
                new_wid = _spin_phase_worker(state, pid, phase, bypass_approval=True)
                if new_wid:
                    rec["worker_ids"][retry_key] = new_wid
                    state["pending"][pid] = new_wid
                    _save_state(state)
                    return
            rec["status"] = "dropped"
            _record_usage(state, pid, meta)

        if len(_active_pids(state)) < 2:
            _finish_iteration(state, "insufficient_participants")
            return
        if state["pending"]:
            _save_state(state)
            return

        if phase == "work":
            _advance_to_review(state)
        else:
            _maybe_advance_after_review(state)
        _save_state(state)
