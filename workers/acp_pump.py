from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import terminals
from local_models import _mark_model_bad
from permissions import (
    _command_writes,
    _large_file_read_denial,
    _packet_reread_denial,
    _terminal_command,
    get_protected_commands,
    get_protected_exceptions,
)
from store import one_line
from usage.opencode import _fetch_opencode_usage, _opencode_session_tokens
from usage.shared import _quota_error_event_text

import server as _s

OPENCODE_WATCHDOG_TIMEOUT = 120
AGY_SAFE_COMMANDS = _s.AGY_SAFE_COMMANDS


def _opencode_progress_guard(run: Dict[str, Any], write: Callable[[str], None], tool_name: str, params: Any, kind: str = "") -> None:
    """Count a tool call for the progress guard. Shell/terminal writes count as edits."""
    from worker_supervision.pumps import _progress_guard_note_tool
    _progress_guard_note_tool(run, write, tool_name, params, kind=kind)


def handle_acp_tool_call(
    run: Dict[str, Any],
    write: Callable[[str], None],
    up: Dict[str, Any],
    s_up: str = "tool_call",
) -> None:
    """Processes an ACP tool_call or tool_call_update notification with deduplication and progress guard checks."""
    tool_call: Dict[str, Any] = up.get("toolCall") if isinstance(up.get("toolCall"), dict) else dict(up)
    tcid = str(tool_call.get("toolCallId") or tool_call.get("id") or up.get("toolCallId") or up.get("id") or "").strip()

    title_val = tool_call.get("title") or tool_call.get("name") or up.get("title") or up.get("name") or ""
    title = str(title_val).strip() if title_val is not None else ""

    raw = tool_call.get("rawInput")
    if raw is None:
        raw = tool_call.get("input")
    if raw is None:
        raw = up.get("rawInput")
    if raw is None:
        raw = up.get("input")
    if raw is None:
        raw = ""

    kind = str(tool_call.get("kind") or up.get("kind") or "").strip()
    content = tool_call.get("content") or up.get("content")
    diff = tool_call.get("diff") or up.get("diff")

    cmd = ""
    if isinstance(raw, dict):
        for key in ("command", "CommandLine", "commandLine", "cmd"):
            val = raw.get(key)
            if val:
                cmd = str(val).strip()
                break
    elif isinstance(raw, str):
        cmd = raw.strip()

    has_diff = bool(diff)
    if not has_diff:
        if isinstance(content, list):
            has_diff = any(isinstance(c, dict) and (c.get("type") == "diff" or "diff" in c) for c in content)
        elif isinstance(content, dict):
            has_diff = content.get("type") == "diff" or "diff" in content

    is_edit = (kind.lower() == "edit") or has_diff or (
        bool(title) and any(k in title.lower() for k in ("edit", "write", "patch", "apply"))
    )

    is_generic_title = title.lower() in ("", "tool", "shell", "bash", "terminal", "exec", "execute")
    has_usable_name = bool(cmd) or is_edit or (bool(title) and not is_generic_title) or (
        kind.lower() in ("read", "search") and title.lower() not in ("shell", "bash", "terminal", "exec", "execute")
    )

    counted = run.setdefault("counted_tool_ids", set())
    edit_credited = run.setdefault("edit_credited_ids", set())
    seen = run.setdefault("seen_tool_ids", set())

    if tcid:
        if tcid in counted:
            if is_edit and tcid not in edit_credited:
                edit_credited.add(tcid)
                run["edits"] = run.get("edits", 0) + 1
                run["calls_since_edit"] = 0
            return

        if not has_usable_name:
            return

        counted.add(tcid)
        seen.add(tcid)
        if is_edit:
            edit_credited.add(tcid)
        display_title = title or (cmd[:60] if cmd else "") or kind or "tool"
        detail = json.dumps(raw)[:200] if isinstance(raw, dict) else str(raw)
        run["last_tool"] = detail
        write(f"[tool] {display_title}: {one_line(detail, 200)}")
        _opencode_progress_guard(
            run,
            write,
            str(display_title),
            raw if isinstance(raw, dict) else ({"command": str(raw)} if raw else {}),
            kind=str(kind),
        )
    else:
        display_title = title or "tool"
        detail = json.dumps(raw)[:200] if isinstance(raw, dict) else str(raw)
        run["last_tool"] = detail
        write(f"[tool] {display_title}: {one_line(detail, 200)}")
        if has_usable_name:
            _opencode_progress_guard(
                run,
                write,
                str(display_title),
                raw if isinstance(raw, dict) else ({"command": str(raw)} if raw else {}),
                kind=str(kind),
            )


def _pump_opencode(
    proc: subprocess.Popen, out: Any, run: Dict[str, Any], meta: Dict[str, Any], stopped: Dict[str, Any],
    quota_handler: Optional[Callable[[str, str], None]] = None,
) -> None:
    """Run OpenCode / local agent via ACP protocol over stdin/stdout pipes."""
    def write(text: str) -> None:
        out.write((text.rstrip("\n") + "\n").encode("utf-8", "replace"))
        out.flush()

    workdir = meta.get("worktree") or meta.get("project_dir") or _s.HOME
    if "role" not in run and meta.get("role"):
        run["role"] = meta.get("role")
    if "capabilities" not in run and meta.get("capabilities"):
        run["capabilities"] = meta.get("capabilities")
    msg_counter = 0

    def send_rpc(msg: Dict[str, Any]) -> None:
        raw = json.dumps(msg) + "\n"
        try:
            proc.stdin.write(raw.encode("utf-8"))  # type: ignore[union-attr]
            proc.stdin.flush()  # type: ignore[union-attr]
        except (OSError, ValueError):
            pass

    msg_counter += 1
    init_id = msg_counter
    send_rpc({"jsonrpc": "2.0", "id": init_id, "method": "initialize", "params": {"protocolVersion": 1, "clientCapabilities": {}}})

    session_new_id = None
    prompt_id = None
    session_id = None
    config_ops: List[Tuple[str, str]] = []

    is_opencode = meta.get("provider") in ("opencode", "local")
    model_val = meta.get("model")
    if meta.get("provider") == "local":
        if model_val and not str(model_val).startswith("ollama/"):
            model_val = f"ollama/{model_val}"
    if model_val and model_val != "default" and is_opencode:
        config_ops.append(("model", str(model_val)))

    effort = meta.get("effort")
    if effort and effort != "auto" and is_opencode:
        config_ops.append(("effort", str(effort)))

    curr_config_id = None
    response_chunks: List[str] = []
    stop_monitor = threading.Event()
    monitor_threads: List[threading.Thread] = []
    cancel_sent = False
    last_msg_time = time.time()
    had_tool_call = False
    had_tool_call_this_turn = False
    continued_replies: List[str] = []
    preserve_continued_reply = False
    run.setdefault("continues", 0)

    def send_cancel() -> None:
        nonlocal cancel_sent
        if not cancel_sent and session_id and proc.poll() is None:
            cancel_sent = True
            send_rpc({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}})

    def watchdog() -> None:
        while not stop_monitor.is_set() and not stopped["flag"]:
            timeout = OPENCODE_WATCHDOG_TIMEOUT
            if had_tool_call and session_id and (time.time() - last_msg_time) >= timeout:
                write("[stalled] no reply from OpenCode for 120s")
                send_cancel()
                run["finished"] = True
                proc.terminate()
                return
            stop_monitor.wait(0.05 if timeout < 5 else 1.0)

    watchdog_thread = threading.Thread(target=watchdog, name="opencode-worker-watchdog", daemon=True)
    monitor_threads.append(watchdog_thread)
    watchdog_thread.start()

    def monitor_tokens(sid: str) -> None:
        nonlocal cancel_sent
        began = time.time()
        while not stop_monitor.is_set() and not stopped["flag"]:
            hit = None
            with contextlib.suppress(Exception):
                hit = _fetch_opencode_usage().get("limit_hit")
            if hit and hit.get("at", 0) >= began and (not hit.get("model") or hit.get("model") == meta.get("model")):
                send_cancel()
                stopped["quota_error"] = f"OpenCode free limit reached: {hit['message']}"
                stopped["flag"] = True
                proc.terminate()
                return

            toks = _opencode_session_tokens(sid)
            if toks:
                run["usage"] = toks
                spent = toks.get("input_tokens", 0) + toks.get("output_tokens", 0) + toks.get("thinking_tokens", 0)
                run["spent"] = spent
                soft = run["cap"] * 2 // 3 if run.get("cap") else 0
                if soft and spent >= soft and spent < run["cap"] and not run.get("wrapping"):
                    run["wrapping"] = "asked"  # The cancelled turn's reply sends the wrap-up prompt.
                    send_cancel()
                elif run.get("cap") and spent >= run["cap"] and run.get("on_cap"):
                    cancel_sent = False
                    send_cancel()
                    run["on_cap"](f"Stopped at its token cap of {run['cap']:,}.")
                    return
            stop_monitor.wait(2.0)

    try:
        for raw in proc.stdout:  # type: ignore[union-attr]
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            last_msg_time = time.time()
            try:
                msg = json.loads(line)
            except ValueError:
                write(line)
                if quota_handler:
                    quota_handler(line, "stdout")
                continue
            error_text = _quota_error_event_text(msg)
            if error_text and quota_handler:
                quota_handler(error_text, "error_event")

            msg_id = msg.get("id")
            method = msg.get("method")
            # OpenCode's own requests carry a method and number their ids separately: only replies can answer ours.
            is_reply = method is None

            if is_reply and msg_id == init_id and msg_id is not None:
                if msg.get("error"):
                    run["acp_unavailable"] = True
                    if proc.poll() is None:
                        proc.terminate()
                    return
                msg_counter += 1
                session_new_id = msg_counter
                send_rpc({"jsonrpc": "2.0", "id": session_new_id, "method": "session/new",
                          "params": {"cwd": workdir, "mcpServers": []}})
                continue

            if is_reply and msg_id is not None and msg_id == session_new_id:
                res = msg.get("result") or {}
                session_id = res.get("sessionId")
                if msg.get("error") or not session_id:
                    run["acp_unavailable"] = True
                    if proc.poll() is None:
                        proc.terminate()
                    return
                run["acp_handshake_complete"] = True
                run["conversation"] = session_id
                if session_id and is_opencode:
                    monitor_thread = threading.Thread(
                        target=monitor_tokens, args=(session_id,), name="opencode-token-poller", daemon=True,
                    )
                    monitor_threads.append(monitor_thread)
                    monitor_thread.start()

                if config_ops:
                    cfg_id, cfg_val = config_ops.pop(0)
                    msg_counter += 1
                    curr_config_id = msg_counter
                    send_rpc({"jsonrpc": "2.0", "id": curr_config_id, "method": "session/set_config_option",
                              "params": {"sessionId": session_id, "configId": cfg_id, "value": cfg_val}})
                else:
                    msg_counter += 1
                    prompt_id = msg_counter
                    send_rpc({"jsonrpc": "2.0", "id": prompt_id, "method": "session/prompt",
                              "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": meta.get("prompt", "")}]}})
                continue

            if is_reply and msg_id == curr_config_id and curr_config_id is not None:
                if config_ops:
                    cfg_id, cfg_val = config_ops.pop(0)
                    msg_counter += 1
                    curr_config_id = msg_counter
                    send_rpc({"jsonrpc": "2.0", "id": curr_config_id, "method": "session/set_config_option",
                              "params": {"sessionId": session_id, "configId": cfg_id, "value": cfg_val}})
                else:
                    curr_config_id = None
                    msg_counter += 1
                    prompt_id = msg_counter
                    send_rpc({"jsonrpc": "2.0", "id": prompt_id, "method": "session/prompt",
                              "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": meta.get("prompt", "")}]}})
                continue

            if is_reply and prompt_id is not None and msg_id == prompt_id:
                res = msg.get("result") or {}
                err = msg.get("error") or (res.get("error") if isinstance(res, dict) else None)
                err_code = err.get("code") if isinstance(err, dict) else None
                err_text = json.dumps(err) if isinstance(err, dict) else str(err or "")
                if isinstance(res, dict):
                    err_text += " " + json.dumps(res)
                if err_code == -32000 or "authentication required" in err_text.lower():
                    bad_model = meta.get("model") or ""
                    if not bad_model:
                        cmd = meta.get("cmd") or []
                        if "--model" in cmd:
                            idx = cmd.index("--model")
                            if idx + 1 < len(cmd):
                                bad_model = cmd[idx + 1]
                    if bad_model:
                        _mark_model_bad(bad_model, err_text)
                        if "/" in bad_model:
                            _mark_model_bad(bad_model.split("/", 1)[1], err_text)
                    stopped["quota_error"] = f"Authentication required: {err_text}"
                    stopped["flag"] = True
                    if proc.poll() is None:
                        with contextlib.suppress(OSError):
                            proc.terminate()
                    return
                if run.get("wrapping") == "asked" and res.get("stopReason") == "cancelled":
                    run["wrapping"] = "running"
                    cancel_sent = False
                    left = max(0, int(run.get("cap") or 0) - int(run.get("spent") or 0))
                    msg_counter += 1
                    prompt_id = msg_counter
                    had_tool_call_this_turn = False
                    send_rpc({"jsonrpc": "2.0", "id": prompt_id, "method": "session/prompt", "params": {"sessionId": session_id or run.get("conversation"), "prompt": [
                        {"type": "text", "text": f"You've used most of your token budget: about {left:,} tokens are left. Finish the change "
                                                 "you're on, start nothing new, save anything unfinished to files, then report what's done "
                                                 "and what isn't in under 10 lines."}]}})
                    continue
                reply = (res.get("response") or res.get("text") or "".join(response_chunks)).strip()

                if session_id and is_opencode:
                    toks = _opencode_session_tokens(session_id)
                    if toks:
                        run["usage"] = toks
                        run["spent"] = toks.get("input_tokens", 0) + toks.get("output_tokens", 0) + toks.get("thinking_tokens", 0)
                spent = int(run.get("spent") or 0)
                cap = int(run.get("cap") or 0)
                past_two_thirds = bool(cap and spent >= (cap * 2 // 3))

                worker_prompt = str(meta.get("prompt") or "")
                is_edit_worker = "files:write" in (meta.get("capabilities") or [])
                plan_markers = ("I'll", "I will", "Let me", "Next,")
                is_plan = any(m in reply for m in plan_markers) or any(m.lower() in reply.lower() for m in plan_markers)

                continue_reason = None
                if res.get("stopReason") == "max_tokens":
                    continue_reason = "max_tokens"
                elif not had_tool_call_this_turn and is_edit_worker and is_plan:
                    continue_reason = "plan"

                continues = run.get("continues", 0)
                if continue_reason and continues < 2 and not past_two_thirds and not run.get("wrapping"):
                    run["continues"] = continues + 1
                    write(f"[continue] {continue_reason}")
                    if reply:
                        continued_replies.append(reply)
                    if continue_reason == "max_tokens":
                        preserve_continued_reply = True
                    response_chunks.clear()
                    had_tool_call_this_turn = False
                    cancel_sent = False
                    msg_counter += 1
                    prompt_id = msg_counter
                    continue_text = (
                        "You were cut off by the output limit. Continue exactly where you stopped; "
                        "do not repeat what you already wrote."
                        if continue_reason == "max_tokens"
                        else "Continue: do it now with your tools. Make the edits, then reply DONE with what changed."
                    )
                    continue_text += "\nYour task: " + worker_prompt[:300]
                    send_rpc({"jsonrpc": "2.0", "id": prompt_id, "method": "session/prompt", "params": {"sessionId": session_id or run.get("conversation"), "prompt": [
                        {"type": "text", "text": continue_text}]}})
                    continue

                if preserve_continued_reply:
                    reply = "\n".join([*continued_replies, reply]).strip()
                run["response"] = reply
                if not reply:
                    no_reply_err = f"{res.get('stopReason') or msg.get('error') or 'no reason given'}"
                    write(f"[no reply] OpenCode ended the turn: {no_reply_err}")
                    if "authentication required" in no_reply_err.lower():
                        bad_model = meta.get("model") or ""
                        if not bad_model:
                            cmd = meta.get("cmd") or []
                            if "--model" in cmd:
                                idx = cmd.index("--model")
                                if idx + 1 < len(cmd):
                                    bad_model = cmd[idx + 1]
                        if bad_model:
                            _mark_model_bad(bad_model, no_reply_err)
                            if "/" in bad_model:
                                _mark_model_bad(bad_model.split("/", 1)[1], no_reply_err)
                        stopped["quota_error"] = f"Authentication required: {no_reply_err}"
                        stopped["flag"] = True
                        if proc.poll() is None:
                            with contextlib.suppress(OSError):
                                proc.terminate()
                        return
                if reply:
                    write(reply)
                stop_monitor.set()
                if session_id and is_opencode:
                    final_toks = _opencode_session_tokens(session_id)
                    if final_toks:
                        run["usage"] = final_toks
                        run["spent"] = final_toks.get("input_tokens", 0) + final_toks.get("output_tokens", 0) + final_toks.get("thinking_tokens", 0)
                run["finished"] = True
                proc.terminate()
                return

            if msg_id is None and method == "session/update":
                params = msg.get("params") or {}
                up = params.get("update") or {}
                s_up = up.get("sessionUpdate")

                if s_up in ("tool_call", "tool_call_update"):
                    had_tool_call = True
                    had_tool_call_this_turn = True
                    handle_acp_tool_call(run, write, up, s_up=s_up)

                elif s_up == "agent_message_chunk":
                    chunk_text = up.get("text") or up.get("chunk") or (up.get("content") if isinstance(up.get("content"), str) else (up.get("content") or {}).get("text")) or ""
                    if chunk_text:
                        response_chunks.append(chunk_text)

                continue

            if msg_id is not None and method == "session/request_permission":
                had_tool_call = True
                had_tool_call_this_turn = True
                params = msg.get("params") or {}
                tool_call = params.get("toolCall") or {}
                tcid = tool_call.get("toolCallId") or tool_call.get("id") or ""
                seen_tc = run.setdefault("seen_tool_ids", set())
                counted_tc = run.setdefault("counted_tool_ids", set())
                if not (tcid and (tcid in seen_tc or tcid in counted_tc)):
                    if tcid:
                        seen_tc.add(tcid)
                        counted_tc.add(tcid)
                    title0 = tool_call.get("title") or tool_call.get("name") or tool_call.get("kind") or "tool"
                    raw0 = tool_call.get("rawInput") or tool_call.get("input") or ""
                    kind0 = tool_call.get("kind") or ""
                    _opencode_progress_guard(run, write, str(title0), raw0 if isinstance(raw0, dict) else ({"command": str(raw0)} if raw0 else {}), kind=str(kind0))
                title = tool_call.get("title") or tool_call.get("kind") or "tool"
                raw = tool_call.get("rawInput") or tool_call.get("input") or ""
                detail = json.dumps(raw) if isinstance(raw, dict) else str(raw or title)
                options = params.get("options") or []

                kind = tool_call.get("kind")
                places = [str(p.get("path") or "") for p in tool_call.get("locations") or [] if isinstance(p, dict)]
                root = os.path.join(os.path.realpath(workdir), "")
                inside = all(os.path.join(os.path.realpath(os.path.join(workdir, p)), "").startswith(root) for p in places if p)
                auto_approve = meta.get("permission_mode") == "auto_approve" or meta.get("skip_permissions")
                auto_safe = (meta.get("permission_mode") == "auto_safe" and kind in ("read", "search", "edit")
                             and inside and (places or kind != "edit"))
                if not auto_safe and meta.get("permission_mode") == "auto_safe":
                    # A shell tool arrives as kind "execute", never as "read", so auto_safe used
                    # to ask for plainly read-only commands like `git grep`. Headless, that ask
                    # became a denial, and workers retried it two to four times, burning budget.
                    cmd_text = ""
                    if isinstance(raw, dict):
                        for key in ("command", "CommandLine", "commandLine", "cmd"):
                            val = raw.get(key)
                            if isinstance(val, str) and val.strip():
                                cmd_text = val.strip()
                                break
                    if cmd_text:
                        cmd_kind, _why = terminals.classify_command(
                            cmd_text, AGY_SAFE_COMMANDS, get_protected_commands(), get_protected_exceptions())
                        auto_safe = (cmd_kind == "safe")
                if auto_approve or auto_safe:
                    denial = _packet_reread_denial(meta, str(title), raw, kind=str(kind or ""), locations=places) or _large_file_read_denial(meta, str(title), raw, kind=str(kind or ""), locations=places)
                    if denial:
                        write(f"[read guard] {denial}")
                        decision = "deny"
                    else:
                        decision = "allow"
                else:
                    rid = _s.request_permission(meta, str(title), detail, raw_input=raw)
                    decision = _s.await_permission(rid, stop=lambda: stopped["flag"])

                if decision not in ("allow", "allow_always"):
                    run["user_denied"] = True
                wanted_kind = "allow_once" if decision == "allow" else "allow_always" if decision == "allow_always" else "reject_once"
                selected_opt = next((o for o in options if o.get("kind") == wanted_kind), None)
                if selected_opt is None:
                    selected_opt = next((o for o in options if o.get("kind") == "reject_once"), None)
                chosen_id = selected_opt.get("optionId") if selected_opt else (options[0].get("optionId") if options else "")

                send_rpc({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "outcome": {
                            "outcome": "selected",
                            "optionId": chosen_id,
                        }
                    }
                })
                continue
    finally:
        if meta.get("provider") == "copilot" and not run.get("acp_handshake_complete"):
            run["acp_unavailable"] = True
        stop_monitor.set()
        for monitor_thread in monitor_threads:
            if monitor_thread is not threading.current_thread():
                monitor_thread.join(timeout=3)
        if session_id and is_opencode and not run.get("usage"):
            final_toks = _opencode_session_tokens(session_id)
            if final_toks:
                run["usage"] = final_toks
                run["spent"] = final_toks.get("input_tokens", 0) + final_toks.get("output_tokens", 0) + final_toks.get("thinking_tokens", 0)
