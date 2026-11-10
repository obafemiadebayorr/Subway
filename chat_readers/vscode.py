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

import server as _s
def _vscode_user_dirs() -> List[str]:
    """Return user-data folders for VS Code, Cursor, Devin, Windsurf, and Antigravity."""
    ide_names = [
        "Code", "Code - Insiders", "Cursor", "Devin", "Windsurf", "Antigravity"
    ]
    candidates = []
    for ide in ide_names:
        candidates.append(os.path.join(_s.HOME, "Library", "Application Support", ide, "User"))
        candidates.append(os.path.join(_s.HOME, ".config", ide, "User"))
    appdata = os.environ.get("APPDATA")
    if appdata:
        for ide in ide_names:
            candidates.append(os.path.join(appdata, ide, "User"))
    return list(dict.fromkeys(path for path in candidates if path and os.path.isdir(path)))


def _vscode_workspace_project(workspace_dir: str) -> str:
    path = os.path.join(workspace_dir, "workspace.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    candidates = [data.get("folder"), data.get("workspace")]
    folders = data.get("folders")
    if isinstance(folders, list):
        candidates.extend(x.get("uri") or x.get("path") for x in folders if isinstance(x, dict))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            parsed = urllib.parse.urlparse(candidate)
            return urllib.parse.unquote(parsed.path) if parsed.scheme == "file" else candidate
    return ""


def _vscode_chat_files() -> List[Tuple[str, str, str]]:
    """Return (transcript path, project path, surface) for old VS Code chat storage."""
    found: List[Tuple[str, str, str]] = []
    for user_dir in _s._vscode_user_dirs():
        storage = os.path.join(user_dir, "workspaceStorage")
        for path in glob.glob(os.path.join(storage, "*", "chatSessions", "*.jsonl")):
            workspace_dir = os.path.dirname(os.path.dirname(path))
            found.append((path, _s._vscode_workspace_project(workspace_dir), "VS Code Chat"))
        empty_root = os.path.join(user_dir, "globalStorage", "emptyWindowChatSessions")
        for path in glob.glob(os.path.join(empty_root, "*.jsonl")):
            found.append((path, "", "VS Code Chat (empty window)"))
    return found


def _vscode_patch_path(path: Any) -> List[Any]:
    if isinstance(path, list):
        return path
    if isinstance(path, str):
        return [part for part in path.split(".") if part]
    return []


def _vscode_set_path(root: Dict[str, Any], path: List[Any], value: Any) -> None:
    current: Any = root
    for index, part in enumerate(path[:-1]):
        next_part = path[index + 1]
        if isinstance(current, dict):
            key = str(part)
            if key not in current or not isinstance(current[key], (dict, list)):
                current[key] = [] if str(next_part).isdigit() else {}
            current = current[key]
        elif isinstance(current, list):
            try:
                position = int(part)
            except (TypeError, ValueError):
                return
            while len(current) <= position:
                current.append({})
            current = current[position]
        else:
            return
    if not path:
        return
    final = path[-1]
    if isinstance(current, dict):
        current[str(final)] = value
    elif isinstance(current, list):
        try:
            position = int(final)
        except (TypeError, ValueError):
            return
        while len(current) <= position:
            current.append(None)
        current[position] = value


def _vscode_chat_state(path: str) -> Dict[str, Any]:
    """Replay the VS Code kind-0 snapshot, kind-1 replacement, and kind-2 append records."""
    state: Dict[str, Any] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                value = record.get("v")
                if record.get("kind") == 0 and isinstance(value, dict):
                    state = value
                elif record.get("kind") == 1:
                    _s._vscode_set_path(state, _s._vscode_patch_path(record.get("k")), value)
                elif record.get("kind") == 2:
                    patch_path = _s._vscode_patch_path(record.get("k"))
                    if not patch_path:
                        continue
                    target: Any = state
                    for part in patch_path:
                        if isinstance(target, dict):
                            target = target.get(str(part))
                        elif isinstance(target, list):
                            try:
                                target = target[int(part)]
                            except (TypeError, ValueError, IndexError):
                                target = None
                        else:
                            target = None
                    if isinstance(target, list) and isinstance(value, list):
                        target.extend(value)
    except OSError:
        return {}
    return state


def _vscode_response_text(response: Any) -> str:
    if isinstance(response, str):
        return response.strip()
    parts: List[str] = []
    for item in response if isinstance(response, list) else []:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            for key in ("value", "text", "markdown", "content"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
                    break
    return "\n".join(parts).strip()


def _vscode_request_model(request: Dict[str, Any]) -> str:
    model = request.get("modelId") or request.get("model")
    metadata = request.get("metadata") or {}
    rounds = metadata.get("toolCallRounds") if isinstance(metadata, dict) else None
    if not model and isinstance(rounds, list):
        model = next((r.get("modelId") for r in reversed(rounds)
                      if isinstance(r, dict) and r.get("modelId")), "")
    if not model:
        state = request.get("modelState") or {}
        model = state.get("value") if isinstance(state, dict) else ""
    return str(model or "")


def _vscode_chat_data(path: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    state = _s._vscode_chat_state(path)
    turns: List[Dict[str, Any]] = []
    requests = state.get("requests") or []
    if not isinstance(requests, list):
        return state, turns
    for request in requests:
        if not isinstance(request, dict):
            continue
        message = request.get("message") or request.get("prompt") or ""
        if isinstance(message, dict):
            message = message.get("text") or message.get("message") or ""
        ts = request.get("timestamp") or request.get("creationDate") or state.get("creationDate")
        if isinstance(message, str) and message.strip():
            turns.append({"role": "user", "text": message.strip(), "ts": ts})
        response = _s._vscode_response_text(request.get("response"))
        if not response:
            result = request.get("result")
            response = _s._vscode_response_text(result.get("response")) if isinstance(result, dict) else ""
        if response:
            turns.append({"role": "assistant", "text": response, "ts": ts})
    return state, turns


def _vscode_transcript_path(path: str, session_id: str) -> str:
    workspace_dir = os.path.dirname(os.path.dirname(path))
    return os.path.join(workspace_dir, "GitHub.copilot-chat", "transcripts", f"{session_id}.jsonl")


def _timestamp_seconds(ts: Any) -> Optional[float]:
    if ts is None or ts == "":
        return None
    try:
        if isinstance(ts, (int, float)) or (isinstance(ts, str) and re.fullmatch(r"\d+(?:\.\d+)?", ts)):
            value = float(ts)
            return value / 1000.0 if value > 1e11 else value
        value = _s.sort_key_ts(ts)
        return value if value else None
    except (TypeError, ValueError, OverflowError):
        return None


def _vscode_transcript_latest_timestamp(path: str) -> Optional[float]:
    latest = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    ts = _s._timestamp_seconds(event.get("timestamp"))
                    if ts is not None and (latest is None or ts > latest):
                        latest = ts
    except OSError:
        return None
    return latest


def _vscode_chat_meta(path: str, project: str, surface: str,
                      worker_index: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    state, turns = _s._vscode_chat_data(path)
    if state.get("responderUsername") not in (None, "", "GitHub Copilot"):
        return None
    sid = str(state.get("sessionId") or os.path.basename(path)[:-6])
    user_turns = [turn["text"] for turn in turns if turn["role"] == "user"]
    requests = state.get("requests") or []
    input_tokens = output_tokens = 0
    models: List[str] = []
    for request in requests if isinstance(requests, list) else []:
        if not isinstance(request, dict):
            continue
        input_tokens += _s._copilot_int(request.get("promptTokens"))
        output_tokens += _s._copilot_int(request.get("completionTokens"))
        model = _s._vscode_request_model(request)
        if model:
            models.append(model)
    usage = {
        "input": input_tokens, "output": output_tokens, "total": input_tokens + output_tokens,
        "requests": len(requests) if isinstance(requests, list) else 0,
        "model": models[-1] if models else "",
    }
    worker = worker_index.get(sid)
    updated = state.get("completedAt") or state.get("creationDate") or os.path.getmtime(path)
    if isinstance(requests, list):
        active_times = [
            _s._timestamp_seconds(request.get("timestamp") or request.get("creationDate"))
            for request in requests if isinstance(request, dict)
        ]
    else:
        active_times = []
    transcript_path = _s._vscode_transcript_path(path, sid)
    transcript_event_time = _s._vscode_transcript_latest_timestamp(transcript_path)
    if transcript_event_time is not None:
        active_times.append(transcript_event_time)
    try:
        if os.path.isfile(transcript_path):
            active_times.append(os.path.getmtime(transcript_path))
    except OSError:
        pass
    active_times = [ts for ts in active_times if ts is not None]
    if active_times:
        updated = max(active_times)
    return {
        "source": "copilot", "id": sid, "path": path, "format": "vscode-chat",
        "title": state.get("customTitle") or (_s.one_line(user_turns[0], 80) if user_turns else "(Copilot Chat session)"),
        "first_prompt": user_turns[0] if user_turns else "",
        "last_prompt": user_turns[-1] if user_turns else "",
        "project": project, "updated": updated, "model": usage["model"],
        "usage": usage, "tokens": usage["total"], "open": False,
        "worker": bool(worker), "subagent": bool(worker),
        "parent": str((worker or {}).get("launched_by") or ""), "surface": surface,
    }
