"""Permission modes, requests, approvals and protected-command policy.

Moved from server.py (Subway 1.3.5). Rule: server-level names are read late through `_s.NAME` so tests that patch `server.X` still apply. Import this module through server, not directly."""

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

_MODE_ALIASES = {"standard": "ask", "dangerously_skip": "auto_approve"}
import server as _s


def get_permission_mode(thread_id: str = "") -> str:
    """The effective mode for a thread (a chat id or worker id): its own override if set, else the
    universal default. Oversight unless the user chose otherwise."""
    thread_id = str(thread_id or "").strip()
    if thread_id:
        override = _s._load_json("thread_settings.json").get(thread_id)
        if isinstance(override, dict):
            mode = override.get("permission_mode")
            if mode in _s.PERMISSION_MODES:
                return mode
    mode = _s._load_json("config.json").get("permission_mode")
    return mode if mode in _s.PERMISSION_MODES else "ask"


def set_permission_mode(mode: str, thread_id: str = "") -> str:
    """Only the HUD and terminal call this: agents can't loosen oversight for themselves.

    With thread_id, sets (or with an empty mode, clears) that chat's or worker's own override
    instead of the universal default. A thread override always wins over the universal mode
    (see get_permission_mode)."""
    thread_id = str(thread_id or "").strip()
    if thread_id:
        mode = _s._MODE_ALIASES.get(mode, mode) if mode else ""
        if mode and mode not in _s.PERMISSION_MODES:
            return f"Error: mode must be one of {', '.join(_s.PERMISSION_MODES)}."
        with _s._state_lock("thread_settings"):
            data = _s._load_json("thread_settings.json")
            entry = data.get(thread_id)
            entry = dict(entry) if isinstance(entry, dict) else {}
            if mode:
                entry["permission_mode"] = mode
            else:
                entry.pop("permission_mode", None)
            if entry:
                data[thread_id] = entry
            else:
                data.pop(thread_id, None)
            _s._save_json("thread_settings.json", data)
        if mode:
            return f"[PERMISSION MODE] {thread_id}: {_s.PERMISSION_MODES[mode]} (overrides the universal mode)."
        return f"[PERMISSION MODE] {thread_id}: reset to the universal mode."
    mode = _s._MODE_ALIASES.get(mode, mode)
    if mode not in _s.PERMISSION_MODES:
        return f"Error: mode must be one of {', '.join(_s.PERMISSION_MODES)}."
    with _s._state_lock("config"):
        data = _s._load_json("config.json")
        data["permission_mode"] = mode
        _s._save_json("config.json", data)
    running = [w for w in _s._all_workers() if w.get("status") in _s.ACTIVE_WORKER_STATES]
    return f"[PERMISSION MODE] {_s.PERMISSION_MODES[mode]}. Applied to {len(running)} running workers."


def get_terminal_mode(thread_id: str = "") -> str:
    """How run_terminal treats requests from a thread: its own terminal override if set, else
    the universal terminal mode. Independent of the worker permission mode: terminal commands
    keep asking unless the user chose a terminal mode on purpose."""
    thread_id = str(thread_id or "").strip()
    if thread_id:
        override = _s._load_json("thread_settings.json").get(thread_id)
        if isinstance(override, dict):
            mode = override.get("terminal_mode")
            if mode in _s.PERMISSION_MODES:
                return mode
    mode = _s._load_json("config.json").get("terminal_mode")
    return mode if mode in _s.PERMISSION_MODES else "ask"


def set_terminal_mode(mode: str, thread_id: str = "") -> str:
    """Only the HUD and terminal call this. With thread_id, sets (or with an empty mode,
    clears) that chat's or worker's terminal override; otherwise the universal terminal mode.
    Merges into the thread's other settings instead of replacing them."""
    thread_id = str(thread_id or "").strip()
    if thread_id:
        mode = _s._MODE_ALIASES.get(mode, mode) if mode else ""
        if mode and mode not in _s.PERMISSION_MODES:
            return f"Error: mode must be one of {', '.join(_s.PERMISSION_MODES)}."
        with _s._state_lock("thread_settings"):
            data = _s._load_json("thread_settings.json")
            entry = data.get(thread_id)
            entry = dict(entry) if isinstance(entry, dict) else {}
            if mode:
                entry["terminal_mode"] = mode
            else:
                entry.pop("terminal_mode", None)
            if entry:
                data[thread_id] = entry
            else:
                data.pop(thread_id, None)
            _s._save_json("thread_settings.json", data)
        if mode:
            return (f"[TERMINAL MODE] {thread_id}: {_s.PERMISSION_MODES[mode]} for terminal commands "
                    "(overrides the universal mode).")
        return f"[TERMINAL MODE] {thread_id}: reset to the universal mode."
    mode = _s._MODE_ALIASES.get(mode, mode)
    if mode not in _s.PERMISSION_MODES:
        return f"Error: mode must be one of {', '.join(_s.PERMISSION_MODES)}."
    with _s._state_lock("config"):
        data = _s._load_json("config.json")
        data["terminal_mode"] = mode
        _s._save_json("config.json", data)
    return f"[TERMINAL MODE] {_s.PERMISSION_MODES[mode]} for terminal commands."


def get_protected_commands() -> List[str]:
    """The protected-command list from config.json, or the built-in defaults when unset."""
    value = _s._load_json("config.json").get("protected_commands")
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return list(terminals.PROTECTED_DEFAULTS)


def get_protected_exceptions() -> List[str]:
    """The protected-command exceptions from config.json, or the built-in defaults when unset."""
    value = _s._load_json("config.json").get("protected_exceptions")
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return list(terminals.PROTECTED_EXCEPTIONS_DEFAULTS)


def thread_permission_rows() -> Dict[str, Any]:
    """One row per chat or worker with its own permission-mode override, for the HUD."""
    overrides = _s._load_json("thread_settings.json")
    if not overrides:
        return {"threads": []}
    universal = _s.get_permission_mode()
    rows = []
    for thread_id, entry in overrides.items():
        mode = (entry or {}).get("permission_mode") if isinstance(entry, dict) else None
        if mode not in _s.PERMISSION_MODES:
            continue
        worker = _s._load_worker(thread_id) if _s.WORKER_ID_RE.match(thread_id) else None
        label = (worker.get("role") if worker else None) or _s._short_id(thread_id)
        rows.append({
            "thread_id": thread_id,
            "label": label,
            "permission_mode": mode,
            "universal": universal,
            "is_worker": bool(worker),
        })
    rows.sort(key=lambda r: r["label"])
    return {"threads": rows}


def approval_code(worker_id: str) -> str:
    """Six-character code shown only on user surfaces. Telling it to an agent is the approval."""
    return hmac.new(_s.hud_token().encode(), worker_id.encode(), hashlib.sha256).hexdigest()[:6].upper()


def _perm_dir() -> str:
    path = os.path.join(_s.SUBWAY_DIR, "permissions")
    os.makedirs(path, exist_ok=True)
    return path


def _perm_file(request_id: str) -> str:
    if not re.match(r"^perm_[0-9a-f]{8}$", request_id or ""):
        raise ValueError(f"invalid permission request '{request_id}'")
    return os.path.join(_s._perm_dir(), f"{request_id}.json")


def _pending_permissions(worker_id: str = "", station: str = "") -> List[Dict[str, Any]]:
    out = []
    for fp in glob.glob(os.path.join(_s._perm_dir(), "perm_*.json")):
        with contextlib.suppress(OSError, ValueError):
            with open(fp, "r", encoding="utf-8") as f:
                r = json.load(f)
            if r.get("status") == "pending" and (not worker_id or r.get("worker_id") == worker_id) \
                    and (not station or r.get("launched_by") == station):
                out.append(r)
    return sorted(out, key=lambda r: r.get("created_at", 0))


def _read_target(detail: Any, locations: Optional[List[str]] = None) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    """Parse target file path and optional line range (start, end) from tool inputs.

    Returns (path, start, end) where start/end None means a whole-file read.
    """
    raw: Any = detail
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                raw = parsed
        except ValueError:
            pass

    path: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None

    if isinstance(raw, dict):
        path_keys = {"path", "filepath", "targetfile", "filename", "file"}
        for key, value in raw.items():
            normalized = re.sub(r"[\s_-]+", "", str(key).lower())
            if normalized in path_keys and isinstance(value, str) and value:
                path = value
                break

        offset_val: Optional[int] = None
        limit_val: Optional[int] = None

        for key, value in raw.items():
            normalized = re.sub(r"[\s_-]+", "", str(key).lower())
            if normalized == "startline" and value is not None and value != "":
                try:
                    start = int(value)
                except (TypeError, ValueError):
                    pass
            elif normalized == "endline" and value is not None and value != "":
                try:
                    end = int(value)
                except (TypeError, ValueError):
                    pass
            elif normalized in ("linerange", "lines", "range") and value is not None and value != "":
                if isinstance(value, (list, tuple)) and len(value) >= 2:
                    try:
                        start = int(value[0])
                        end = int(value[1])
                    except (TypeError, ValueError):
                        pass
                elif isinstance(value, str) and value.strip():
                    m = re.match(r"^\s*(\d+)\s*[-:]\s*(\d+)\s*$", value.strip())
                    if m:
                        start = int(m.group(1))
                        end = int(m.group(2))
            elif normalized == "offset" and value is not None and value != "":
                try:
                    offset_val = int(value)
                except (TypeError, ValueError):
                    pass
            elif normalized == "limit" and value is not None and value != "":
                try:
                    limit_val = int(value)
                except (TypeError, ValueError):
                    pass

        if start is None and end is None:
            if offset_val is not None:
                start = offset_val
                if limit_val is not None:
                    end = start + limit_val - 1
            elif limit_val is not None:
                start = 1
                end = limit_val
        elif start is not None and end is None and limit_val is not None:
            end = start + limit_val - 1
        elif start is None and end is not None:
            start = 1
    elif isinstance(raw, str):
        path = raw.strip()

    if not path and locations:
        path = next((p for p in locations if p), None)

    return (path, start, end)


def _large_file_read_denial(meta: Dict[str, Any], tool: str, detail: Any, kind: str = "",
                            locations: Optional[List[str]] = None) -> Optional[str]:
    """Block identifiable whole-file reads of large files inside a worker's workspace."""
    if str(meta.get("provider") or "").lower() not in ("antigravity", "opencode", "local"):
        return None
    config = _s._load_json("config.json")
    if str(config.get("large_file_reads") or "").lower() == "allow":
        return None
    tool_name = re.sub(r"[\s_-]+", "", str(tool or "").lower())
    kind_name = str(kind or "").lower()
    if tool_name not in ("read", "readfile", "readtextfile") and kind_name != "read":
        return None

    path, start, _end = _read_target(detail, locations)
    ranged = start is not None
    if ranged:
        return None
    if not path:
        return None

    workspace = str(meta.get("worktree") or meta.get("project_dir") or "")
    if not workspace:
        return None
    root = os.path.realpath(os.path.expanduser(workspace))
    resolved = os.path.realpath(os.path.expanduser(path if os.path.isabs(path) else os.path.join(root, path)))
    try:
        if os.path.commonpath((root, resolved)) != root or not os.path.isfile(resolved):
            return None
    except (OSError, ValueError):
        return None

    threshold = config.get("large_file_read_lines", 1500)
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
        threshold = 1500
    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            line_count = sum(1 for _ in f)
    except OSError:
        return None
    if line_count <= threshold:
        return None
    return f"{path} has {line_count} lines. Grep for what you need, then read a line range."


_TERMINAL_WRITE_DENY = ("read-only worker: shell command may write files (missing files:write capability)")


def _terminal_command(detail: Any) -> Optional[str]:
    if isinstance(detail, dict):
        for key in ("command", "cmd", "input", "CommandLine", "commandLine", "command_line"):
            val = detail.get(key)
            if isinstance(val, str) and val.strip():
                return val
        for key in ("file_path", "path"):
            val = detail.get(key)
            if isinstance(val, str) and val.strip():
                return None
        raw = detail.get("raw") or detail.get("detail")
        if isinstance(raw, str) and raw.strip():
            return raw
        return json.dumps(detail, ensure_ascii=False)
    if isinstance(detail, str):
        return detail
    if detail is None:
        return None
    return str(detail)


def _command_writes(command: str) -> bool:
    cmd = command or ""
    stripped = re.sub(r"'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\"", "''", cmd)
    # Redirects > >> except to /dev/null (handles 2>/dev/null, &>/dev/null, 1>>/dev/null).
    if re.search(r"(?<!<)(?:&|\d*)(?:>>|>(?!>))\s*(?!&?\d+\b)(?!/dev/null\b)\S", stripped):
        return True
    if re.search(r"\btee\b", stripped) and not re.search(r"\btee\b[^;&|]*\s+/dev/null\b", stripped):
        return True
    if re.search(r"\bsed\b[^;&|]*\s-(?=[a-zA-Z]*i)[a-zA-Z]*i", stripped):
        return True
    if re.search(r"\bperl\b[^;&|]*\s-(?=[a-zA-Z]*i)[a-zA-Z]*i", stripped):
        return True
    if re.search(r"\b(?:mv|cp|rm|touch|mkdir)\b", stripped):
        return True
    if re.search(r"\bgit\b[^;&|]*\b(?:apply|checkout|reset|commit|stash)\b", stripped):
        return True
    # Check original (not stripped): inline code strings contain open/write tokens.
    if re.search(r"\b(?:python[23]?|python|node)\b[^;&|]*\s-(?:c|e)\b[^;&|]*\b(?:open\s*\(|write)", cmd):
        return True
    return False


def _readonly_terminal_denial(meta: Dict[str, Any], tool: str, detail: Any, kind: str = "",
                              locations: Optional[List[str]] = None) -> Optional[str]:
    """Deny shell commands that write when a worker lacks files:write."""
    caps = meta.get("capabilities") or []
    if not isinstance(caps, (list, tuple, set)):
        caps = []
    if "files:write" in caps or meta.get("mode") == "god":
        return None
    tool_name = re.sub(r"[\s_-]+", "", str(tool or "").lower())
    kind_name = str(kind or "").lower()
    terminal_tools = {
        "bash", "shell", "terminal", "runterminal", "exec", "execute",
        "command", "runcommand", "execcommand", "shellcommand",
        "run_in_terminal", "runinterminal",
    }
    if tool_name not in terminal_tools and kind_name not in ("terminal", "bash", "shell", "command"):
        return None
    command = _terminal_command(detail)
    if command is None:
        return None
    if _command_writes(command):
        return f"{_TERMINAL_WRITE_DENY}: {_s.one_line(command, 180)}"
    return None


def _packet_reread_denial(meta: Dict[str, Any], tool: str, detail: Any, kind: str = "",
                          locations: Optional[List[str]] = None) -> Optional[str]:
    """Block re-reading code already provided in the worker's brief code packet."""
    config = _s._load_json("config.json")
    if str(config.get("packet_rereads") or "").lower() == "allow":
        return None

    tool_name = re.sub(r"[\s_-]+", "", str(tool or "").lower())
    kind_name = str(kind or "").lower()
    if tool_name not in ("read", "readfile", "readtextfile") and kind_name != "read":
        return None

    path, start, end = _read_target(detail, locations)
    if not path:
        return None

    workspace = str(meta.get("worktree") or meta.get("project_dir") or "")
    if not workspace:
        return None
    root = os.path.realpath(os.path.expanduser(workspace))
    resolved = os.path.realpath(os.path.expanduser(path if os.path.isabs(path) else os.path.join(root, path)))
    try:
        rel = os.path.relpath(resolved, root)
    except (OSError, ValueError):
        rel = path

    code_ranges = meta.get("code_ranges")
    if not isinstance(code_ranges, dict):
        return None
    ranges = code_ranges.get(resolved)
    if not ranges:
        for k, v in code_ranges.items():
            if os.path.realpath(k) == resolved:
                ranges = v
                break
    if not ranges:
        return None

    if start is None and end is None:
        s, e = ranges[0][0], ranges[0][1]
        return f'Denied: lines {s}-{e} of {rel} are already in your brief under "=== CODE ===". Use that copy; do not re-read it.'

    req_start = start if start is not None else 1
    req_end = end if end is not None else req_start
    if req_start > req_end:
        req_start, req_end = req_end, req_start
    req_len = max(1, req_end - req_start + 1)

    for r in ranges:
        pkt_s, pkt_e = r[0], r[1]
        overlap = max(0, min(req_end, pkt_e) - max(req_start, pkt_s) + 1)
        if overlap >= 0.8 * req_len:
            s, e = pkt_s, pkt_e
            return f'Denied: lines {s}-{e} of {rel} are already in your brief under "=== CODE ===". Use that copy; do not re-read it.'

    return None


def request_permission(meta: Dict[str, Any], tool: str, detail: str, rules: Optional[List[str]] = None,
                       raw_input: Any = None) -> str:
    """Queue a question for the user, and tell the window that launched this worker."""
    rid = f"perm_{uuid.uuid4().hex[:8]}"
    denial = (_readonly_terminal_denial(meta, tool, raw_input or detail)
              or _s._packet_reread_denial(meta, tool, raw_input or detail)
              or _s._large_file_read_denial(meta, tool, raw_input or detail))
    rec = {"id": rid, "worker_id": meta["id"], "role": meta.get("role"), "provider": meta.get("provider"),
           "tool": tool, "detail": detail, "rules": rules or [], "status": "pending",
           "launched_by": meta.get("launched_by"), "created_at": time.time()}
    _s._write_json_atomic(_s._perm_file(rid), rec)
    _s._thread_record(meta.get("thread_root") or _s._thread_root_for_caller(meta["id"]), "permission_asked", meta["id"],
                   data={"request_id": rid, "tool": tool, "detail": _s.one_line(str(detail or ""), 300)})
    if denial:
        _s._answer_permission(rid, "deny", "Subway large-file read guard", denial_reason=denial)
        return rid
    # Roadmap 4a: publish/delete must ask in EVERY mode. Auto-approval is about convenience
    # for ordinary work; it must never approve a publish or a delete, because those actions
    # outside git cannot be undone by a patch.
    publish_delete = _s.classify_mcp_tool(tool) == "publish_delete"

    if meta.get("skip_permissions") and not publish_delete:
        _s._answer_permission(rid, "allow", "your auto-approve setting")
        return rid

    wid = str(meta.get("id") or "")
    parent_id = str(meta.get("parent_id") or "")
    thread_mode = ""
    if wid:
        t_override = _s._load_json("thread_settings.json").get(wid)
        if isinstance(t_override, dict) and t_override.get("permission_mode") in _s.PERMISSION_MODES:
            thread_mode = t_override["permission_mode"]
    cfg_mode = _s._load_json("config.json").get("permission_mode")
    if thread_mode:
        eff_mode = thread_mode
    elif cfg_mode in _s.PERMISSION_MODES:
        eff_mode = cfg_mode
    else:
        eff_mode = meta.get("permission_mode") or "ask"

    if parent_id:
        parent_mode = get_permission_mode(parent_id)
        if _s._PERMISSION_RANK.get(eff_mode, 0) > _s._PERMISSION_RANK.get(parent_mode, 0):
            eff_mode = parent_mode

    if tool != "run_terminal" and eff_mode == "auto_approve" and not publish_delete:
        # The user chose to auto-approve everything: keep the record, but never ask or alert them.
        _s._answer_permission(rid, "allow", "your auto-approve setting")
        return rid
    launcher = str(meta.get("launched_by") or "")
    if re.match(r"^[0-9a-f]{8}-", launcher) or launcher.startswith("ses_"):
        with contextlib.suppress(OSError):
            with _s._state_lock("inbox"):
                with open(os.path.join(_s.SUBWAY_DIR, "inbox", f"{launcher}.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps({"ts": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                                        "from": meta["id"], "shared_chat": None,
                                        "text": f"Permission needed ({rid}): {tool}: {detail}"}) + "\n")
    _s._broadcast_track_internal(f"[PERMISSION REQUEST] `{meta['id']}` ({meta.get('role')}) asks to use {tool}: {_s.one_line(detail, 200)}",
                              track=meta.get("track", "main"), station_id=meta["id"])
    _s.notify_if_still_waiting(f"permission_request|{meta['id']}|{rid}", f"Permission needed ({meta['id']}): {tool}",
                            lambda: rid in {r["id"] for r in _s._pending_permissions()}, route="workers")
    return rid


def _answer_permission(rid: str, decision: str, answered_by: str, denial_reason: str = "") -> str:
    if decision not in ("allow", "allow_always", "deny"):
        return "Error: decision must be 'allow', 'allow_always', or 'deny'."
    with _s._state_lock("permissions"):
        try:
            with open(_s._perm_file(rid), "r", encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            return f"Error: permission request `{rid}` not found."
        if rec.get("status") != "pending":
            return f"Permission request `{rid}` was already answered ({rec.get('decision')})."
        rec.update({"status": "answered", "decision": decision, "answered_by": answered_by, "answered_at": time.time()})
        if denial_reason:
            rec["denial_reason"] = denial_reason
        _s._write_json_atomic(_s._perm_file(rid), rec)
    _perm_node = str(rec.get("worker_id") or rec.get("launched_by") or "")
    _s._thread_record(_s._thread_root_for_caller(_perm_node), "permission_answered", _perm_node, data={
        "request_id": rid, "decision": decision, "answered_by": answered_by})
    _s._broadcast_track_internal(f"[PERMISSION {decision.upper()}] `{rec['worker_id']}`: {rec['tool']}: {_s.one_line(rec['detail'], 160)} "
                              f"(by {answered_by})", track="main", station_id="guard")
    return f"[{decision.upper()}] `{rid}`: {rec['tool']}: {_s.one_line(rec['detail'], 160)}"


def permission_text(requests: List[Dict[str, Any]]) -> str:
    lines = [f"[PERMISSION NEEDED] {len(requests)} request(s) from your workers. Show each to your user and ask them. "
             f"Then call answer_permission(request_id, decision) with allow, allow_always, or deny. Don't decide for them."]
    for r in requests:
        lines.append(f"- `{r['id']}` worker `{r['worker_id']}` ({r.get('role')}, {str(r.get('provider')).upper()}) "
                     f"wants {r['tool']}: {r['detail']}")
    return "\n".join(lines)


def wait_for_activity(timeout: float = 25.0) -> None:
    """Block until a worker or permission file changes. The OS wakes this, so nothing is polled on macOS and BSD."""
    paths = [_s._perm_dir(), _s.WORKERS_DIR]
    if not hasattr(select, "kqueue"):
        before = [os.stat(p).st_mtime_ns for p in paths]
        deadline = time.time() + timeout
        while time.time() < deadline and [os.stat(p).st_mtime_ns for p in paths] == before:
            time.sleep(0.05)
        return
    fds = [os.open(p, os.O_RDONLY) for p in paths]
    kq = select.kqueue()
    try:
        kq.control([select.kevent(fd, select.KQ_FILTER_VNODE, select.KQ_EV_ADD | select.KQ_EV_CLEAR, select.KQ_NOTE_WRITE)
                    for fd in fds], 2, timeout)
    finally:
        kq.close()
        for fd in fds:
            os.close(fd)


def run_permit_server(worker_id: str) -> int:
    """MCP server handed to a Claude worker as --permission-prompt-tool. It asks the user and waits."""
    meta = _s._load_worker(worker_id) or {"id": worker_id}
    always: Set[str] = set()
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        rid, method = req.get("id"), req.get("method")
        if rid is None:
            continue
        if method == "initialize":
            result: Dict[str, Any] = {"protocolVersion": (req.get("params") or {}).get("protocolVersion") or "2025-06-18",
                                      "capabilities": {"tools": {}},
                                      "serverInfo": {"name": "subway_permit", "version": _s.VERSION}}
        elif method == "tools/list":
            result = {"tools": [{"name": "request", "description": "Ask the user to allow a tool call.",
                                 "inputSchema": {"type": "object", "properties": {
                                     "tool_name": {"type": "string"}, "input": {"type": "object"}}}}]}
        elif method == "tools/call":
            args = (req.get("params") or {}).get("arguments") or {}
            tool, tool_input = str(args.get("tool_name")), args.get("input") or {}
            if tool in always:
                decision = "allow"
            else:
                detail = tool_input.get("command") or tool_input.get("file_path") or json.dumps(tool_input)[:300]
                decision = _s.await_permission(_s.request_permission(meta, tool, str(detail)))
                if decision == "allow_always":
                    always.add(tool)
            answer = ({"behavior": "allow", "updatedInput": tool_input} if decision != "deny"
                      else {"behavior": "deny", "message": "The user denied this."})
            result = {"content": [{"type": "text", "text": json.dumps(answer)}]}
        else:
            result = {}
        _s.send({"jsonrpc": "2.0", "id": rid, "result": result})
    return 0


def _handle_elicitation_reply(msg: Dict[str, Any]) -> None:
    rid = str(msg.get("id"))[len("elicit-"):]
    _s._closed.add(rid)
    result = msg.get("result") or {}
    decision = (result.get("content") or {}).get("decision")
    if result.get("action") == "accept" and decision in ("allow", "allow_always", "deny"):
        _s._answer_permission(rid, str(decision), "chat")


def run_hook_guard() -> int:
    """Claude Code hook entry. Fails open: a Subway bug must never block someone's editing."""
    try:
        decision = _s.hook_guard(json.load(sys.stdin))
    except Exception as e:
        _s.log("hook guard error:", e)
        return 0
    if decision:
        print(json.dumps(decision))
    return 0
