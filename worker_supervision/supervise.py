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


def supervise_worker(worker_id: str) -> int:
    """Own one worker: enforce its timeout, relay permission requests, and record how it really ended."""
    meta = _s._load_worker(worker_id)
    if not meta:
        return 2
    stopped = {"flag": False}
    current: Dict[str, Optional[subprocess.Popen]] = {"proc": None}
    budget_stop_event = threading.Event()
    budget_thread: Optional[threading.Thread] = None

    def _on_term(_sig: int, _frame: Any) -> None:
        stopped["flag"] = True
        proc = current["proc"]
        if is_acp and run.get("conversation") and proc and proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": run["conversation"]}}) + "\n").encode("utf-8"))
                proc.stdin.flush()
        if proc and proc.poll() is None:
            proc.terminate()

    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    limit = int(meta.get("timeout_seconds") or 0)
    deadline = time.time() + limit if limit else None
    is_pi = meta.get("provider") == "pi"
    is_agy = meta.get("provider") == "antigravity"
    is_claude = meta.get("provider") == "claude"
    is_copilot = meta.get("provider") == "copilot"
    is_local = meta.get("provider") == "local"
    cmd = list(meta["cmd"])
    is_acp = meta.get("provider") in ("opencode", "local") or (
        meta.get("provider") == "copilot" and "--acp" in cmd
    )
    budget = meta.get("budget") or {}
    shared_spent: Dict[str, Any] = {"spent": sum(meta.get("usage", {}).get(k, 0) for k in ("input", "output"))}
    run: Dict[str, Any] = shared_spent

    def budget_stop(reason: str) -> None:
        if run.get("response") or run.get("finished"):
            return
        stopped["budget"] = reason  # type: ignore[assignment]
        stopped["flag"] = True
        proc = current["proc"]
        if is_acp and run.get("conversation") and proc and proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": run["conversation"]}}) + "\n").encode("utf-8"))
                proc.stdin.flush()
        if proc and proc.poll() is None:
            proc.terminate()

    def budget_watch() -> None:
        """Enforce the caps while the worker runs. A pool is shared, so other work in it counts too."""
        windows = {"weekly": budget.get("weekly_pct"), "5h": budget.get("session_pct")}
        if not any(windows.values()):
            return
        start: Dict[str, float] = {}
        prov = meta.get("provider")
        def _fetch_prov_tok() -> int:
            try:
                if prov == "claude":
                    tok = _s._fetch_claude_tokens()
                elif prov == "antigravity":
                    tok = _s._fetch_agy_tokens()
                elif prov in ("opencode", "local"):
                    tok = _s._fetch_opencode_usage()
                else:
                    tok = {}
                win = (tok.get("tokens") or {}).get("5h") or {}
                return int(win.get("input") or 0) + int(win.get("output") or 0)
            except Exception:
                return 0
        prov_tok_start = _fetch_prov_tok()
        while not stopped["flag"] and not budget_stop_event.is_set():
            try:
                record = next((p for p in _s.discover_connected_providers() if p["id"] == meta.get("provider")), None)
                pool = _s._select_pool(record, meta.get("model", "")) if record else None
            except Exception:
                pool = None
            own_tokens = shared_spent.get("spent", sum(meta.get("usage", {}).get(k, 0) for k in ("input", "output")))
            provider_tokens = max(0, _fetch_prov_tok() - prov_tok_start)
            own_share = own_tokens / max(1, provider_tokens)
            for w in (pool or {}).get("windows", []):
                cap, left = windows.get(w["key"]), w.get("left_pct")
                if not cap or left is None or w.get("disabled"):
                    continue
                start.setdefault(w["key"], float(left))
                pool_points_spent = start[w["key"]] - float(left)
                spent = pool_points_spent * min(1.0, own_share)
                save_progress(budget_spent={**(_s._load_worker(worker_id) or {}).get("budget_spent", {}), w["key"]: round(spent, 1)})
                if spent >= float(cap):
                    budget_stop(f"Stopped at its budget: {spent:.1f} points of the {w['label']} limit, cap {cap}.")
                    return
            budget_stop_event.wait(30)

    budget_thread = threading.Thread(target=budget_watch, name=f"worker-budget-{worker_id}", daemon=True)
    budget_thread.start()

    def remaining() -> Optional[float]:
        return None if deadline is None else max(1.0, deadline - time.time())

    def save_progress(**fields: Any) -> None:
        with _s._state_lock("workers"):
            cur = _s._load_worker(worker_id) or meta
            cur.update(fields)
            _s._save_worker(cur)

    result: Dict[str, Any] = {}
    retries = 0
    raw_out = ""  # set only on some retry paths; read later when the main tree changed under a worker
    copilot_acp_fallback = False
    wrapped_up = False
    asked_final_report = False
    turn_cap = 0
    final_reason = ""
    asked_denied = set()
    if meta.get("kind") == "batch":
        result = _s.run_batch(meta, stopped, deadline, save_progress)
        _s._write_json_atomic(_s._exit_file(worker_id), result)
        _s._thread_finished(worker_id, meta, result)
        stopped["flag"] = True
        budget_stop_event.set()
        budget_thread.join(timeout=2)
        return 0
    if is_local and not _s._wait_for_memory(meta["model"], stopped, min(remaining() or 900, 900)):
        reason = "Stopped on request." if stopped["flag"] else "Not enough free memory for this model within 15 minutes."
        memory_result = {"status": "stopped" if stopped["flag"] else "failed", "exit_code": None, "reason": reason}
        _s._write_json_atomic(_s._exit_file(worker_id), memory_result)
        _s._thread_finished(worker_id, meta, memory_result)
        stopped["flag"] = True
        budget_stop_event.set()
        budget_thread.join(timeout=2)
        return 0
    try:
        with open(meta["log_file"], "ab") as out:
            # Pre-launch checks
            pre_external_mtimes = {}
            prompt_text = str(meta.get("prompt") or "")
            proj_dir = os.path.realpath(meta.get("project_dir") or _s.HOME)
            ext_pattern = r'(?:~/|/Users/|/tmp/|/private/tmp/|/var/folders/)[^\s"\'`<>|;:)]+\.(?:md|json|txt)'
            for m in re.finditer(ext_pattern, prompt_text):
                raw_p = m.group(0)
                expanded = os.path.realpath(os.path.expanduser(raw_p))
                try:
                    if os.path.commonpath([proj_dir, expanded]) == proj_dir:
                        continue
                except ValueError:
                    pass
                try:
                    pre_external_mtimes[expanded] = os.path.getmtime(expanded)
                except OSError:
                    pre_external_mtimes[expanded] = None

            pre_main_dirty = {}
            if meta.get("isolate") and os.path.isdir(proj_dir):
                with contextlib.suppress(Exception):
                    for root, dirs, files in os.walk(proj_dir):
                        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules", ".venv", "venv")]
                        for fn in files:
                            full_p = os.path.join(root, fn)
                            rel_p = os.path.relpath(full_p, proj_dir)
                            try:
                                pre_main_dirty[rel_p] = os.path.getmtime(full_p)
                            except OSError:
                                pre_main_dirty[rel_p] = None

            worker_started_at = time.time()
            while True:
                run = shared_spent
                run.pop("acp_unavailable", None)
                run.pop("acp_handshake_complete", None)
                workdir = meta.get("worktree") or meta.get("project_dir") or _s.HOME
                env = dict(os.environ, PWD=workdir)
                if meta.get("provider") == "pi":
                    # The pi CLI is plain Node and runs with the app closed, but it
                    # only knows the deepai/* catalog when this points at the app's
                    # userdata. Pin it so workers never depend on inherited env.
                    with contextlib.suppress(Exception):
                        env.update(_s.pi_env(env))
                if meta.get("provider") in ("opencode", "local") and is_acp and not meta.get("skip_permissions"):
                    env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"permissions": [{"action": "*", "resource": "*", "effect": "ask"}]})
                env["SUBWAY_WORKER"] = "1"
                env["SUBWAY_WORKER_ID"] = meta["id"]
                env["SUBWAY_STATION_ID"] = meta["id"]
                if meta.get("secret"):
                    env["SUBWAY_WORKER_SECRET"] = meta["secret"]
                try:
                    proc = subprocess.Popen(cmd, cwd=workdir, stdin=subprocess.PIPE if is_acp else subprocess.DEVNULL, env=env,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                except OSError:
                    if not copilot_acp_fallback and _s._fallback_copilot_acp(meta, workdir):
                        copilot_acp_fallback = True
                        cmd = list(meta["cmd"])
                        is_acp = False
                        continue
                    raise
                current["proc"] = proc
                pump = None
                stall_timeout = _s._worker_stall_timeout(meta, proc)
                stall_event = threading.Event()
                start_time = time.time()
                next_opencode_check = start_time
                opencode_retries_seen: Set[Tuple[str, str]] = set()
                previous_activity = _s._worker_activity_signals(meta, proc, worker_started_at)
                last_activity = {signal: start_time for signal in previous_activity}
                last_activity["start"] = start_time  # Never refreshes: with no other signal, quiet since start is a stall.
                signal_interval = min(5.0, max(0.05, stall_timeout / 20))
                next_signal_check = start_time + signal_interval
                copilot_events_path = (
                    os.path.join(_s.COPILOT_DIR, "session-state", str(meta["copilot_session_id"]), "events.jsonl")
                    if meta.get("provider") == "copilot" and meta.get("copilot_session_id") else ""
                )
                next_copilot_check = start_time

                def quota_handler(text: str, source: str) -> None:
                    q_err = _s.check_quota_error(str(meta.get("provider") or ""), text, source=source)
                    if not q_err or stopped.get("quota_error"):
                        return
                    if (meta.get("provider") == "copilot"
                            and "from --model flag is not available" in text.lower()):
                        return
                    stopped["quota_error"] = f"Quota/usage limit error: {q_err}"
                    stopped["flag"] = True
                    if proc.poll() is None:
                        with contextlib.suppress(OSError):
                            proc.terminate()

                def pump_stderr() -> None:
                    for raw in proc.stderr:  # type: ignore[union-attr]
                        line = raw.decode("utf-8", "replace").rstrip("\r\n")
                        if line:
                            out.write((line + "\n").encode("utf-8", "replace"))
                            out.flush()
                            quota_handler(line, "stderr")

                def stall_watchdog() -> None:
                    nonlocal next_opencode_check, previous_activity, next_signal_check, next_copilot_check
                    while not stall_event.is_set() and not stopped["flag"]:
                        now = time.time()
                        if copilot_events_path and now >= next_copilot_check:
                            next_copilot_check = now + 0.5
                            events_signature = _s._file_activity_signature(copilot_events_path)
                            if events_signature != previous_activity.get("copilot_events"):
                                previous_activity["copilot_events"] = events_signature
                                last_activity["copilot_events"] = now
                        if is_acp and meta.get("provider") == "opencode" and now >= next_opencode_check:
                            next_opencode_check = now + 3
                            retry = _s._poll_opencode_worker_retry(meta, worker_started_at, opencode_retries_seen)
                            if retry and retry.get("quota"):
                                stopped["quota_error"] = f"Quota/usage limit error: {retry['reason']}"
                                stopped["quota_result"] = retry.get("result")
                                stopped["quota_handled"] = True
                                stopped["flag"] = True
                                if run.get("conversation") and proc.poll() is None:
                                    with contextlib.suppress(Exception):
                                        proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": run["conversation"]}}) + "\n").encode("utf-8"))
                                        proc.stdin.flush()
                                if proc.poll() is None:
                                    proc.terminate()
                                return
                        if now >= next_signal_check:
                            # git, ps and the OpenCode DB are too costly to read every half second.
                            next_signal_check = now + signal_interval
                            signals = _s._worker_activity_signals(meta, proc, worker_started_at)
                            _s._update_worker_activity(previous_activity, last_activity, signals, now)
                        if _s._worker_is_stalled(
                            last_activity, now, stall_timeout,
                            active=bool(previous_activity.get("copilot_tool_running")),
                        ):
                            if copilot_events_path and _s._copilot_has_open_tool_execution(copilot_events_path):
                                previous_activity["copilot_tool_running"] = True
                                last_activity["copilot_tool_running"] = now
                            else:
                                quiet = ", ".join(sorted(last_activity)) or "no signals"
                                stopped["stall"] = f"No activity for {int(stall_timeout)}s from any signal ({quiet})."
                                stopped["flag"] = True
                                if is_acp and run.get("conversation") and proc.poll() is None:
                                    with contextlib.suppress(Exception):
                                        proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": run["conversation"]}}) + "\n").encode("utf-8"))
                                        proc.stdin.flush()
                                if proc.poll() is None:
                                    proc.terminate()
                                return
                        stall_event.wait(min(0.5, max(0.05, stall_timeout / 20)))

                stall_thread = threading.Thread(
                    target=stall_watchdog, name=f"worker-stall-{worker_id}", daemon=True,
                )
                stall_thread.start()
                stderr_thread = threading.Thread(
                    target=pump_stderr, name=f"worker-stderr-{worker_id}", daemon=True,
                )
                stderr_thread.start()

                if is_agy or is_claude or is_acp or is_copilot or is_pi:
                    run["spent"] = max(run.get("spent", 0), sum(meta.get("usage", {}).get(k, 0) for k in ("input", "output")))
                    run["cap"], run["on_cap"] = turn_cap or int(budget.get("max_tokens") or 0), budget_stop
                    run["soft"] = run["cap"] * 2 // 3 if (not wrapped_up and _s._resume_reuses_cache(meta)) else 0
                    caps_now = meta.get("capabilities") or []
                    cfg = _s._load_json("config.json") or {}
                    run["no_edit_limit"] = int(cfg.get("no_edit_tool_calls", 12) or 0) if "files:write" in caps_now else 0
                    if run["no_edit_limit"] and (meta.get("code_ranges") or re.search(r"(?m)^\s*(EDIT \d|old:)", str(meta.get("prompt") or ""))):
                        run["no_edit_limit"] = min(run["no_edit_limit"], int(cfg.get("no_edit_tool_calls_literal", 6) or 6))
                    run["stale_edit_limit"] = int(cfg.get("calls_since_edit", 25) or 0) if "files:write" in caps_now else 0
                    run["meta"] = meta
                    run["worker_id"] = worker_id
                    if is_acp:
                        pump = threading.Thread(
                            target=_s._pump_opencode, args=(proc, out, run, meta, stopped, quota_handler),
                            name=f"worker-output-{worker_id}", daemon=True,
                        )
                    else:
                        target_pump = _s._pump_pi if is_pi else (_s._pump_agy if is_agy else (_s._pump_copilot if is_copilot else _s._pump_claude))
                        pump_args = (proc, out, run, quota_handler, meta) if is_agy else (proc, out, run, quota_handler)
                        pump = threading.Thread(
                            target=target_pump, args=pump_args,
                            name=f"worker-output-{worker_id}", daemon=True,
                        )
                else:
                    pump = threading.Thread(
                        target=_s._pump_plain, args=(proc, out, quota_handler),
                        name=f"worker-output-{worker_id}", daemon=True,
                    )
                pump.start()
                try:
                    code = proc.wait(timeout=remaining())
                except subprocess.TimeoutExpired:
                    stall_event.set()
                    if is_acp and run.get("conversation") and proc.poll() is None:
                        with contextlib.suppress(Exception):
                            proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": run["conversation"]}}) + "\n").encode("utf-8"))
                            proc.stdin.flush()
                    proc.terminate()
                    try:
                        code = proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        code = proc.wait()
                    stall_event.set()
                    pump.join(timeout=5)
                    stderr_thread.join(timeout=5)
                    stall_thread.join(timeout=1)
                    for stream in (proc.stdin, proc.stdout, proc.stderr):
                        if callable(getattr(stream, "close", None)):
                            with contextlib.suppress(OSError):
                                stream.close()
                    result = {"status": "timed_out", "exit_code": code, "reason": f"Stopped after the {limit}s timeout."}
                    break
                stall_event.set()
                if pump:
                    pump.join(timeout=5)
                stderr_thread.join(timeout=5)
                stall_thread.join(timeout=1)
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if callable(getattr(stream, "close", None)):
                        with contextlib.suppress(OSError):
                            stream.close()
                handshake_stalled = bool(stopped.get("stall") and not stopped.get("quota_error"))
                if (is_copilot and is_acp and run.get("acp_unavailable")
                        and (not stopped["flag"] or handshake_stalled)
                        and not copilot_acp_fallback and _s._fallback_copilot_acp(meta, workdir)):
                    copilot_acp_fallback = True
                    stopped["flag"] = False
                    stopped.pop("stall", None)
                    cmd = list(meta["cmd"])
                    is_acp = False
                    continue
                if pump:
                    save_kwargs = {}
                    if run.get("model_used"):
                        meta["model_used"] = run["model_used"]
                        save_kwargs["model_used"] = run["model_used"]
                    if run.get("usage"):
                        # A killed turn sends no total, so the next turn's total includes it. The live count shows which applies.
                        final = sum(int(run["usage"].get(k) or 0) for k in ("input_tokens", "output_tokens", "thinking_tokens"))
                        saved = sum(int(meta.get("usage", {}).get(k) or 0) for k in ("input", "output"))
                        if saved and abs(final - run.get("spent", 0)) < abs(saved + final - run.get("spent", 0)):
                            meta["usage"] = {"input": 0, "output": 0, "cache_read": 0}
                        _s._add_usage(meta, run.pop("usage"))
                        save_kwargs.update({"usage": meta["usage"], "conversation": run.get("conversation")})
                        save_progress(**save_kwargs)
                    elif save_kwargs:
                        save_progress(**save_kwargs)
                    usage = meta.setdefault("usage", {"input": 0, "output": 0, "cache_read": 0})
                    if int(usage.get("input") or 0) + int(usage.get("output") or 0) < run.get("spent", 0):
                        diff = run["spent"] - (int(usage.get("input") or 0) + int(usage.get("output") or 0))
                        usage["input"] = int(usage.get("input") or 0) + diff
                        save_progress(usage=usage)

                if stopped.get("stall") or stopped.get("quota_error"):
                    reason = str(stopped.get("stall") or stopped.get("quota_error"))
                    if stopped.get("quota_handled"):
                        result = stopped.get("quota_result") or {"status": "failed", "exit_code": code, "reason": reason}
                    else:
                        result = _s._handle_worker_exhaustion_or_stall(worker_id, meta, reason, is_stall=bool(stopped.get("stall")))
                    break

                if stopped["flag"]:
                    fresh_meta = _s._load_worker(worker_id) or meta
                    stop_r = fresh_meta.get("stop_reason") or ""
                    is_b = bool(stopped.get("budget") or (stop_r and "budget" in stop_r.lower()))
                    why = stopped.get("budget") or (stop_r if is_b else None)
                    token_cap = int(budget.get("max_tokens") or 0)
                    # One call costs the whole context, so the save turn only runs if a call of that size still fits.
                    room = not token_cap or run.get("spent", 0) + run.get("last_step", 0) < token_cap
                    if why and token_cap and not room and why.startswith("Told to wrap up"):
                        why = f"Stopped at {run.get('spent', 0):,} of its {token_cap:,} token budget: no room left to wrap up."
                    if why and (is_agy or is_claude) and _s._resume_reuses_cache(meta) and run.get("conversation") and not wrapped_up and room:
                        # Spending the cap shouldn't lose the work: one last short turn to save what it found.
                        wrapped_up = True
                        final_reason = why
                        stopped["flag"] = False
                        stopped.pop("budget", None)
                        turn_cap = token_cap or run.get("spent", 0) + 20000
                        left = token_cap - run.get("spent", 0) if token_cap else 20000
                        ask_wrap = (f"You've used most of your token budget: about {left:,} tokens are left, and every tool call costs "
                                    "your whole context. Finish the change you're on, start nothing new, save anything unfinished to "
                                    "files, then report what's done and what isn't in under 10 lines.")
                        cmd = _s._agy_resume(cmd, run["conversation"], ask_wrap, []) if is_agy else _s._claude_resume(cmd, run["conversation"], ask_wrap)
                        continue
                    reason = why or stop_r or final_reason or "Stopped on request."
                    if (run.get("response") or run.get("last_text")) and "partial report" not in reason.lower():
                        reason = f"{reason} A partial report exists."
                    result = {"status": "budget_stopped" if is_b else "stopped", "exit_code": code, "reason": reason}
                    break
                if final_reason:  # It finished inside the budget after being told to wrap up.
                    result = {"status": "completed", "exit_code": code, "reason": final_reason}
                    break
                if is_agy and run.get("lead_gate_denial") and run.get("conversation"):
                    denial_msg = run.pop("lead_gate_denial")
                    save_progress(lead_read_calls=meta.get("lead_read_calls", 0))
                    cmd = _s._agy_resume(cmd, run["conversation"], denial_msg, [])
                    continue
                if is_agy and run.get("mcp_safety_denial") and run.get("conversation"):
                    # The publish/delete gate sets this from pumps.py and the pump terminates the
                    # turn. Nothing used to read it, so a worker that touched publish/delete was
                    # simply killed without the user ever being asked. Ask, then resume.
                    tool_name, detail, raw_input = run.pop("mcp_safety_denial")
                    rid = _s.request_permission(meta, tool_name, detail, [], raw_input=raw_input)
                    save_progress(waiting_permission=rid)
                    decision = _s.await_permission(rid, min(_s.PERMISSION_WAIT, int(remaining() or _s.PERMISSION_WAIT)),
                                                   stop=lambda: stopped["flag"])
                    save_progress(waiting_permission=None)
                    if stopped["flag"]:
                        fresh_meta = _s._load_worker(worker_id) or meta
                        stop_r = fresh_meta.get("stop_reason") or ""
                        is_b = bool(stopped.get("budget") or (stop_r and "budget" in stop_r.lower()))
                        result = {"status": "budget_stopped" if is_b else "stopped", "exit_code": code,
                                  "reason": stop_r or "Stopped on request."}
                        break
                    if decision == "deny":
                        cmd = _s._agy_resume(
                            cmd, run["conversation"],
                            f"The user denied this publish/delete action: {detail}. Don't retry it. "
                            "Continue without it and report what you couldn't do.", [])
                    else:
                        cmd = _s._agy_resume(
                            cmd, run["conversation"],
                            f"The user allowed this publish/delete action: {detail}. Do it once, then "
                            "continue your task from where you stopped.", [f"{tool_name}({detail})"])
                    continue
                if is_agy and run.get("read_guard_denial") and run.get("conversation"):
                    denial_msg = run.pop("read_guard_denial")
                    save_progress(read_guard_hits=meta.get("read_guard_hits", 0))
                    cmd = _s._agy_resume(cmd, run["conversation"], denial_msg, [])
                    continue
                denied = run.get("last_error")
                if is_agy and code == 0 and denied and run.get("conversation") and denied[1] not in asked_denied:
                    tool, detail, *denied_input = denied
                    asked_denied.add(detail)
                    project = os.path.realpath(meta.get("project_dir") or _s.HOME)
                    if tool in ("run_command", "command"):
                        once, rules = [f"command({detail})"], _s._agy_rules(detail)
                    else:
                        scope = project if os.path.realpath(detail).startswith(project + os.sep) else os.path.dirname(detail)
                        once, rules = [f"{tool}({detail})"], [f"{tool}({scope})"]
                    rid = _s.request_permission(meta, tool, detail, rules,
                                             raw_input=denied_input[0] if denied_input else None)
                    save_progress(waiting_permission=rid)
                    decision = _s.await_permission(rid, min(_s.PERMISSION_WAIT, int(remaining() or _s.PERMISSION_WAIT)),
                                                stop=lambda: stopped["flag"])
                    save_progress(waiting_permission=None)
                    if stopped["flag"]:
                        fresh_meta = _s._load_worker(worker_id) or meta
                        stop_r = fresh_meta.get("stop_reason") or ""
                        is_b = bool(stopped.get("budget") or (stop_r and "budget" in stop_r.lower()))
                        result = {"status": "budget_stopped" if is_b else "stopped", "exit_code": code, "reason": stop_r or "Stopped on request."}
                        break
                    if decision == "deny":
                        permission_record = {}
                        with contextlib.suppress(OSError, ValueError):
                            with open(_s._perm_file(rid), "r", encoding="utf-8") as f:
                                loaded_record = json.load(f)
                                permission_record = loaded_record if isinstance(loaded_record, dict) else {}
                        denial_reason = permission_record.get("denial_reason")
                        reason = (f"Subway denied this read: {denial_reason}. Don't retry it. Continue without it, "
                                  "and report what you couldn't do." if denial_reason else
                                  f"The user denied this: {detail}. Don't retry it. Continue without it, "
                                  "and report what you couldn't do.")
                        cmd = _s._agy_resume(cmd, run["conversation"], reason, [])
                    else:
                        # Allow covers this exact command. Only allow_always widens to the whole program.
                        grant = rules if decision == "allow_always" else once
                        cmd = _s._agy_resume(cmd, run["conversation"], f"The user allowed this: {detail}. Run it, then "
                                          f"continue your task from where you stopped.", grant)
                    continue
                if code != 0 and is_agy and run.get("conversation") and retries < 2:
                    retries += 1  # A dropped connection shouldn't throw away a worker's whole conversation.
                    time.sleep(5 * retries)
                    cmd = _s._agy_resume(cmd, run["conversation"], "Your connection dropped. Continue your task "
                                      "from where you stopped.", [])
                    continue
                if code != 0 and is_copilot and retries < 1:
                    raw_out = run.get("raw_output") or ""
                    if not raw_out and os.path.exists(meta.get("log_file", "")):
                        with contextlib.suppress(Exception):
                            with open(meta["log_file"], "r", encoding="utf-8", errors="replace") as f:
                                raw_out = f.read()
                    if "from --model flag is not available" in raw_out:
                        m_match = re.search(r'Model\s+["\']?([^"\'\s]+)["\']?\s+from --model flag is not available', raw_out)
                        refused_id = m_match.group(1) if m_match else meta.get("model", "")
                        if not refused_id or refused_id == "auto":
                            if "--model" in cmd:
                                idx = cmd.index("--model")
                                if idx + 1 < len(cmd):
                                    refused_id = cmd[idx + 1]
                        if refused_id and refused_id != "auto":
                            _s.record_copilot_cli_refused(refused_id)
                            msg = f"[copilot] {refused_id} is not available to the CLI on this plan; using auto"
                            _s.log(msg)
                            out.write((msg + "\n").encode("utf-8", "replace"))
                            out.flush()
                            retries += 1
                            if "--model" in cmd:
                                idx = cmd.index("--model")
                                if idx + 1 < len(cmd):
                                    cmd[idx + 1] = "auto"
                            meta["model"] = "auto"
                            continue
                if is_acp and run.get("finished"):
                    code = 0
                if is_copilot and code == 0:
                    used_m = meta.get("model") or ""
                    if not used_m and "--model" in cmd:
                        idx = cmd.index("--model")
                        if idx + 1 < len(cmd):
                            used_m = cmd[idx + 1]
                    if used_m and used_m != "auto":
                        _s.record_copilot_cli_accepted(used_m)

                raw_out = run.get("raw_output") or ""
                if not raw_out and os.path.exists(meta.get("log_file", "")):
                    with contextlib.suppress(Exception):
                        with open(meta["log_file"], "r", encoding="utf-8", errors="replace") as f:
                            raw_out = f.read()

                if is_agy and "sensitive words" in raw_out.lower() and retries < 1:
                    retries += 1
                    msg = "[antigravity] prompt contains sensitive words, sanitizing and retrying"
                    _s.log(msg)
                    out.write((msg + "\n").encode("utf-8", "replace"))
                    out.flush()
                    # Sanitize prompt and retry once on same model
                    sanitized_prompt = _s._safe_prompt(meta.get("prompt", ""), provider="antigravity")
                    meta["prompt"] = sanitized_prompt
                    if "--print" in cmd:
                        idx = cmd.index("--print")
                        if idx + 1 < len(cmd):
                            cmd[idx + 1] = sanitized_prompt
                    continue

                if code != 0:
                    fresh_meta = _s._load_worker(worker_id) or meta
                    stop_r = fresh_meta.get("stop_reason") or ""
                    if stop_r and "budget" in stop_r.lower():
                        result = {"status": "budget_stopped", "exit_code": code, "reason": stop_r}
                    elif stop_r:
                        result = {"status": "stopped", "exit_code": code, "reason": stop_r}
                    else:
                        result = {"status": "failed", "exit_code": code}
                elif is_acp and run.get("user_denied") and not run.get("response"):
                    result = {"status": "stopped", "exit_code": 0, "reason": "You denied its request, so it stopped."}
                elif (is_agy or is_acp or is_copilot) and not run.get("response"):
                    refused = _s._headless_permission_denial(raw_out)
                    dirty = None
                    if refused:
                        with contextlib.suppress(OSError, subprocess.SubprocessError):
                            dirty = _s._git_dirty_paths(meta.get("project_dir") or _s.HOME)
                    if refused and dirty:
                        detail = run.get("last_tool") or f'the "{refused}" permission'
                        result = {"status": "completed", "exit_code": 0,
                                  "reason": f"Finished, but headless mode could not prompt for permission to run "
                                            f"{detail}. Its changes are kept — review them."}
                    else:
                        # Gap 6b: ask once for the agent's own final report before failing.
                        if is_agy and run.get("conversation") and not asked_final_report:
                            asked_final_report = True
                            ask_report = ("Your turn ended without a final message. "
                                          "Write your final report now in your own words.")
                            msg = "[supervisor] agent ended without a final message; requesting a final report"
                            _s.log(msg)
                            out.write((msg + "\n").encode("utf-8", "replace"))
                            out.flush()
                            run.pop("response", None)
                            cmd = _s._agy_resume(cmd, run["conversation"], ask_report, [])
                            continue
                        result = {"status": "failed", "exit_code": 0, "reason": "The agent finished without producing a result."}
                else:
                    result = {"status": "completed", "exit_code": 0}
                break
    except OSError as exc:
        result = {"status": "failed", "exit_code": None, "reason": f"Could not start agent: {exc}"}
    stopped["flag"] = True
    budget_stop_event.set()
    budget_thread.join(timeout=2)
    if is_local and _s.local_profile(_s.memory_snapshot()["total"])["keep_alive"] == "0":
        _s._unload_local(meta["model"])  # On a small machine, memory goes back the moment the work ends.
    fresh = _s._load_worker(worker_id) or meta
    wt_note = ""
    if fresh.get("worktree"):
        try:
            wt_note = _s._collect_worktree(fresh, partial=result.get("status") != "completed")
            result["reason"] = f"{result.get('reason') or ''} {wt_note}".strip()
            if "Partial patch kept" in wt_note:
                fresh["partial_patch"] = True
                result["partial_patch"] = True
                _s._save_worker(fresh)
        except (OSError, subprocess.SubprocessError) as exc:
            result["reason"] = f"Worktree kept at {fresh['worktree']}: {exc}"
    no_changes = bool(fresh.get("isolate") and wt_note == "No files changed.")
    if result.get("status") == "completed" and fresh.get("git_state_before"):
        try:
            no_changes = _s._git_change_fingerprint(fresh.get("project_dir") or _s.HOME) == fresh["git_state_before"]
        except (OSError, subprocess.SubprocessError) as exc:
            result["status"] = "failed"
            result["reason"] = f"Could not verify worker changes: {exc}"

    # Check delegation, external prompt paths, and main-tree writes
    delegated_ids = {str(x) for x in (fresh.get("triggered") or []) if x}
    for w_path in glob.glob(os.path.join(_s.WORKERS_DIR, "worker_*.json")):
        if os.path.basename(w_path) == f"{worker_id}.json":
            continue
        with contextlib.suppress(Exception):
            with open(w_path, "r", encoding="utf-8") as wf:
                w_data = json.load(wf)
            if (w_data.get("parent_id") == worker_id
                    or w_data.get("launched_by") == worker_id
                    or w_data.get("triggered_by") == worker_id
                    or w_data.get("id") in delegated_ids):
                delegated_ids.add(str(w_data.get("id") or os.path.basename(w_path)))
    delegated_count = len(delegated_ids)
    if delegated_count > 0:
        no_changes = False
        del_note = f"delegated to {delegated_count} workers"
        result["reason"] = f"{result.get('reason') or ''} {del_note}".strip()

    for ext_p, pre_m in pre_external_mtimes.items():
        try:
            cur_m = os.path.getmtime(ext_p)
            if pre_m is None or cur_m > pre_m or cur_m >= (worker_started_at - 1.0):
                no_changes = False
                break
        except OSError:
            pass

    if no_changes and fresh.get("isolate") and fresh.get("project_dir"):
        proj_dir = os.path.realpath(fresh.get("project_dir"))
        main_changed = []
        if os.path.isdir(proj_dir):
            with contextlib.suppress(Exception):
                for root, dirs, files in os.walk(proj_dir):
                    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules", ".venv", "venv")]
                    for fn in files:
                        full_p = os.path.join(root, fn)
                        rel_p = os.path.relpath(full_p, proj_dir)
                        try:
                            cur_m = os.path.getmtime(full_p)
                        except OSError:
                            cur_m = None
                        if rel_p not in pre_main_dirty or cur_m != pre_main_dirty.get(rel_p):
                            main_changed.append(rel_p)
        if main_changed:
            inspect_text = raw_out or ""
            if not inspect_text and fresh.get("log_file") and os.path.exists(fresh["log_file"]):
                with contextlib.suppress(Exception):
                    with open(fresh["log_file"], "r", encoding="utf-8", errors="replace") as f:
                        inspect_text = f.read()
            mentioned_files = [p for p in main_changed if p in inspect_text or os.path.basename(p) in inspect_text]
            if mentioned_files:
                no_changes = False
                result["status"] = "completed"
                main_tree_note = f'wrote to the main tree instead of its worktree: {", ".join(mentioned_files)}'
                result["reason"] = f"{result.get('reason') or ''} {main_tree_note}".strip()

    if result.get("status") == "completed" and (fresh.get("isolate") or fresh.get("git_state_before")):
        _s._settle_worker_result(result, fresh, no_changes, str(run.get("response") or run.get("last_text") or ""))
        if result.get("status") == "completed":
            patch_file = fresh.get("patch_path") or os.path.join(_s.WORKERS_DIR, f"{worker_id}.patch")
            if fresh.get("isolate") and os.path.exists(patch_file):
                with contextlib.suppress(Exception):
                    _s.verify_worker(worker_id)
    # Persist finish-time counters (P1). Never fake values; omit unknown keys.
    with contextlib.suppress(Exception):
        _tc = int(run.get("tool_calls") or 0)
        _ed = int(run.get("edits") or 0)
        _st = {"tool_calls": _tc, "edits": _ed}
        _usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
        _think = _usage.get("thinking_tokens")
        if _think is None:
            _think = run.get("max_thinking")
        try:
            _think_i = int(_think) if _think is not None else 0
        except Exception:
            _think_i = 0
        if _think_i > 0:
            _st["thinking_tokens"] = _think_i
        try:
            _st["wall_seconds"] = int(max(0, time.time() - worker_started_at))
        except Exception:
            pass
        fresh["stats"] = _st
        _s._save_worker(fresh)
    if (result.get("status") == "completed" and not fresh.get("isolate")
            and any(step.get("verdict") for step in fresh.get("then") or [])):
        _s._capture_in_place_review_diff(fresh)
        _s._save_worker(fresh)
    with contextlib.suppress(Exception):
        result["response"] = str(run.get("response") or run.get("last_text") or "")
    if result.get("status") == "completed":
        _s._clear_model_stall_cooldown(str(fresh.get("model_used") or fresh.get("model") or ""))
    _s._write_json_atomic(_s._exit_file(worker_id), result)
    _s._thread_finished(worker_id, meta, result)
    _s._wake_worker_milestone(worker_id, meta, result)
    _s._update_worker_status(_s._load_worker(worker_id) or meta)
    with contextlib.suppress(Exception):
        _s._run_triggers(worker_id, result)
    # Iteration hook: never let it break the normal end of a worker, including a failed record load.
    with contextlib.suppress(Exception):
        if (_s._load_worker(worker_id) or {}).get("iteration_id"):
            import iteration
            iteration.on_participant_done(worker_id)
    with contextlib.suppress(Exception):
        _s.start_approved_workers()  # A slot just opened.
    return 0

