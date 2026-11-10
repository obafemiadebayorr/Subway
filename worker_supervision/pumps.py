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

# Antigravity's file-writing tools, as they appear in its step events (seen in worker logs).
_EDIT_TOOLS = frozenset({"edit_file", "write_file", "write_to_file", "replace_file_content", "multi_replace_file_content"})

_TERMINAL_TOOLS = frozenset({
    "bash", "shell", "terminal", "runterminal", "exec", "execute",
    "command", "runcommand", "execcommand", "shellcommand",
    "runinterminal", "run_in_terminal",
})


def _cache_weight() -> float:
    """Share of cache-read tokens that counts toward a worker's token cap (config cap_cache_weight)."""
    with contextlib.suppress(Exception):
        return max(0.0, float((_s._load_json("config.json") or {}).get("cap_cache_weight", 0.25)))
    return 0.25


def _is_terminal_tool(tool_name: str) -> bool:
    return re.sub(r"[\s_-]+", "", str(tool_name or "").lower()) in _TERMINAL_TOOLS


def _shell_write_counts_as_edit(tool_name: str, params: Any) -> bool:
    """A shell/terminal tool call whose command writes files counts as an edit for the progress guard."""
    if not _is_terminal_tool(tool_name):
        return False
    from permissions import _command_writes, _terminal_command
    cmd = _terminal_command(params)
    return bool(cmd and _command_writes(cmd))


_CLAUDE_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "edit", "write", "multiedit", "notebookedit", "replace"})
_PI_EDIT_TOOLS = frozenset({"edit", "write", "Edit", "Write", "replace"})

DEFAULT_COORDINATION_TOOLS = [
    "spin_worker", "spin_batch", "run_concurrent", "await_worker",
    "on_worker_done", "instruct_worker", "approve_worker", "reject_worker",
    "answer_permission", "broadcast_track", "send_note",
    "spin_swarm", "spin_iteration", "escalate_worker", "stop_worker",
    "worker_status", "collect_reports", "hud_status", "permission_requests",
    "watch_workers", "check_notes"
]
DEFAULT_COUNTED_READ_TOOLS = [
    "Read", "read", "grep", "sed", "cat", "git show", "git diff", "git log",
    "read_symbol", "context_slice", "project_files", "search"
]
DEFAULT_TEST_COMMANDS = ["pytest", "node --check", "npm test"]
DEFAULT_NO_EDIT_TOOL_CALLS = 12
DEFAULT_CALLS_SINCE_EDIT = 25
DEFAULT_NO_EDIT_WARN = 8
DEFAULT_CALLS_SINCE_EDIT_WARN = 20


def _progress_guard_settings() -> Dict[str, Any]:
    """Progress-guard tool lists and thresholds, from config.json with hardcoded defaults as fallback."""
    cfg = _s._load_json("config.json") or {}
    return {
        "coordination_tools": set(cfg.get("progress_guard_coordination_tools") or DEFAULT_COORDINATION_TOOLS),
        "test_commands": cfg.get("progress_guard_test_commands") or DEFAULT_TEST_COMMANDS,
        "no_edit_limit": int(cfg.get("no_edit_tool_calls", DEFAULT_NO_EDIT_TOOL_CALLS) or 0),
        "stale_edit_limit": int(cfg.get("calls_since_edit", DEFAULT_CALLS_SINCE_EDIT) or 0),
        "no_edit_warn": int(cfg.get("no_edit_tool_calls_warn", DEFAULT_NO_EDIT_WARN) or 0),
        "stale_edit_warn": int(cfg.get("calls_since_edit_warn", DEFAULT_CALLS_SINCE_EDIT_WARN) or 0),
    }


def _is_test_run_command(tool_name: str, params: Any, test_commands: List[str]) -> bool:
    """True if this terminal call is a test/typecheck run (pytest, node --check, npm test, ...)."""
    if not _is_terminal_tool(tool_name):
        return False
    from permissions import _terminal_command
    cmd = str(_terminal_command(params) or "")
    return any(t in cmd for t in test_commands)


_MCP_PREFIX_RE = re.compile(r"^(?:mcp__)?subway(?:[-_]context)?(?:__|_|\.)")


def _role_kind(role: Any = "", caps: Any = None) -> str:
    """Classify worker role into orchestrator, research, reviewer, or builder."""
    r = str(role or "").lower()
    if bool(re.search(r"orchestrat|lead|coordinator", r)):
        return "orchestrator"
    if bool(re.search(r"research|measure|investigat|analys|audit", r)):
        return "research"
    if bool(re.search(r"review|verif", r)):
        return "reviewer"
    if caps is not None:
        if isinstance(caps, (list, tuple, set, frozenset)):
            if "files:write" not in caps:
                return "orchestrator"
        elif isinstance(caps, str):
            if "files:write" not in caps:
                return "orchestrator"
    return "builder"


role_kind = _role_kind


def _check_git_log_clamp(run: Dict[str, Any], write: Callable[[str], None], tool_name: str, params: Any, kind: str = "") -> None:
    """Nudge to clamp git log if run without -n/--max-count/-<digit>."""
    if run.get("git_log_nudged"):
        return
    k = str(kind or "").lower()
    tn = re.sub(r"[\s_-]+", "", str(tool_name or "").lower())
    is_term = _is_terminal_tool(tool_name) or k in ("execute", "terminal", "bash", "shell", "command") or tn in {
        "bash", "shell", "terminal", "runterminal", "exec", "execute", "command", "runcommand",
        "execcommand", "shellcommand", "runinterminal", "run_in_terminal",
    }
    if not is_term:
        return
    cmd = ""
    try:
        from permissions import _terminal_command
        cmd = str(_terminal_command(params) or "")
    except Exception:
        pass
    if not cmd:
        if isinstance(params, str):
            cmd = params
        elif isinstance(params, dict):
            cmd = str(params.get("command") or params.get("CommandLine") or params.get("cmd") or params.get("input") or "")
    if not cmd:
        return
    m = re.search(r"\bgit\s+log\b(.*)$", cmd, re.DOTALL)
    if m:
        rest = m.group(1)
        if not re.search(r"(?:-n\b|-n\d+|--max-count\b|-\d+)", rest):
            run["git_log_nudged"] = True
            write("use git log -n 15 --oneline")


def _bare_tool_name(tool_name: Any) -> str:
    """`mcp__subway__spin_worker` / `subway_spin_worker` (or the old subway-context prefix) -> `spin_worker`."""
    name = str(tool_name or "")
    name = _MCP_PREFIX_RE.sub("", name)
    return name.rsplit("__", 1)[-1]


def _progress_guard_note_tool(
    run: Dict[str, Any],
    write: Callable[[str], None],
    tool_name: str,
    params: Any,
    *,
    edit_tool_names: Optional[Set[str]] = None,
    kind: str = "",
) -> None:
    """Count a tool call for the progress guard. Shell/terminal writes count as edits.

    Coordination calls (spin_worker, await_worker, ...) don't count toward idle at all.
    A test run (pytest/node --check/npm test) after at least one edit doesn't advance
    the since-last-edit counter -- it's verifying the edit, not stalling on reads.
    """
    settings = _progress_guard_settings()
    k = str(kind or "").lower()
    if _bare_tool_name(tool_name) in settings["coordination_tools"]:
        run["orchestrating"] = True
        return
    if k in {
        "spin_worker", "spin_batch", "run_concurrent", "await_worker", "on_worker_done",
        "instruct_worker", "approve_worker", "reject_worker", "answer_permission",
        "broadcast_track", "send_note",
    }:
        return

    _check_git_log_clamp(run, write, tool_name, params, kind=kind)

    if run.get("orchestrating"):
        # It dispatches work now: its reads are planning, not stalling. Count, never stop.
        run["tool_calls"] = run.get("tool_calls", 0) + 1
        return

    run["tool_calls"] = run.get("tool_calls", 0) + 1

    counts_as_edit = k == "edit"
    tn = re.sub(r"[\s_-]+", "", str(tool_name or "").lower())
    is_term = _is_terminal_tool(tool_name) or k in ("execute", "terminal", "bash", "shell", "command") or tn in {
        "bash", "shell", "terminal", "runterminal", "exec", "execute", "command", "runcommand",
        "execcommand", "shellcommand", "runinterminal", "run_in_terminal",
    }
    cmd = ""
    if is_term:
        try:
            from permissions import _terminal_command, _command_writes
            cmd = _terminal_command(params)
            if not counts_as_edit and cmd and _command_writes(cmd):
                counts_as_edit = True
        except Exception:
            pass

    names = edit_tool_names if edit_tool_names is not None else _EDIT_TOOLS
    if not counts_as_edit:
        counts_as_edit = (tool_name in names) or _shell_write_counts_as_edit(tool_name, params)

    is_test_run = (not counts_as_edit) and run.get("edits") and (
        _is_test_run_command(tool_name, params, settings["test_commands"])
        or (is_term and cmd and any(t in cmd for t in settings["test_commands"]))
    )

    if counts_as_edit:
        run["edits"] = run.get("edits", 0) + 1
        run["calls_since_edit"] = 0
    elif not is_test_run:
        run["calls_since_edit"] = run.get("calls_since_edit", 0) + 1

    kind_name = run.get("role_kind")
    if not kind_name:
        kind_name = _role_kind(
            run.get("role") or run.get("role_name") or "",
            run.get("capabilities") or run.get("caps"),
        )
    if run.get("orchestrating"):
        kind_name = "orchestrator"

    # EXEMPT research and orchestrator kinds from the nudge AND from the hard stop.
    if kind_name in ("research", "orchestrator"):
        return

    # Nudges (fire once when no edits):
    # builder: at 5 calls with no edit, nudge "5 read/search calls without an edit. Apply your first change now, or say what blocks you."
    # every other non-exempt worker: at 7 calls with no edit, the same message with 7.
    if not run.get("edits"):
        cfg = _s._load_json("config.json") or {}
        b = int(cfg.get("nudge_builder_calls", 5) or 5)
        o = int(cfg.get("nudge_default_calls", 7) or 7)
        if kind_name == "builder":
            if run["tool_calls"] >= b and not run.get("no_edit_nudge"):
                run["no_edit_nudge"] = True
                write(f"{b} read/search calls without an edit. Apply your first change now, or say what blocks you.")
        else:
            if run["tool_calls"] >= o and not run.get("no_edit_nudge"):
                run["no_edit_nudge"] = True
                write(f"{o} read/search calls without an edit. Apply your first change now, or say what blocks you.")

    # Reviewers get the nudge at 7 but no hard stop (they never edit).
    # Keep the existing hard stop at 12 (no edit) / 25 (since last edit) for builders only.
    if kind_name != "builder":
        return

    stale = int(run.get("stale_edit_limit") or 0)
    if stale and run.get("edits") and run["calls_since_edit"] >= stale and not run.get("stale_edit_stopped"):
        run["stale_edit_stopped"] = True
        write(f"[progress guard] {run['calls_since_edit']} tool calls since the last edit")
        if run.get("on_cap"):
            run["on_cap"](f"Stopped after {run['calls_since_edit']} tool calls since its last edit: it is re-reading instead of editing. Rebrief it with the remaining exact edits.")
    elif stale and run.get("edits") and settings["stale_edit_warn"] and run["calls_since_edit"] >= settings["stale_edit_warn"] and not run.get("stale_edit_warned"):
        run["stale_edit_warned"] = True
        write(f"You have {stale - run['calls_since_edit']} calls left before the progress guard stops you: make your edit now, or write your findings into your final report.")
    limit = int(run.get("no_edit_limit") or 0)
    if limit and not run.get("edits") and run["tool_calls"] >= limit and not run.get("no_edit_stopped"):
        run["no_edit_stopped"] = True
        write(f"[progress guard] {run['tool_calls']} tool calls and no edits")
        if run.get("on_cap"):
            run["on_cap"](f"Stopped after {run['tool_calls']} tool calls without an edit: "
                          "rebrief it with the exact code it has to change.")
    elif limit and not run.get("edits") and settings["no_edit_warn"] and run["tool_calls"] >= settings["no_edit_warn"] and not run.get("no_edit_warned"):
        run["no_edit_warned"] = True
        write(f"You have {limit - run['tool_calls']} calls left before the progress guard stops you: make your edit now, or write your findings into your final report.")


def stream_activity(emit: Callable[[str], None] = print, once: bool = False) -> None:
    """One line per new permission request or finished worker, the moment it happens."""
    seen = {r["id"] for r in _s._pending_permissions()} if not once else set()
    ended = ({os.path.basename(p)[:-5] for p in glob.glob(os.path.join(_s.WORKERS_DIR, "worker_*.exit"))}
             | {w["id"] for w in _s._all_workers() if w.get("status") not in _s.ACTIVE_WORKER_STATES}) if not once else set()
    seen_iter_rounds: Set[Tuple[str, int]] = set()
    iter_dir = os.path.join(_s.SUBWAY_DIR, "iterations")
    if not once and os.path.isdir(iter_dir):
        for fp in glob.glob(os.path.join(iter_dir, "iter_*.json")):
            with contextlib.suppress(Exception):
                with open(fp, "r", encoding="utf-8") as f:
                    st = json.load(f)
                    for ev in st.get("consensus_events", []):
                        seen_iter_rounds.add((st.get("id", ""), int(ev.get("round", 0))))
    while True:
        for r in _s._pending_permissions():
            if r["id"] not in seen:
                seen.add(r["id"])
                emit(f"PERMISSION {r['id']} {r['worker_id']} ({r.get('role')}) {r['tool']}: {_s.one_line(str(r['detail']), 300)}")
        for w in _s._all_workers():
            if w["id"] not in ended and w.get("status") not in _s.ACTIVE_WORKER_STATES:
                ended.add(w["id"])
                if not w.get("iteration_id"):
                    emit(f"WORKER_ENDED {w['id']} {w.get('status')}")
                    if w.get("verdict") and w.get("verdict") != "none":
                        emit(f"REVIEW_ENDED {w.get('triggered_by') or w['id']} {w.get('verdict')} {w.get('verdict_reason', '')}")
        if os.path.isdir(iter_dir):
            for fp in glob.glob(os.path.join(iter_dir, "iter_*.json")):
                with contextlib.suppress(Exception):
                    with open(fp, "r", encoding="utf-8") as f:
                        st = json.load(f)
                        iid = st.get("id")
                        for ev in st.get("consensus_events", []):
                            rnd_num = int(ev.get("round", 0))
                            if (iid, rnd_num) not in seen_iter_rounds:
                                seen_iter_rounds.add((iid, rnd_num))
                                emit(f"ITERATION_ROUND {iid} {rnd_num} {ev.get('summary', '')}")
        if once:
            return
        _s.wait_for_activity(20.0)


def _agy_rules(command: str) -> List[str]:
    """Allow-rules for a shell command: one per segment of a compound command."""
    rules = []
    for seg in re.split(r"&&|\|\||;|\|", command):
        words = seg.strip().split()
        if not words:
            continue
        target = " ".join(words[:2]) if words[0] == "git" and len(words) > 1 else words[0]
        rules.append(f"command({target})")
    return list(dict.fromkeys(rules))


def _claude_resume(cmd: List[str], conversation: str, prompt: str) -> List[str]:
    out = list(cmd)
    out[out.index("-p") + 1] = prompt
    if "--resume" in out:
        out[out.index("--resume") + 1] = conversation
    else:
        out[out.index("-p"):out.index("-p")] = ["--resume", conversation]
    return out


def _agy_resume(cmd: List[str], conversation: str, prompt: str, extra_rules: List[str]) -> List[str]:
    out = list(cmd)
    out[out.index("--print") + 1] = prompt
    if "--conversation" not in out:
        out.extend(["--conversation", conversation])
    if "--settings" in out:
        i = out.index("--settings") + 1
        settings = json.loads(out[i])
    else:
        settings = {"permissions": {"allow": []}}
        out.extend(["--settings", ""])
        i = len(out) - 1
    allow = settings["permissions"]["allow"]
    allow.extend(r for r in extra_rules if r not in allow)
    out[i] = json.dumps(settings)
    return out


def _pump_plain(proc: subprocess.Popen, out: Any, quota_handler: Callable[[str, str], None]) -> None:
    """Log a non-structured provider stream while watching error-shaped lines."""
    for raw in proc.stdout:  # type: ignore[union-attr]
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            continue
        out.write((line + "\n").encode("utf-8", "replace"))
        out.flush()
        quota_handler(line, "stdout")


def _pump_pi(
    proc: subprocess.Popen, out: Any, run: Dict[str, Any],
    quota_handler: Optional[Callable[[str, str], None]] = None,
) -> None:
    """Log pi JSON stream, track tokens and enforce caps."""
    def write(text: str) -> None:
        out.write((text.rstrip("\n") + "\n").encode("utf-8", "replace"))
        out.flush()

    for raw in proc.stdout:  # type: ignore[union-attr]
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            continue
        out.write((line + "\n").encode("utf-8", "replace"))
        out.flush()
        if quota_handler:
            quota_handler(line, "stdout")
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        ev_type = ev.get("type")
        if ev_type in ("tool_call", "tool_execution_start", "tool_use"):
            tid = ev.get("id") or ev.get("toolCallId") or ev.get("call_id") or ""
            seen_tools = run.setdefault("seen_tool_ids", set())
            if not (tid and tid in seen_tools):
                if tid:
                    seen_tools.add(tid)
                tc = ev.get("toolCall") if isinstance(ev.get("toolCall"), dict) else {}
                tool_name = ev.get("toolName") or ev.get("name") or ev.get("tool") or tc.get("name") or tc.get("toolName") or ""
                params = ev.get("input") or ev.get("arguments") or ev.get("args") or tc.get("input") or tc.get("arguments") or {}
                _progress_guard_note_tool(run, write, str(tool_name), params, edit_tool_names=_PI_EDIT_TOOLS)
            continue
        if ev_type == "message_end":
            msg = ev.get("message") or {}
            if msg.get("role") == "assistant":
                # tool calls may be embedded in assistant message content
                content = msg.get("content")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") in ("toolCall", "tool_use", "tool_call"):
                            tid = item.get("id") or item.get("toolCallId") or ""
                            seen_tools = run.setdefault("seen_tool_ids", set())
                            if tid and tid in seen_tools:
                                continue
                            if tid:
                                seen_tools.add(tid)
                            tool_name = item.get("name") or item.get("toolName") or ""
                            params = item.get("arguments") or item.get("input") or item.get("args") or {}
                            _progress_guard_note_tool(run, write, str(tool_name), params, edit_tool_names=_PI_EDIT_TOOLS)
                # Check for errors first
                stop_reason = msg.get("stopReason") or ev.get("stopReason")
                error_message = msg.get("errorMessage") or msg.get("error") or ev.get("errorMessage") or ev.get("error")
                if stop_reason == "error" or error_message:
                    if isinstance(error_message, dict):
                        error_message = error_message.get("message") or str(error_message)
                    error_message = str(error_message or "").strip()
                    if error_message:
                        run["error"] = error_message
                        run["last_error"] = error_message
                        # Call quota handler for theos_insufficient or quota-related errors
                        if quota_handler:
                            try:
                                quota_handler(error_message, "error_event")
                                quota_handler(line, "error_event")
                            except Exception:
                                pass
                        if "theos_insufficient" in error_message.lower() or "theos_exhausted" in error_message.lower():
                            if quota_handler:
                                try:
                                    quota_handler(error_message, "error_event")
                                except Exception:
                                    pass
                u = msg.get("usage") or {}
                inp = int(u.get("input") or 0)
                outp = int(u.get("output") or 0)
                think = int(u.get("reasoning") or 0)
                cache = int(u.get("cacheRead") or 0)
                step_cost = inp + outp + think + int(cache * _cache_weight())
                run["spent"] = run.get("spent", 0) + step_cost
                run["last_step"] = (inp + outp) or run.get("last_step", 0)
                usage = run.setdefault("usage", {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "thinking_tokens": 0,
                    "cache_read_tokens": 0,
                })
                usage["input_tokens"] += inp
                usage["output_tokens"] += outp
                usage["thinking_tokens"] += think
                usage["cache_read_tokens"] += cache
                # Extract assistant text
                text_parts = []
                content = msg.get("content")
                if isinstance(content, str):
                    text_parts.append(content)
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "text" and item.get("text"):
                                text_parts.append(str(item.get("text")))
                            elif item.get("text"):
                                text_parts.append(str(item.get("text")))
                elif content is None and msg.get("text"):
                    text_parts.append(str(msg.get("text")))
                full_text = "\n".join([t for t in text_parts if t and str(t).strip()]).strip()
                if full_text:
                    run["last_text"] = full_text
                    run["response"] = full_text
                if run.get("cap"):
                    spent = run.get("spent", 0)
                    if "cap_logged" not in run or run["cap_logged"] != spent:
                        run["cap_logged"] = spent
                        write(f"[cap] spent {spent} of {run['cap']}, soft {run.get('soft', 0)}")
                _s._check_cap(run)


def _pump_claude(
    proc: subprocess.Popen, out: Any, run: Dict[str, Any],
    quota_handler: Optional[Callable[[str, str], None]] = None,
) -> None:
    """Read Claude's stream-json output, log text, track tokens and caps."""
    def write(text: str) -> None:
        out.write((text.rstrip("\n") + "\n").encode("utf-8", "replace"))
        out.flush()

    for raw in proc.stdout:  # type: ignore[union-attr]
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            write(line)
            if quota_handler:
                quota_handler(line, "stdout")
            continue
        error_text = _s._quota_error_event_text(ev)
        if error_text and quota_handler:
            quota_handler(error_text, "error_event")
        kind = ev.get("type")
        run["conversation"] = ev.get("session_id") or run.get("conversation")  # A stopped run never reaches its result.
        if kind == "assistant":
            msg = ev.get("message") or {}
            usage = msg.get("usage") or {}
            # One message arrives as several events with growing usage, so only count what's new.
            seen = run.setdefault("seen_messages", {})
            step_cost = int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0) + int(usage.get("output_tokens") or 0) + int(int(usage.get("cache_read_input_tokens") or 0) * _cache_weight())
            msg_id = msg.get("id") or f"no-id-{len(seen)}"  # Messages with no id can't be repeats of each other.
            new_cost = max(0, step_cost - seen.get(msg_id, 0))
            seen[msg_id] = max(step_cost, seen.get(msg_id, 0))
            run["spent"] = run.get("spent", 0) + new_cost
            # The next call resends the context, so that's its likely cost. Thinking and output vary too much to predict from.
            context = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "output_tokens"))
            run["last_step"] = context or run.get("last_step", 0)
            if run.get("cap"):
                spent = run.get("spent", 0)
                if "cap_logged" not in run or run["cap_logged"] != spent:
                    run["cap_logged"] = spent
                    write(f"[cap] spent {spent} of {run['cap']}, soft {run.get('soft', 0)}")
            _s._check_cap(run)
            text_parts = []
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    write(block.get("text", ""))
                    run["text_streamed"] = True
                    if block.get("text"):
                        text_parts.append(str(block.get("text")))
                elif btype == "tool_use":
                    tid = block.get("id") or ""
                    seen_tools = run.setdefault("seen_tool_ids", set())
                    if tid and tid in seen_tools:
                        continue
                    if tid:
                        seen_tools.add(tid)
                    tool_name = block.get("name") or ""
                    params = block.get("input") or {}
                    _progress_guard_note_tool(run, write, tool_name, params, edit_tool_names=_CLAUDE_EDIT_TOOLS)
            if text_parts:
                run["last_text"] = "\n".join(text_parts)
        elif kind == "result":
            run["conversation"] = ev.get("session_id") or run.get("conversation")
            run["response"] = ev.get("result") or ""
            usage = ev.get("usage") or {}
            run["usage"] = {
                "input_tokens": int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
            }
            if run["response"] and (not run.get("seen_messages") or not run.get("text_streamed")):
                write(str(run["response"]))


def classify_mcp_tool(tool_name: str) -> str:
    """Classify MCP tool into 'read', 'write', or 'publish_delete' (Roadmap Item 4a)."""
    tl = (tool_name or "").lower()
    norm = tl.split("__")[-1] if "__" in tl else tl
    publish_delete_kws = (
        "publish", "delete", "destroy", "drop", "terminate", "truncate",
        "purge", "rm", "unlink", "deploy", "release", "kill_all"
    )
    if any(kw in norm for kw in publish_delete_kws):
        return "publish_delete"
    read_kws = ("read", "get", "list", "search", "find", "grep", "cat", "view", "show", "describe", "status")
    if any(norm.startswith(kw) or f"_{kw}" in norm or f"{kw}_" in norm for kw in read_kws):
        return "read"
    return "write"


def _check_publish_delete_gate(w_meta: Dict[str, Any], tool_name: str, params: Any = None) -> Optional[str]:
    """Roadmap Item 4a: publish_delete tools always require explicit human approval, regardless of mode."""
    if not isinstance(w_meta, dict):
        return None
    cls = classify_mcp_tool(tool_name)
    if cls == "publish_delete":
        return f"[MCP SAFETY GATE] Tool '{tool_name}' is classified as publish_delete and requires explicit user confirmation."
    return None


def _check_lead_tool_gate(w_meta: Dict[str, Any], tool_name: str, params: Any = None) -> Optional[str]:
    """Hard tool-call gate on lead/overseer/orchestrator workers (Roadmap Item 17)."""
    if not isinstance(w_meta, dict):
        return None
    is_lead = "subagents" in (w_meta.get("capabilities") or []) or bool(re.search(r"\b(?:lead|overseer|orchestrator)\b", str(w_meta.get("role") or "").lower()))
    if not is_lead:
        return None

    tl = tool_name.lower()
    if tl in ("spin_worker", "spin_batch", "spin_swarm"):
        w_meta["lead_read_calls"] = 0
        if w_meta.get("id"):
            with contextlib.suppress(Exception):
                with _s._state_lock("workers"):
                    cur = _s._load_worker(w_meta["id"])
                    if cur:
                        cur["lead_read_calls"] = 0
                        _s._save_worker(cur)
        return None

    read_tools = {"read_file", "read", "read_text_file", "grep", "search", "find", "search_chat", "read_chat"}
    is_read_tool = tl in read_tools or any(k in tl for k in ("read", "grep", "search"))
    if not is_read_tool:
        return None

    w_meta["lead_read_calls"] = int(w_meta.get("lead_read_calls") or 0) + 1
    if w_meta.get("id"):
        with contextlib.suppress(Exception):
            with _s._state_lock("workers"):
                cur = _s._load_worker(w_meta["id"])
                if cur:
                    cur["lead_read_calls"] = w_meta["lead_read_calls"]
                    _s._save_worker(cur)

    if w_meta["lead_read_calls"] == 6:
        return "[LEAD GATE BLOCKED] Lead/overseer tool budget reached (5 read/grep calls). You must now call spin_worker to delegate tasks to sub-workers or emit your blueprint."
    elif w_meta["lead_read_calls"] > 6:
        return "[LEAD GATE HARD STOP] Lead worker continued reading after gate warning. Terminating runaway loop."
    return None


def _pump_agy(
    proc: subprocess.Popen, out: Any, run: Dict[str, Any],
    quota_handler: Optional[Callable[[str, str], None]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Turn agy's event stream into a readable log, and note what got denied."""
    def write(text: str) -> None:
        out.write((text.rstrip("\n") + "\n").encode("utf-8", "replace"))
        out.flush()

    for raw in proc.stdout:  # type: ignore[union-attr]
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            write(line)
            if quota_handler:
                quota_handler(line, "stdout")
            # File tools don't report an error step: this notice is the only sign they were denied.
            m = re.search(r'required the "(\w+)" permission', line)
            if m and run.get("last_tool"):
                run["last_error"] = (m.group(1), run["last_tool"], run.get("last_tool_input"))
            continue
        error_text = _s._quota_error_event_text(ev)
        if error_text and quota_handler:
            quota_handler(error_text, "error_event")
        kind = ev.get("event")
        if kind == "init":
            run["conversation"] = ev.get("conversation_id") or run.get("conversation")
        elif kind == "step_update":
            st = ev.get("step_update") or {}
            used = st.get("usage") or {}
            step_cost = int(used.get("input_tokens") or 0) + int(used.get("output_tokens") or 0) + int(used.get("thinking_tokens") or 0) + int(int(used.get("cache_read_tokens") or 0) * _cache_weight())
            run["spent"] = run.get("spent", 0) + step_cost
            think = int(used.get("thinking_tokens") or 0)
            run["max_thinking"] = max(run.get("max_thinking", 0), think)
            # The next call resends the context, so that's its likely cost. Thinking varies too much to predict from.
            run["last_step"] = int(used.get("input_tokens") or 0) + int(used.get("output_tokens") or 0) or run.get("last_step", 0)
            if run.get("cap"):
                spent = run.get("spent", 0)
                if "cap_logged" not in run or run["cap_logged"] != spent:
                    run["cap_logged"] = spent
                    write(f"[cap] spent {spent} of {run['cap']}, soft {run.get('soft', 0)}")
            _s._check_cap(run)
            if run.get("cap") and step_cost > 0 and run["spent"] > run["cap"] and not run.get("overshoot_logged"):
                run["overshoot_logged"] = True
                write(f"[cap] one step overshot the cap by {run['spent'] - run['cap']:,} tokens ({step_cost:,} in that step)")
            params = (st.get("tool_info") or {}).get("parameters") or {}
            detail = params.get("CommandLine") or params.get("TargetFile") or params.get("Path") or json.dumps(params)[:200]
            start = params.get("StartLine") if "StartLine" in params else (params.get("start_line") if "start_line" in params else params.get("startLine"))
            end = params.get("EndLine") if "EndLine" in params else (params.get("end_line") if "end_line" in params else params.get("endLine"))
            offset = params.get("Offset") if "Offset" in params else params.get("offset")
            limit = params.get("Limit") if "Limit" in params else params.get("limit")

            range_suffix = ""
            if start is not None and end is not None:
                range_suffix = f" lines {start}-{end}"
            elif start is not None:
                range_suffix = f" lines {start}+"
            elif end is not None:
                range_suffix = f" lines 1-{end}"
            elif offset is not None and limit is not None:
                range_suffix = f" offset {offset} limit {limit}"
            elif offset is not None:
                range_suffix = f" offset {offset}"
            elif limit is not None:
                range_suffix = f" limit {limit}"

            detail_log = f"{detail}{range_suffix}"
            if st.get("step_type") == "tool":
                tool_name = st.get("tool_name") or ""
                if st.get("state") == "ACTIVE":
                    run["last_tool"] = str(detail)
                    run["last_tool_input"] = params
                    write(f"[tool] {tool_name}: {_s.one_line(str(detail_log), 200)}")

                    _progress_guard_note_tool(run, write, tool_name, params)

                    w_meta = meta if meta is not None else run.get("meta")
                    if w_meta is None and run.get("worker_id"):
                        w_meta = _s._load_worker(run["worker_id"])
                    if w_meta and not w_meta.get("provider"):
                        w_meta = dict(w_meta, provider="antigravity")
                    if w_meta:
                        pub_del_denial = _check_publish_delete_gate(w_meta, tool_name, params)
                        if pub_del_denial:
                            write(f"[mcp safety gate] {pub_del_denial}")
                            # Keep what was attempted, so the supervisor can ask the user and
                            # resume instead of leaving the worker dead with an unread flag.
                            run["mcp_safety_denial"] = (tool_name, str(detail), params)
                            if hasattr(proc, "terminate"):
                                with contextlib.suppress(Exception):
                                    proc.terminate()
                        else:
                            lead_denial = _check_lead_tool_gate(w_meta, tool_name, params)
                            if lead_denial:
                                write(f"[lead gate] {lead_denial}")
                                run["lead_gate_denial"] = lead_denial
                                is_hard_stop = int(w_meta.get("lead_read_calls") or 0) > 6 or "HARD STOP" in lead_denial
                                if is_hard_stop and hasattr(proc, "terminate"):
                                    with contextlib.suppress(Exception):
                                        proc.terminate()
                            else:
                                denial = (_s._readonly_terminal_denial(w_meta, tool_name, params)
                                          or _s._packet_reread_denial(w_meta, tool_name, params)
                                          or _s._large_file_read_denial(w_meta, tool_name, params))
                                if denial:
                                    is_terminal = bool(getattr(_s, "_readonly_terminal_denial", lambda *a, **k: None)(w_meta, tool_name, params))
                                    label = "read-only terminal" if is_terminal else "read guard"
                                    write(f"[{label}] {denial}")
                                    run["read_guard_denial"] = denial
                                    w_meta["read_guard_hits"] = int(w_meta.get("read_guard_hits") or 0) + 1
                                    if w_meta.get("id"):
                                        with contextlib.suppress(Exception):
                                            with _s._state_lock("workers"):
                                                cur = _s._load_worker(w_meta["id"])
                                                if cur:
                                                    cur["read_guard_hits"] = w_meta["read_guard_hits"]
                                                    _s._save_worker(cur)
                                    if not is_terminal:
                                        _s._record_read_guard_hit(w_meta)
                                    if hasattr(proc, "terminate"):
                                        with contextlib.suppress(Exception):
                                            proc.terminate()
                elif st.get("state") == "ERROR":
                    write(f"[denied or failed] {tool_name}: {_s.one_line(str(detail_log), 200)}")
                    run["last_error"] = (tool_name, str(detail), params)
        elif kind == "result":
            res = ev.get("result") or {}
            run["conversation"] = res.get("conversation_id") or run.get("conversation")
            run["response"] = res.get("response") or ""
            run["usage"] = res.get("usage") or {}
            if run["response"]:
                write(run["response"])


def _process_copilot_event(ev: Dict[str, Any], run: Dict[str, Any], write: Callable[[str], None]) -> None:
    kind = ev.get("type")
    sess_id = ev.get("sessionId") or ev.get("data", {}).get("interactionId") or ev.get("id")
    if sess_id and not run.get("conversation"):
        run["conversation"] = sess_id

    if kind == "session.auto_mode_resolved":
        chosen = ev.get("data", {}).get("chosenModel")
        if chosen:
            run["model_used"] = chosen
    elif kind == "assistant.message":
        content = ev.get("data", {}).get("content") or ""
        if content:
            run["response"] = content
            write(content)
        model = ev.get("data", {}).get("model")
        if model and not run.get("model_used"):
            run["model_used"] = model
    elif kind == "result":
        if ev.get("sessionId"):
            run["conversation"] = ev["sessionId"]
        if ev.get("result") and not run.get("response"):
            run["response"] = ev["result"]
            write(str(ev["result"]))
        usage = ev.get("usage") or {}
        reqs = usage.get("premiumRequests")  # A request count, never credits.
        creds = usage.get("credits")
        if reqs is not None or creds is not None:
            u = run.setdefault("usage", {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "credits": 0.0, "requests": 0})
            if reqs is not None:
                u["requests"] = int(reqs)
            if creds is not None:
                u["credits"] = float(creds)
    elif kind == "session.usage_checkpoint":
        data = ev.get("data") or {}
        snap = data.get("accountingSnapshot") or {}
        credits_val = None
        if data.get("totalNanoAiu") is not None:
            credits_val = data["totalNanoAiu"] / 1e9
        elif snap.get("totalNanoAiu") is not None:
            credits_val = snap["totalNanoAiu"] / 1e9
        elif snap.get("credits") is not None:
            credits_val = float(snap["credits"])

        reqs_val = data.get("totalPremiumRequests")
        if reqs_val is None:
            reqs_val = snap.get("totalPremiumRequests")

        td = data.get("tokenDetails") or snap.get("tokenDetails") or {}
        inp = int((td.get("input") or {}).get("tokenCount", 0))
        out_tok = int((td.get("output") or {}).get("tokenCount", 0))
        cache_read = int((td.get("cache_read") or {}).get("tokenCount", 0))
        td_inp = inp
        td_write = int((td.get("cache_write") or td.get("cache_creation") or {}).get("tokenCount", 0))

        mm = data.get("modelMetrics") or snap.get("modelMetrics") or {}
        mm_inp = mm_out = mm_cache = mm_reasoning = 0
        for m_name, m_info in mm.items():
            if m_name and not run.get("model_used"):
                run["model_used"] = m_name
            mu = m_info.get("usage") or {}
            mm_inp += int(mu.get("inputTokens") or 0)
            mm_out += int(mu.get("outputTokens") or 0)
            mm_cache += int(mu.get("cacheReadTokens") or 0)
            mm_reasoning += int(mu.get("reasoningTokens") or 0)

        if mm_inp > inp:
            inp = mm_inp
        if mm_cache > cache_read:
            cache_read = mm_cache

        if not inp and not out_tok:
            inp = mm_inp
            out_tok = mm_out + mm_reasoning
            cache_read = mm_cache
        elif mm_reasoning:
            out_tok += mm_reasoning

        u = run.setdefault("usage", {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "credits": 0.0, "requests": 0})
        if credits_val is not None:
            u["credits"] = credits_val
        if reqs_val is not None:
            u["requests"] = int(reqs_val)
        # Checked against live events: tokenDetails.input already excludes cache reads and writes, while
        # modelMetrics inputTokens = uncached input + cache reads + cache writes. Fresh input = uncached + cache writes.
        if mm_inp:
            fresh_inp = _s._copilot_fresh_input(mm_inp, mm_cache)
        else:
            fresh_inp = td_inp + td_write
        u["input_tokens"] = fresh_inp
        u["output_tokens"] = out_tok
        u["cache_read_tokens"] = cache_read
        run["input_excludes_cache"] = True

        step_cost = fresh_inp + out_tok + int(cache_read * _cache_weight())
        new_cost = max(0, step_cost - run.get("seen_tokens", 0))
        run["seen_tokens"] = max(step_cost, run.get("seen_tokens", 0))
        run["spent"] = run.get("spent", 0) + new_cost
        run["last_step"] = step_cost or run.get("last_step", 0)
        if run.get("cap"):
            spent = run.get("spent", 0)
            if "cap_logged" not in run or run["cap_logged"] != spent:
                run["cap_logged"] = spent
                write(f"[cap] spent {spent} of {run['cap']}, soft {run.get('soft', 0)}")
        _s._check_cap(run)
    else:
        if "response" in ev and not run.get("response"):
            run["response"] = ev["response"]
            write(str(ev["response"]))
        if "credits" in ev:
            u = run.setdefault("usage", {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "credits": 0.0, "requests": 0})
            u["credits"] = float(ev["credits"])
        if "requests" in ev:
            u = run.setdefault("usage", {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "credits": 0.0, "requests": 0})
            u["requests"] = int(ev["requests"])
        if "usage" in ev and isinstance(ev["usage"], dict):
            u_dict = ev["usage"]
            u = run.setdefault("usage", {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "credits": 0.0, "requests": 0})
            if "credits" in u_dict:
                u["credits"] = float(u_dict["credits"])
            if "requests" in u_dict:
                u["requests"] = int(u_dict["requests"])
            for k_src, k_dst in [("input_tokens", "input_tokens"), ("input", "input_tokens"),
                                 ("output_tokens", "output_tokens"), ("output", "output_tokens"),
                                 ("cache_read_tokens", "cache_read_tokens"), ("cache_read", "cache_read_tokens")]:
                if k_src in u_dict:
                    u[k_dst] = int(u_dict[k_src] or 0)
            u["input_tokens"] = _s._copilot_fresh_input(u.get("input_tokens"), u.get("cache_read_tokens"))
            run["input_excludes_cache"] = True
            step_cost = u.get("input_tokens", 0) + u.get("output_tokens", 0)
            if step_cost:
                new_cost = max(0, step_cost - run.get("seen_tokens", 0))
                run["seen_tokens"] = max(step_cost, run.get("seen_tokens", 0))
                run["spent"] = run.get("spent", 0) + new_cost
                run["last_step"] = step_cost or run.get("last_step", 0)
                if run.get("cap"):
                    _s._check_cap(run)


def _pump_copilot(
    proc: subprocess.Popen, out: Any, run: Dict[str, Any],
    quota_handler: Optional[Callable[[str, str], None]] = None,
) -> None:
    """Read Copilot's JSONL output, track text, tokens, credits and caps."""
    def write(text: str) -> None:
        out.write((text.rstrip("\n") + "\n").encode("utf-8", "replace"))
        out.flush()

    raw_lines = []
    for raw in proc.stdout:  # type: ignore[union-attr]
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        raw_lines.append(line)
        try:
            ev = json.loads(line)
        except ValueError:
            write(line)
            if quota_handler:
                quota_handler(line, "stdout")
            continue
        error_text = _s._quota_error_event_text(ev)
        if error_text and quota_handler:
            quota_handler(error_text, "error_event")
        _s._process_copilot_event(ev, run, write)
    if raw_lines:
        run["raw_output"] = "\n".join(raw_lines)

