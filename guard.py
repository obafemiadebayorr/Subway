"""Workspace guard (file leases/collision shield hook) and shared tracks plumbing.

Extracted from server.py. Functions that depend on server globals or late-bound
usage helpers read them at call time to preserve patch/test behavior.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, Optional

from cli_tools import (
    _broadcast_track_internal,
    _canon_path,
    _clean_locks,
    _clean_track,
    _file_sha,
    _git_head,
    _track_file,
    load_locks,
    read_track_entries,
    save_locks,
    t_broadcast_track,
    t_claim_file,
    t_join_track,
    t_list_locks,
    t_read_track,
    t_release_file,
    t_signoff_file,
)


def _writes_subway_state(command: str) -> bool:
    """Best effort: a shell can write a file in too many ways to catch them all, so this stops the plain ones."""
    # Match the state folder as a path (".../.subway-context"), not names like the "subway.subway-context" extension id.
    return bool(re.search(r">\s*\S*/\.subway-context\b|\btee\b.*/\.subway-context\b|"
                          r"\bsed\s+-i.*/\.subway-context\b|\b(mv|cp|rm)\b.*/\.subway-context\b", command))


DEFAULT_PRINCIPAL_MCP_READ_TOOLS = [
    "mcp__subway-context__read_symbol",
    "mcp__subway-context__context_slice",
    "mcp__subway-context__read_terminal",
    "mcp__subway-context__search_terminal",
    "mcp__subway-context__project_files",
]


def principal_mcp_read_tools(cfg):
    mcp_read_tools = (cfg or {}).get("principal_mcp_read_tools")
    if not isinstance(mcp_read_tools, list) or not all(isinstance(x, str) for x in mcp_read_tools):
        return list(DEFAULT_PRINCIPAL_MCP_READ_TOOLS)
    return mcp_read_tools


def _principal_settings():
    import server
    cfg = server._load_json("config.json") or {}
    enabled = bool(cfg.get("principal_guard_enabled", True))
    try: max_reads = int(cfg.get("principal_max_reads", 30))
    except Exception: max_reads = 30
    read_budget = cfg.get("read_budget") or {}
    if not isinstance(read_budget, dict): read_budget = {}
    read_budget.setdefault("reset", "compaction")
    read_budget.setdefault("scope", "all")
    read_budget.setdefault("per_provider", {})
    return enabled, max(0, max_reads), principal_mcp_read_tools(cfg), read_budget


def resolve_read_reset(settings, provider):
    scope = (settings or {}).get("scope", "all")
    if scope == "provider":
        per_provider = (settings or {}).get("per_provider") or {}
        prov_cfg = per_provider.get(provider) or {}
        if isinstance(prov_cfg, dict) and prov_cfg.get("reset"):
            return prov_cfg["reset"]
    return (settings or {}).get("reset", "compaction")

def _principal_reads_path():
    import server
    sdir = getattr(server, "SUBWAY_DIR", None) or os.path.expanduser("~/.subway-context")
    return os.path.join(sdir, "principal_reads.json")

def _bump_principal_reads(session_id):
    import json as _json
    p = _principal_reads_path(); data = {}
    try:
        if os.path.exists(p): data = _json.load(open(p, encoding="utf-8")) or {}
    except Exception: data = {}
    sessions = data.setdefault("sessions", {})
    n = int(sessions.get(session_id or "", 0)) + 1
    sessions[session_id or ""] = n
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"; _json.dump(data, open(tmp, "w", encoding="utf-8")); os.replace(tmp, p)
    except Exception: pass
    return n

def reset_principal_reads(session_id):
    import json as _json
    p = _principal_reads_path(); data = {}
    try:
        if os.path.exists(p): data = _json.load(open(p, encoding="utf-8")) or {}
    except Exception: data = {}
    sessions = data.get("sessions") or {}
    if session_id: sessions.pop(session_id, None)
    else: sessions.clear()
    data["sessions"] = sessions
    try: _json.dump(data, open(p, "w", encoding="utf-8"))
    except Exception: pass

def _is_mcp_read_tool(tool_name: str, tool_input: Dict[str, Any], mcp_read_tools: list) -> bool:
    if tool_name in mcp_read_tools:
        return True
    if tool_name == "call_mcp_tool":
        sub_tool = str(tool_input.get("ToolName") or "")
        srv_name = str(tool_input.get("ServerName") or "")
        if sub_tool in mcp_read_tools or f"mcp__{srv_name}__{sub_tool}" in mcp_read_tools:
            return True
        if any(m.endswith(f"__{sub_tool}") for m in mcp_read_tools):
            return True
        if any(m.replace("-", "_").endswith(f"_{sub_tool.replace('-', '_')}") for m in mcp_read_tools):
            return True
    if tool_name.startswith("mcp_") or tool_name.startswith("mcp__"):
        norm = tool_name.replace("-", "_")
        if any(m.replace("-", "_").replace("__", "_") == norm.replace("__", "_") for m in mcp_read_tools):
            return True
        sub = tool_name.split("_")[-1]
        if any(m.endswith(f"__{sub}") for m in mcp_read_tools):
            return True
    return False


def _count_user_turns(transcript_path: str) -> str:
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        count = 0
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as f:
            if size > 512 * 1024:
                f.seek(max(0, size - 512 * 1024))
                f.readline()
            for raw_line in f:
                if b'"user"' not in raw_line:
                    continue
                try:
                    obj = json.loads(raw_line.decode("utf-8", "replace"))
                    if obj.get("type") == "user":
                        msg = obj.get("message")
                        content = msg.get("content") if isinstance(msg, dict) else obj.get("content")
                        is_tool_res = False
                        if isinstance(content, list):
                            is_tool_res = any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content)
                        elif isinstance(content, dict):
                            is_tool_res = content.get("type") == "tool_result"
                        if not is_tool_res:
                            count += 1
                except Exception:
                    continue
        return str(count) if count > 0 else ""
    except Exception:
        return ""


def hook_guard(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Claude Code & Antigravity PreToolUse hook: deny an edit to a file another agent holds a lease on, or stop if chat budget reached."""
    import server
    from usage import chat_budget_for, chat_tokens_used

    is_antigravity = bool("toolCall" in event or "conversationId" in event or event.get("provider") == "antigravity")
    provider = str(event.get("provider") or ("antigravity" if is_antigravity else "claude"))

    def _deny(reason: str) -> Dict[str, Any]:
        if is_antigravity:
            return {"decision": "deny", "reason": reason}
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}

    def _allow() -> Optional[Dict[str, Any]]:
        if is_antigravity:
            return {"decision": "allow"}
        return None

    sid = str(event.get("session_id") or event.get("conversationId") or "")
    limit, scope = chat_budget_for(sid)
    if limit:
        used = chat_tokens_used(sid)
        if used is not None and used >= limit:
            label = "This chat's budget" if scope == "chat" else "The universal chat budget"
            return _deny(f"{label} is reached: {used:,} of {limit:,} tokens. Ask the user to raise it.")

    raw_tc = event.get("toolCall")
    tc: Dict[str, Any] = raw_tc if isinstance(raw_tc, dict) else {}
    tool_name = str(tc.get("name") or event.get("tool_name") or "")
    raw_args = tc.get("args")
    raw_ti = event.get("tool_input")
    tool_input: Dict[str, Any] = raw_args if isinstance(raw_args, dict) else (raw_ti if isinstance(raw_ti, dict) else {})

    ws_paths = event.get("workspacePaths")
    default_ws = ws_paths[0] if isinstance(ws_paths, list) and ws_paths else None
    cwd = os.path.abspath(str(event.get("cwd") or default_ws or os.getcwd()))

    principal = os.environ.get("SUBWAY_WORKER") != "1"
    if principal:
        enabled, max_reads, mcp_read_tools, read_budget = _principal_settings()
        if enabled:
            _fp = (tool_input.get("Path") or tool_input.get("path") or tool_input.get("file_path") or
                   tool_input.get("filepath") or tool_input.get("notebook_path"))
            if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit", "write_file", "edit_file") and isinstance(_fp, str) and _fp:
                cpath = _canon_path(_fp, cwd)
                sdir = os.path.abspath(os.path.expanduser(getattr(server, "SUBWAY_DIR", "") or ""))
                roots = [os.path.join(cwd, "scratchpad"), os.path.join(sdir, "worktrees"),
                         os.path.expanduser("~/.claude"), os.path.expanduser("~/.gemini"), "/tmp"]
                ar = [os.path.realpath(x) for x in roots if x]
                creal = os.path.realpath(cpath)
                in_allowed = any(creal == x or creal.startswith(x + os.sep) for x in ar)
                rcwd = os.path.realpath(cwd)
                in_project = creal == rcwd or creal.startswith(rcwd + os.sep)
                if in_project and not in_allowed:
                    return _deny("Principal hands off: your chat does not edit project files. Spin a worker (spin_worker) with the exact edit.")
            if (tool_name in ("Read", "Grep", "Glob", "Bash", "read_file", "run_command", "search_web")
                    or _is_mcp_read_tool(tool_name, tool_input, mcp_read_tools)):
                if max_reads > 0:
                    n = _bump_principal_reads(sid)
                    if n > max_reads:
                        reset_mode = resolve_read_reset(read_budget, provider)
                        tail = (" Resets at compaction, or run `subway-context read-budget reset`."
                                if reset_mode == "compaction" else " Run `subway-context read-budget reset`.")
                        return _deny(f"Read budget reached ({n} reads, limit {max_reads}). Delegate this: spin a worker with spin_worker, "
                                     "and use code= packets in the brief instead of pulling code into this chat." + tail)
            try:
                import governor
                turn_key = ""
                r = governor.check("claude", event.get("session_id", ""), event.get("tool_name", ""), event.get("tool_input"), turn_key=turn_key)
                if isinstance(r, dict) and not r.get("allow", True):
                    return _deny(r.get("reason", "Governor limit reached."))
            except Exception:
                pass

    cmd = tool_input.get("CommandLine") or tool_input.get("command")
    if cmd and _writes_subway_state(str(cmd)):
        return _deny("Budgets are the user's to change. Ask them to run it, or use the HUD.")
    if (tool_name in ("Bash", "run_command") or cmd is not None) and isinstance(cmd, str):
        if (re.search(r"(subway-context|server\.py)\s+(budget|compaction)\b", cmd)
                or any(k in cmd for k in ("/api/budget", "/api/chat_budget", "/api/settings", "X-Subway-Token"))):
            return _deny("Budgets are the user's to change. Ask them to run it, or use the HUD.")

    filepath = (tool_input.get("Path") or tool_input.get("path") or tool_input.get("file_path") or
                tool_input.get("filepath") or tool_input.get("notebook_path")) if isinstance(tool_input, dict) else None
    if filepath and isinstance(filepath, str):
        cpath = _canon_path(filepath, cwd)
        sdir = os.path.abspath(os.path.expanduser(getattr(server, "SUBWAY_DIR", "") or os.path.expanduser("~/.subway-context")))
        sreal = os.path.realpath(sdir)
        creal = os.path.realpath(cpath)
        wdir = os.path.join(sdir, "worktrees")
        wreal = os.path.join(sreal, "worktrees")
        in_worktrees = (cpath == wdir or cpath.startswith(wdir + os.sep)
                        or creal == wreal or creal.startswith(wreal + os.sep))
        if not in_worktrees and (cpath == sdir or cpath.startswith(sdir + os.sep)
                or creal == sreal or creal.startswith(sreal + os.sep)):
            return _deny("Subway's state files are the user's to change.")

    if not filepath or not isinstance(filepath, str):
        return _allow()
    cpath = _canon_path(filepath, cwd)
    locks_data = load_locks()["locks"]
    lease = locks_data.get(cpath)
    if lease and lease.get("holder_id") != sid and lease.get("holder") != sid:
        rem = max(0, int(lease.get("expires_at", 0) - time.time()))
        return _deny(f"Subway lease: {filepath} is held by {lease.get('holder')} for "
                     f"'{lease.get('purpose')}' ({rem}s left). Wait, or coordinate through send_note.")

    prefix = f"{cpath}::"
    sym_leases = {k: v for k, v in locks_data.items() if k.startswith(prefix)}
    if not lease and sym_leases:
        other_sym_leases = [
            v for v in sym_leases.values()
            if v.get("holder_id") != sid and v.get("holder") != sid
        ]
        if other_sym_leases:
            old_string = tool_input.get("old_string") or tool_input.get("Target") if isinstance(tool_input, dict) else None
            extract_fn = getattr(server, "_extract_code_item", None)
            if extract_fn is None:
                from worker_supervision.code_items import _extract_code_item as extract_fn
            for sl in other_sym_leases:
                sym = sl.get("symbol") or ""
                res = extract_fn(f"{filepath}:{sym}", cwd)
                if res is None:
                    res = extract_fn(f"{cpath}:{sym}", cwd)
                if res:
                    _, _, _, sym_content, _ = res
                    if old_string is None or old_string in sym_content:
                        holder = sl.get("holder", "another agent")
                        return _deny(f"Subway lease: symbol '{sym}' in '{filepath}' is held by {holder}. "
                                     f"Cannot modify this function.")

    if not lease and not sym_leases and tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit", "write_file", "edit_file"):
        t_claim_file(filepath=filepath, holder=sid, duration_seconds=300,
                     purpose="auto-claimed on first edit", project_dir=cwd)
    return _allow()


# --------------------------------------------------------------------------
# Shared Context Tracks (~/.subway-context/tracks/)
# --------------------------------------------------------------------------

_ALERTS_TRACK = "alerts"  # Reserved: the destructive-action audit log, not a conversation track.

DESTRUCTIVE_RE = re.compile("|".join([
    r"\brm\s+\S",
    r"\bsudo\s+\S",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+push\b[^\n]*(?:--force\b|--force-with-lease\b|\s-f\b)",
    r"\bgit\s+clean\s+-\S*[fdx]",
    r"\bdrop\s+(?:table|database|schema)\b",
    r"\btruncate\s+table\b",
    r"\bdelete\s+from\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
]), re.IGNORECASE)
