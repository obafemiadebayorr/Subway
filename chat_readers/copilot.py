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
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple, Union

import terminals
import native_terminals
import threads

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

from store import ttl_cached
import server as _s

from .common import chat_turns
def _copilot_session_store() -> Dict[str, Dict[str, Any]]:
    """Read the Copilot CLI session index without opening it for writes."""
    path = os.path.join(_s.COPILOT_DIR, "session-store.db")
    if not os.path.isfile(path):
        return {}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            cols = {row[1] for row in con.execute("pragma table_info(sessions)")}
            if "id" not in cols:
                return {}
            wanted = [c for c in ("id", "cwd", "repository", "branch", "updated_at", "host_type") if c in cols]
            rows = con.execute(f"select {', '.join(wanted)} from sessions").fetchall()
            return {str(row[wanted.index("id")]): dict(zip(wanted, row)) for row in rows if row[0]}
        finally:
            con.close()
    except (OSError, sqlite3.Error):
        return {}


def _copilot_worker_index() -> Dict[str, Dict[str, Any]]:
    """Map Copilot session ids back to Subway worker records without updating worker state."""
    result: Dict[str, Dict[str, Any]] = {}
    paths = glob.glob(os.path.join(_s.WORKERS_DIR, "worker_*.json"))
    paths.extend(glob.glob(os.path.join(_s._archive_dir(), "worker_*.json")))
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                worker = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(worker, dict) or worker.get("provider") != "copilot":
            continue
        sid = worker.get("conversation") or worker.get("thread")
        if sid:
            result[str(sid)] = worker
    return result


def _copilot_open_sessions() -> Set[str]:
    path = os.path.join(_s.COPILOT_DIR, "open-sessions-state.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        return {str(sid) for sid, value in state.items()
                if isinstance(value, dict) and value.get("working")} if isinstance(state, dict) else set()
    except (OSError, ValueError):
        return set()


def _copilot_current_session() -> str:
    path = os.path.join(_s.COPILOT_DIR, "open-sessions-state.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return ""
    if not isinstance(state, dict):
        return ""
    active = [(str(value.get("refreshedAt") or value.get("openedAt") or ""), str(sid))
              for sid, value in state.items() if isinstance(value, dict) and value.get("working")]
    return max(active)[1] if active else ""


def _copilot_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _copilot_usage_checkpoint(data: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = data.get("accountingSnapshot") or {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    details = snapshot.get("tokenDetails") or {}
    if not isinstance(details, dict):
        details = {}
    model_metrics = snapshot.get("modelMetrics") or {}
    if not isinstance(model_metrics, dict):
        model_metrics = {}
    model = snapshot.get("currentModel") or data.get("lastActiveModel") or ""
    if not model and model_metrics:
        model = next(reversed(model_metrics), "")
    input_details = details.get("input") if isinstance(details.get("input"), dict) else {}
    output_details = details.get("output") if isinstance(details.get("output"), dict) else {}
    cache_details = details.get("cache_read") if isinstance(details.get("cache_read"), dict) else {}
    input_tokens = _s._copilot_int(input_details.get("tokenCount"))
    output_tokens = _s._copilot_int(output_details.get("tokenCount"))
    cache_read = _s._copilot_int(cache_details.get("tokenCount"))
    if not input_tokens and not output_tokens and model_metrics:
        model_info = next(iter(model_metrics.values()), {})
        metrics = model_info.get("usage") if isinstance(model_info, dict) else {}
        metrics = metrics if isinstance(metrics, dict) else {}
        input_tokens = _s._copilot_int(metrics.get("inputTokens"))
        output_tokens = _s._copilot_int(metrics.get("outputTokens"))
        cache_read = _s._copilot_int(metrics.get("cacheReadTokens"))
    return {
        "input": input_tokens, "output": output_tokens,
        "cache_read": cache_read, "total": input_tokens + output_tokens,
        "requests": _s._copilot_int(data.get("totalPremiumRequests")),
        "model": model,
    }


def _copilot_session_usage(path: str) -> Optional[Dict[str, Any]]:
    latest: Optional[Dict[str, Any]] = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "session.usage_checkpoint" not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") != "session.usage_checkpoint":
                    continue
                data = event.get("data") or {}
                if not isinstance(data, dict):
                    continue
                latest = _s._copilot_usage_checkpoint(data)
    except OSError:
        return None
    return latest


def _copilot_cli_meta(path: str, index: Dict[str, Dict[str, Any]],
                      worker_index: Dict[str, Dict[str, Any]],
                      open_sessions: Set[str]) -> Dict[str, Any]:
    folder_id = os.path.basename(os.path.dirname(path))
    sid = folder_id
    first_prompt = last_prompt = model = ""
    updated: Any = None
    project = ""
    usage: Optional[Dict[str, Any]] = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                typ = event.get("type")
                data = event.get("data") or {}
                if not isinstance(data, dict):
                    data = {}
                if typ == "session.start":
                    sid = str(data.get("sessionId") or sid)
                    model = data.get("selectedModel") or model
                    context = data.get("context") or {}
                    if isinstance(context, dict):
                        project = (context.get("cwd") or context.get("workingDirectory")
                                   or context.get("workspaceRoot") or project)
                elif typ == "result":
                    sid = str(event.get("sessionId") or sid)
                if typ == "user.message":
                    prompt = str(data.get("content") or data.get("transformedContent") or "").strip()
                    if prompt:
                        first_prompt = first_prompt or prompt
                        last_prompt = prompt
                elif typ == "assistant.message":
                    model = data.get("model") or model
                elif typ == "session.usage_checkpoint":
                    usage = _s._copilot_usage_checkpoint(data)
                ts = event.get("timestamp")
                if ts and (updated is None or _s.sort_key_ts(ts) > _s.sort_key_ts(updated)):
                    updated = ts
    except OSError:
        pass

    indexed = index.get(sid) or index.get(folder_id) or {}
    project = indexed.get("cwd") or project or indexed.get("repository") or ""
    if updated is None:
        try:
            updated = os.path.getmtime(path)
        except OSError:
            updated = None
    worker = worker_index.get(sid) or worker_index.get(folder_id)
    if usage and usage.get("model"):
        model = usage["model"]
    return {
        "source": "copilot", "id": sid, "path": path, "format": "copilot-cli",
        "title": _s.one_line(first_prompt, 80) if first_prompt else "(Copilot CLI session)",
        "first_prompt": first_prompt, "last_prompt": last_prompt,
        "project": project or "", "updated": updated, "model": model,
        "usage": usage, "tokens": usage.get("total") if usage else None,
        "open": sid in open_sessions or folder_id in open_sessions,
        "worker": bool(worker), "subagent": bool(worker),
        "parent": str((worker or {}).get("launched_by") or ""),
    }


def _copilot_agent_host_ids(user_dir: str) -> Set[str]:
    """Agent Host stores copilotcli session references, not chat transcript bodies."""
    path = os.path.join(user_dir, "globalStorage", "agent-host.db")
    if not os.path.isfile(path):
        return set()
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            ids: Set[str] = set()
            for row in con.execute("select session_uri from sessions where provider='copilotcli'"):
                uri = urllib.parse.urlparse(str(row[0] or ""))
                sid = uri.path.rstrip("/").rsplit("/", 1)[-1] or uri.netloc
                if sid:
                    ids.add(sid)
            return ids
        finally:
            con.close()
    except (OSError, sqlite3.Error):
        return set()


def copilot_list(include_subagents: bool = False) -> List[Dict[str, Any]]:
    session_root = os.path.join(_s.COPILOT_DIR, "session-state")
    index = _s._copilot_session_store()
    worker_index = _s._copilot_worker_index()
    open_sessions = _s._copilot_open_sessions()
    out: List[Dict[str, Any]] = []
    if os.path.isdir(session_root):
        for path in glob.glob(os.path.join(session_root, "*", "events.jsonl")):
            if not os.path.isfile(path):
                continue
            try:
                meta = _s._copilot_cli_meta(path, index, worker_index, open_sessions)
            except OSError as e:
                _s.log("copilot session metadata failed", path, e)
                continue
            if meta["subagent"] and not include_subagents:
                continue
            out.append(meta)

    agent_host_ids: Set[str] = set()
    vscode_seen: Set[str] = set()
    for user_dir in _s._vscode_user_dirs():
        agent_host_ids.update(_s._copilot_agent_host_ids(user_dir))
    for path, project, surface in _s._vscode_chat_files():
        try:
            meta = _s._vscode_chat_meta(path, project, surface, worker_index)
        except OSError as e:
            _s.log("VS Code Copilot metadata failed", path, e)
            continue
        if not meta or meta["id"] in vscode_seen:
            continue
        vscode_seen.add(meta["id"])
        if meta["subagent"] and not include_subagents:
            continue
        out.append(meta)

    for meta in out:
        if meta.get("format") == "copilot-cli" and meta["id"] in agent_host_ids:
            meta["surface"] = "VS Code Agent Host"
    return out


def _redact_secrets(val: Any) -> str:
    """Redact tokens, passwords, and API keys from display text."""
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, (dict, list)):
                val = parsed
        except Exception:
            pass
    if isinstance(val, dict):
        clean = {}
        for k, v in val.items():
            if re.search(r"(?i)token|key|secret|password|passwd|auth", str(k)):
                clean[k] = "[REDACTED]"
            elif isinstance(v, (dict, list)):
                clean[k] = json.loads(_s._redact_secrets(v))
            else:
                clean[k] = _s._SECRET_PATTERN.sub("[REDACTED]", str(v))
        return json.dumps(clean, ensure_ascii=False)
    if isinstance(val, list):
        clean_list = []
        for x in val:
            if isinstance(x, (dict, list)):
                clean_list.append(json.loads(_s._redact_secrets(x)))
            else:
                clean_list.append(_s._SECRET_PATTERN.sub("[REDACTED]", str(x)))
        return json.dumps(clean_list, ensure_ascii=False)
    s = str(val if val is not None else "")
    return _s._SECRET_PATTERN.sub("[REDACTED]", s)


def _last_tool_line(turns: List[Dict[str, Any]]) -> str:
    for turn in reversed(turns):
        name = turn.get("tool_name")
        if not name:
            continue
        raw_input = turn.get("tool_input")
        tool_input = _s.one_line(_s._redact_secrets(raw_input), 120) if raw_input not in (None, "") else "(no input)"
        timestamp = _s.fmt_ts(turn.get("ts")) or "time unavailable"
        return f"Last tool: {name}: {tool_input} at {timestamp}"
    return ""


def copilot_read_segments(chat: Dict[str, Any], mode: str = "full") -> Tuple[List[List[Dict[str, Any]]], bool]:
    if chat.get("format") == "vscode-chat":
        path = str(chat.get("path") or "")
        _state, chat_turns = _s._vscode_chat_data(path)
        transcript_path = _s._vscode_transcript_path(path, str(chat.get("id") or ""))
        if not os.path.isfile(transcript_path):
            return [chat_turns], False
        transcript_segments, has_markers = _s.copilot_read_segments(
            {"path": transcript_path, "format": "copilot-cli"}, mode
        )
        transcript_turns = [turn for segment in transcript_segments for turn in segment]
        chat_counts: Dict[Tuple[str, str], int] = {}
        for turn in chat_turns:
            key = (str(turn.get("role") or ""), str(turn.get("text") or ""))
            chat_counts[key] = chat_counts.get(key, 0) + 1
        additional_turns = []
        for turn in transcript_turns:
            role = str(turn.get("role") or "")
            if role in ("user", "assistant") and turn.get("text"):
                key = (role, str(turn.get("text") or ""))
                if chat_counts.get(key, 0):
                    chat_counts[key] -= 1
                    continue
            if turn.get("tool_name"):
                raw_input = turn.get("tool_input")
                display_input = _s.one_line(_s._redact_secrets(raw_input), 120) if raw_input not in (None, "") else "(no input)"
                turn["text"] = f"[tool {turn['tool_name']}] input: {display_input}"
            additional_turns.append(turn)
        if not additional_turns:
            return [chat_turns], has_markers
        merged = [*chat_turns, *additional_turns]
        merged.sort(key=lambda turn: _s._timestamp_seconds(turn.get("ts")) or 0)
        return [merged], has_markers

    mode = str(chat.get("mode") or mode or "full").lower()
    path = str(chat.get("path") or "")
    segs: List[List[Dict[str, Any]]] = [[]]
    has_markers = False
    pending_tools: Dict[str, Dict[str, Any]] = {}
    pending_list: List[Dict[str, Any]] = []

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                typ = str(event.get("type") or "").lower()
                data = event.get("data") or {}
                if not isinstance(data, dict):
                    data = {}

                # summary_text is left alone; it is not yet known whether it marks compaction.
                if typ == "summary_text":
                    continue

                if re.search(r"(?:compact|compaction|summari[sz]|truncat)", typ):
                    has_markers = True
                    summary = data.get("summary") or data.get("content")
                    if isinstance(summary, str) and summary.strip():
                        segs[-1].append({"role": "summary", "text": summary.strip(),
                                         "ts": event.get("timestamp")})
                    segs.append([])
                    continue

                ts = event.get("timestamp")
                if typ == "user.message":
                    text = str(data.get("content") or data.get("transformedContent") or "").strip()
                    if text:
                        segs[-1].append({"role": "user", "text": text, "ts": ts})
                elif typ == "assistant.message":
                    thought = str(data.get("reasoningText") or "").strip()
                    if thought:
                        segs[-1].append({"role": "assistant", "text": "", "thought": thought, "ts": ts})
                    text = str(data.get("content") or "").strip()
                    if text:
                        segs[-1].append({"role": "assistant", "text": text, "ts": ts})
                    for request in data.get("toolRequests") or []:
                        if isinstance(request, dict):
                            call_id = str(request.get("toolCallId") or request.get("callId") or request.get("id") or "")
                            name = request.get("name") or request.get("toolName") or "tool"
                            args = request.get("arguments") or request.get("input") or request.get("parameters") or ""
                            if mode == "decisions":
                                segs[-1].append({"role": "assistant", "text": "", "tool": f"[tool {name}]", "ts": ts})
                            else:
                                if isinstance(args, (dict, list)):
                                    args_str = json.dumps(args, ensure_ascii=False)
                                else:
                                    args_str = str(args) if args else ""
                                red_input = _s.one_line(_s._redact_secrets(args_str), 300)
                                inp_line = f" input: {red_input}" if red_input else ""
                                turn = {
                                    "role": "assistant", "text": f"[tool {name}]{inp_line}",
                                    "tool": f"[tool {name}]", "tool_name": str(name),
                                    "tool_input": args, "ts": ts,
                                }
                                segs[-1].append(turn)
                                if call_id:
                                    pending_tools[call_id] = turn
                                else:
                                    pending_list.append(turn)
                elif typ == "tool.execution_start":
                    call_id = str(data.get("toolCallId") or data.get("callId") or data.get("id") or "")
                    name = data.get("toolTitle") or data.get("toolName") or "tool"
                    args = data.get("arguments") or data.get("input") or data.get("parameters") or ""
                    if call_id and call_id in pending_tools:
                        continue
                    if mode == "decisions":
                        segs[-1].append({"role": "assistant", "text": "", "tool": f"[tool {name}]", "ts": ts})
                    else:
                        if isinstance(args, (dict, list)):
                            args_str = json.dumps(args, ensure_ascii=False)
                        else:
                            args_str = str(args) if args else ""
                        red_input = _s.one_line(_s._redact_secrets(args_str), 300)
                        inp_line = f" input: {red_input}" if red_input else ""
                        turn = {
                            "role": "assistant", "text": f"[tool {name}]{inp_line}",
                            "tool": f"[tool {name}]", "tool_name": str(name),
                            "tool_input": args, "ts": ts,
                        }
                        segs[-1].append(turn)
                        if call_id:
                            pending_tools[call_id] = turn
                        else:
                            pending_list.append(turn)
                elif typ == "tool.execution_complete":
                    call_id = str(data.get("toolCallId") or data.get("callId") or data.get("id") or "")
                    t_name = data.get("toolTitle") or data.get("toolName") or "tool"
                    success = data.get("success")
                    status = "failure" if (success is False or data.get("error") or str(data.get("status", "")).lower() in ("failed", "failure", "error")) else "success"
                    res = data.get("result") if data.get("result") is not None else (
                        data.get("output") if data.get("output") is not None else (
                            data.get("content") if data.get("content") is not None else (data.get("error") or "")
                        )
                    )
                    if isinstance(res, (dict, list)):
                        res_str = json.dumps(res, ensure_ascii=False)
                    else:
                        res_str = str(res) if res is not None else ""
                    red_res = _s.one_line(_s._redact_secrets(res_str), 300)
                    res_line = f"[{status}] {red_res}" if red_res else f"[{status}]"
                    if mode != "decisions":
                        matched_turn = None
                        if call_id and call_id in pending_tools:
                            matched_turn = pending_tools.pop(call_id)
                        elif pending_list:
                            matched_turn = pending_list.pop(0)
                        if matched_turn is not None:
                            matched_turn["text"] = f"{matched_turn['text']}\n{res_line}"
                        else:
                            segs[-1].append({"role": "assistant", "text": f"[tool {t_name}]\n{res_line}",
                                             "tool": f"[tool {t_name}]", "ts": ts})
                elif typ == "permission.requested":
                    asked = (data.get("message") or data.get("permission") or data.get("request") or
                             data.get("detail") or data.get("command") or data.get("tool") or data.get("title") or "")
                    if not asked and data:
                        asked = json.dumps(data, ensure_ascii=False)
                    asked_line = f"[permission requested] {_s.one_line(_s._redact_secrets(str(asked)), 200)}"
                    segs[-1].append({"role": "assistant", "text": asked_line, "ts": ts})
                elif typ == "permission.completed":
                    answer = (data.get("decision") or data.get("answer") or data.get("result") or
                              data.get("status") or data.get("action") or "")
                    if not answer and "allowed" in data:
                        answer = "allowed" if data["allowed"] else "denied"
                    if not answer and data:
                        answer = json.dumps(data, ensure_ascii=False)
                    answer_line = f"[permission completed] {_s.one_line(_s._redact_secrets(str(answer)), 200)}"
                    segs[-1].append({"role": "assistant", "text": answer_line, "ts": ts})
                elif typ.startswith("subagent."):
                    sub_id = data.get("subagentId") or data.get("id") or data.get("agentId") or ""
                    role = data.get("role") or data.get("name") or data.get("type") or "subagent"
                    desc = data.get("description") or data.get("prompt") or data.get("task") or ""
                    status = data.get("status") or data.get("result") or ""
                    if "start" in typ:
                        detail = f"{role}: {desc}" if desc else str(role)
                        if sub_id:
                            detail = f"[{sub_id}] {detail}"
                        line = f"[subagent start] {_s.one_line(_s._redact_secrets(detail), 200)}"
                    elif "finish" in typ or "complete" in typ or "end" in typ:
                        detail = f"{role} ({status})" if status else str(role)
                        if desc:
                            detail = f"{detail}: {desc}"
                        if sub_id:
                            detail = f"[{sub_id}] {detail}"
                        line = f"[subagent finish] {_s.one_line(_s._redact_secrets(detail), 200)}"
                    else:
                        line = f"[{typ}] {_s.one_line(_s._redact_secrets(json.dumps(data) if data else ''), 200)}"
                    segs[-1].append({"role": "assistant", "text": line, "ts": ts})
                elif typ == "result" and isinstance(event.get("result"), str):
                    text = event["result"].strip()
                    if text and not any(turn.get("role") == "assistant" and turn.get("text") == text
                                        for turn in segs[-1][-3:]):
                        segs[-1].append({"role": "assistant", "text": text, "ts": ts})
    except OSError:
        return [[]], False
    # Inspected Copilot CLI logs contain no compaction/summarization event; do not infer boundaries.
    return (segs if segs else [[]]), has_markers


def parse_copilot_jsonl(stream: Any) -> Dict[str, Any]:
    """Parse JSONL output lines from Copilot CLI, returning run state."""
    run: Dict[str, Any] = {}
    lines = stream.splitlines() if isinstance(stream, str) else stream
    for raw in lines:
        line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else str(raw).strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        _s._process_copilot_event(ev, run, lambda text: None)
    return run


@ttl_cached(120.0)
def _copilot_raw() -> Optional[Dict[str, Any]]:
    try:
        res = subprocess.run(
            ["gh", "api", "/copilot_internal/user", "-H", "Editor-Version: vscode/1.95.0", "-H", "Copilot-Integration-Id: vscode-chat"],
            capture_output=True, text=True, timeout=10
        )
        if res.returncode == 0 and res.stdout:
            return json.loads(res.stdout)
    except Exception:
        pass
    return None
