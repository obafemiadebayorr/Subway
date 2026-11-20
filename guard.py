"""Workspace guard (file leases/collision shield hook) and shared tracks plumbing.

Extracted from server.py. Functions that depend on server globals or late-bound
usage helpers read them at call time to preserve patch/test behavior.
"""
from __future__ import annotations

import hashlib
import json
import os
import subway_home
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
    # Match the state folder as a path (".../.subway", or the old ".../.subway-context"), not names like the "subway.subway-context" extension id.
    return bool(re.search(r">\s*\S*[/\\]\.subway(?:-context)?\b|\btee\b.*[/\\]\.subway(?:-context)?\b|"
                          r"\bsed\s+-i.*[/\\]\.subway(?:-context)?\b|\b(mv|cp|rm|del|copy|move|xcopy|robocopy)\b.*[/\\]\.subway(?:-context)?\b", command))


DEFAULT_PRINCIPAL_MCP_READ_TOOLS = [
    "mcp__subway__read_symbol",
    "mcp__subway-context__read_symbol",
    "mcp__subway__context_slice",
    "mcp__subway-context__context_slice",
    "mcp__subway__read_terminal",
    "mcp__subway-context__read_terminal",
    "mcp__subway__search_terminal",
    "mcp__subway-context__search_terminal",
    "mcp__subway__project_files",
    "mcp__subway-context__project_files",
]


def principal_mcp_read_tools(cfg):
    mcp_read_tools = (cfg or {}).get("principal_mcp_read_tools")
    if not isinstance(mcp_read_tools, list) or not all(isinstance(x, str) for x in mcp_read_tools):
        return list(DEFAULT_PRINCIPAL_MCP_READ_TOOLS)
    # The MCP server was renamed from subway-context to subway: honor both spellings.
    renamed = [t.replace("mcp__subway-context__", "mcp__subway__") for t in mcp_read_tools]
    return list(dict.fromkeys(mcp_read_tools + renamed))


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


def _principal_small_edit_lines() -> int:
    import server
    cfg = server._load_json("config.json") or {}
    try:
        return max(0, int(cfg.get("principal_small_edit_lines", 0) or 0))
    except Exception:
        return 0


def _read_budget_warn_pct() -> int:
    """Return configured soft-warning percentage (50..100, default 80) for principal read budget."""
    import server
    cfg = server._load_json("config.json") or {}
    try:
        val = int(cfg.get("read_budget_warn_pct", 80))
    except Exception:
        return 80
    return max(50, min(100, val))


_EXEMPT_READ_BUDGET_PREFIXES = (
    "git status",
    "git diff",
    "git apply",
    "git add",
    "git commit",
    "git merge",
    "git cherry-pick",
    "git fetch",
    "git show",
    "pytest",
    "python3 -m pytest",
    "ruff check",
    "node --test",
    "npm test",
)


def _is_exempt_read_budget_cmd(cmd: str) -> bool:
    """Return True if cmd starts with (or only chains via &&) safe landing/verification commands."""
    if not isinstance(cmd, str):
        return False
    s = cmd.strip()
    if not s:
        return False
    if any(tok in s for tok in (";", "||", "|", "\n", "\r", "`", "$(")):
        return False
    if re.search(r"(?<!&)&(?!&)", s):
        return False
    parts = [p.strip() for p in s.split("&&")]
    if not parts or any(not p for p in parts):
        return False
    for part in parts:
        if not any(
            part == pref or part.startswith(pref + " ") or part.startswith(pref + "\t")
            for pref in _EXEMPT_READ_BUDGET_PREFIXES
        ):
            return False
    return True


def _edit_line_count(tool_name: str, tool_input: Dict[str, Any]) -> int:
    """Lines an Edit/MultiEdit actually changes (context lines excluded); -1 when not measurable."""
    import difflib

    def one(e: Dict[str, Any]) -> int:
        if e.get("replace_all"):
            return -1
        # B1: pi's edit shape is oldText/newText; claude's is old_string/new_string.
        a = str(e.get("old_string") or e.get("oldText") or "").splitlines()
        b = str(e.get("new_string") or e.get("newText") or "").splitlines()
        n = 0
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if tag != "equal":
                n += max(i2 - i1, j2 - j1)
        return n

    if tool_name in ("Edit", "edit_file", "edit") and ("old_string" in tool_input or "oldText" in tool_input):
        return one(tool_input)
    if tool_name in ("MultiEdit", "edit") and isinstance(tool_input.get("edits"), list):
        counts = [one(e) for e in tool_input["edits"] if isinstance(e, dict)]
        return -1 if (not counts or any(c < 0 for c in counts)) else sum(counts)
    return -1


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
    sdir = getattr(server, "SUBWAY_DIR", None) or subway_home.subway_dir()
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


def _stale_reads_path():
    import server
    sdir = getattr(server, "SUBWAY_DIR", None) or subway_home.subway_dir()
    return os.path.join(sdir, "stale_reads.json")


def _load_stale_reads():
    p = _stale_reads_path(); data = {}
    try:
        if os.path.exists(p):
            data = json.load(open(p, encoding="utf-8")) or {}
    except Exception:
        data = {}
    return p, (data if isinstance(data, dict) else {})


def _save_stale_reads(p, data):
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        json.dump(data, open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, p)
    except Exception:
        pass


def _sha1_bytes(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def _sha1_file(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            return _sha1_bytes(f.read())
    except Exception:
        return None


def _stale_guard_enabled() -> bool:
    try:
        import server
        cfg = server._load_json("config.json") or {}
        v = cfg.get("stale_write_guard", True)
        return not (v is False or (isinstance(v, str) and str(v).lower() == "off"))
    except Exception:
        return True


def _stale_record_read(sid: str, real: str, sha: str):
    p, data = _load_stale_reads()
    sess = data.get(sid)
    if not isinstance(sess, dict):
        sess = {}
    sess[real] = sha
    if len(sess) > 500:
        # keep arbitrary 500 entries (cap)
        for k in list(sess.keys())[:-500]:
            sess.pop(k, None)
    data[sid] = sess
    _save_stale_reads(p, data)


def _stale_get_hash(sid: str, real: str) -> Optional[str]:
    _, data = _load_stale_reads()
    sess = data.get(sid)
    if not isinstance(sess, dict):
        return None
    v = sess.get(real)
    return v if isinstance(v, str) else None


def _stale_set_hash(sid: str, real: str, sha: Optional[str]):
    p, data = _load_stale_reads()
    sess = data.get(sid)
    if not isinstance(sess, dict):
        sess = {}
    if sha is None:
        sess.pop(real, None)
    else:
        sess[real] = sha
        if len(sess) > 500:
            for k in list(sess.keys())[:-500]:
                sess.pop(k, None)
    if sess:
        data[sid] = sess
    else:
        data.pop(sid, None)
    _save_stale_reads(p, data)

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


def _git_common_dir(path: str) -> str:
    """Return the normalized realpath of git's common dir for `path`, or '' if not in a git repo."""
    import subprocess
    cur = os.path.abspath(path)
    while cur and not os.path.isdir(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            return ""
        cur = parent
    if not cur:
        return ""
    try:
        out = subprocess.check_output(
            ["git", "-C", cur, "rev-parse", "--git-common-dir"],
            stderr=subprocess.DEVNULL, text=True, timeout=5,
        ).strip()
        if not out:
            return ""
        if not os.path.isabs(out):
            out = os.path.join(cur, out)
        return os.path.normcase(os.path.normpath(os.path.realpath(out)))
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

    read_warning: Optional[str] = None

    def _allow() -> Optional[Dict[str, Any]]:
        if read_warning:
            if is_antigravity:
                # B2: no top-level "additionalContext" here — Antigravity's protojson hook parser
                # rejects the unknown field and then refuses every read_file/run_command.
                return {"decision": "allow", "reason": read_warning}
            return {
                "systemMessage": read_warning,
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "additionalContext": read_warning,
                },
            }
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
            # B1: tool names now include pi's lowercase `edit`/`write` — pi principals were
            # completely ungated before (B7b coupling to the guard enable is tested intent).
            if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit", "write_file", "edit_file",
                             "edit", "write") and isinstance(_fp, str) and _fp:
                cpath = _canon_path(_fp, cwd)
                sdir = os.path.abspath(os.path.expanduser(getattr(server, "SUBWAY_DIR", "") or ""))
                import tempfile as _tempfile
                roots = [os.path.join(cwd, "scratchpad"), os.path.join(sdir, "worktrees"),
                         os.path.expanduser("~/.claude"), os.path.expanduser("~/.gemini"), "/tmp", _tempfile.gettempdir()]
                ar = [os.path.normcase(os.path.normpath(os.path.realpath(x))) for x in roots if x]
                creal = os.path.normcase(os.path.normpath(os.path.realpath(cpath)))
                rcwd = os.path.normcase(os.path.normpath(os.path.realpath(cwd)))
                in_allowed = any((creal == x or creal.startswith(x + os.sep))
                                 and not (rcwd == x or rcwd.startswith(x + os.sep)) for x in ar)
                in_project = creal == rcwd or creal.startswith(rcwd + os.sep)
                if not in_project:
                    cwd_common = _git_common_dir(rcwd)
                    if cwd_common and _git_common_dir(creal) == cwd_common:
                        in_project = True
                if in_project and not in_allowed:
                    small = _principal_small_edit_lines()
                    n = _edit_line_count(tool_name, tool_input) if small else -1
                    if not (0 <= n <= small):
                        extra = f" Small edits up to {small} changed lines are allowed; this one changes {n}." if small and n > small else ""
                        return _deny("Principal hands off: your chat does not edit project files. Spin a worker (spin_worker) with the exact edit." + extra)
            _cmd_val = tool_input.get("CommandLine") or tool_input.get("command")
            _exempt_cmd = (
                tool_name in ("Bash", "run_command")
                and isinstance(_cmd_val, str)
                and _is_exempt_read_budget_cmd(_cmd_val)
            )
            if not _exempt_cmd and (
                    tool_name in ("Read", "Grep", "Glob", "Bash", "read_file", "run_command", "search_web")
                    or _is_mcp_read_tool(tool_name, tool_input, mcp_read_tools)):
                if max_reads > 0:
                    n = _bump_principal_reads(sid)
                    if n > max_reads:
                        reset_mode = resolve_read_reset(read_budget, provider)
                        tail = (" Resets at compaction, or run `subway read-budget reset`."
                                if reset_mode == "compaction" else " Run `subway read-budget reset`.")
                        return _deny(f"Read budget reached ({n} reads, limit {max_reads}). Delegate this: spin a worker with spin_worker, "
                                     "and use code= packets in the brief instead of pulling code into this chat." + tail)
                    warn_pct = _read_budget_warn_pct()
                    if n * 100 >= max_reads * warn_pct:
                        read_warning = (
                            f"Read budget warning: {n}/{max_reads} reads used "
                            f"({(n * 100) // max_reads}%, threshold {warn_pct}%). "
                            "Consider delegating further exploration to a worker with spin_worker."
                        )

    cmd = tool_input.get("CommandLine") or tool_input.get("command")
    if cmd and _writes_subway_state(str(cmd)):
        return _deny("Budgets are the user's to change. Ask them to run it, or use the HUD.")
    if (tool_name in ("Bash", "run_command") or cmd is not None) and isinstance(cmd, str):
        if (re.search(r"(subway(?:-context)?|server\.py)\s+(budget|compaction)\b", cmd)
                or any(k in cmd for k in ("/api/budget", "/api/chat_budget", "/api/settings", "X-Subway-Token"))):
            return _deny("Budgets are the user's to change. Ask them to run it, or use the HUD.")
    if (
        tool_name in ("run_command", "Bash", "bash", "shell")
        and isinstance(cmd, str)
        and os.environ.get("SUBWAY_SANDBOX_EXEC") == "os"
        and os.environ.get("SUBWAY_SANDBOX_WORKTREE")
        and os.environ.get("SUBWAY_SANDBOX_STRICT_READS") == "1"
    ):
        read_roots = [
            os.path.normcase(os.path.normpath(os.path.realpath(p)))
            for p in os.environ.get("SUBWAY_SANDBOX_READ_ROOTS", "").split(os.pathsep)
            if p.strip()
        ]

        def _in_roots(p, roots):
            return any(p == r or p.startswith(r + os.sep) for r in roots)

        real_wt = os.path.normcase(os.path.normpath(os.path.realpath(os.environ["SUBWAY_SANDBOX_WORKTREE"])))
        raw_home = os.path.expanduser("~")
        real_home = os.path.normcase(os.path.normpath(os.path.realpath(raw_home)))
        for raw_tok in re.findall(r"""[^\s"'`|;&<>()]+""", cmd):
            tok = raw_tok.split("=", 1)[1] if ("=" in raw_tok and not raw_tok.startswith(("/", "~"))) else raw_tok
            if not tok:
                continue
            expanded = tok
            if expanded == "~" or expanded.startswith("~/"):
                expanded = raw_home + expanded[1:]
            elif expanded.startswith("$HOME/") or expanded == "$HOME":
                expanded = raw_home + expanded[5:]
            elif expanded.startswith("${HOME}/") or expanded == "${HOME}":
                expanded = raw_home + expanded[7:]
            if not os.path.isabs(expanded):
                continue
            cand = os.path.normcase(os.path.normpath(os.path.realpath(expanded)))
            if (cand == real_home or cand.startswith(real_home + os.sep)) and not (cand == real_wt or cand.startswith(real_wt + os.sep)):
                if not _in_roots(cand, read_roots):
                    return _deny("[SANDBOX] shell access to home path outside worktree denied")

    filepath = (tool_input.get("Path") or tool_input.get("path") or tool_input.get("file_path") or
                tool_input.get("filepath") or tool_input.get("notebook_path") or
                tool_input.get("TargetFile")) if isinstance(tool_input, dict) else None
    cpath = None
    real_cpath = None
    if filepath and isinstance(filepath, str):
        cpath = os.path.normcase(os.path.normpath(_canon_path(filepath, cwd)))
        sdir = os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(getattr(server, "SUBWAY_DIR", "") or subway_home.subway_dir()))))
        sreal = os.path.normcase(os.path.normpath(os.path.realpath(sdir)))
        real_cpath = os.path.normcase(os.path.normpath(os.path.realpath(cpath)))
        wdir = os.path.normcase(os.path.normpath(os.path.join(sdir, "worktrees")))
        wreal = os.path.normcase(os.path.normpath(os.path.join(sreal, "worktrees")))
        in_worktrees = (cpath == wdir or cpath.startswith(wdir + os.sep)
                        or real_cpath == wreal or real_cpath.startswith(wreal + os.sep))
        if not in_worktrees and (cpath == sdir or cpath.startswith(sdir + os.sep)
                or real_cpath == sreal or real_cpath.startswith(sreal + os.sep)):
            return _deny("Subway's state files are the user's to change.")
        if os.environ.get("SUBWAY_SANDBOX_EXEC") == "os" and os.environ.get("SUBWAY_SANDBOX_WORKTREE"):
            sb_wt = os.path.normcase(os.path.normpath(os.path.realpath(os.environ["SUBWAY_SANDBOX_WORKTREE"])))
            in_wt = (real_cpath == sb_wt or real_cpath.startswith(sb_wt + os.sep))
            if tool_name in ("write_file", "edit_file", "Write", "Edit", "MultiEdit", "NotebookEdit",
                             "replace_file_content", "multi_replace_file_content", "write_to_file"):
                git_hooks = os.path.join(sb_wt, ".git", "hooks")
                git_config = os.path.join(sb_wt, ".git", "config")
                in_protected_git = (
                    real_cpath == git_hooks or real_cpath.startswith(git_hooks + os.sep)
                    or real_cpath == git_config or real_cpath.startswith(git_config + os.sep)
                )
                if not in_wt or in_protected_git:
                    return _deny("[SANDBOX] write outside worktree denied")
            elif (os.environ.get("SUBWAY_SANDBOX_STRICT_READS") == "1"
                  and tool_name in ("read_file", "Read", "view_file", "view_file_outline", "view_code_item")):
                home_real = os.path.normcase(os.path.normpath(os.path.realpath(os.path.expanduser("~"))))
                read_roots = [
                    os.path.normcase(os.path.normpath(os.path.realpath(p)))
                    for p in os.environ.get("SUBWAY_SANDBOX_READ_ROOTS", "").split(os.pathsep)
                    if p.strip()
                ]

                def _in_roots(p, roots):
                    return any(p == r or p.startswith(r + os.sep) for r in roots)

                if (real_cpath == home_real or real_cpath.startswith(home_real + os.sep)) and not (in_wt or _in_roots(real_cpath, read_roots)):
                    return _deny("[SANDBOX] read outside worktree denied")

    # 62b chat branches: a chat with its own branch edits only its worktree; with chat_branch_auto on, a second
    # chat editing a tree another chat is editing must take a branch first.
    try:
        if (sid and principal and filepath and isinstance(filepath, str) and tool_name in
                ("Edit", "MultiEdit", "Write", "NotebookEdit", "write_file", "edit_file", "replace_file_content", "write_to_file")):
            import chat_branches
            why = chat_branches.guard_edit(sid, cpath or _canon_path(filepath, cwd))
            if why:
                return _deny(why)
    except Exception:
        pass

    # Stale-write guard (after existing denials, before governor block)
    try:
        if sid and _stale_guard_enabled():
            if tool_name in ("Read", "view_file", "read_file") and filepath and isinstance(filepath, str):
                if cpath is None:
                    cpath = os.path.normcase(os.path.normpath(_canon_path(filepath, cwd)))
                    real_cpath = os.path.normcase(os.path.normpath(os.path.realpath(cpath)))
                if os.path.exists(cpath):
                    sha = _sha1_file(cpath)
                    if sha is not None:
                        _stale_record_read(sid, real_cpath, sha)
            elif tool_name in ("Edit", "MultiEdit", "Write", "replace_file_content", "write_to_file") and filepath and isinstance(filepath, str):
                if cpath is None:
                    cpath = os.path.normcase(os.path.normpath(_canon_path(filepath, cwd)))
                    real_cpath = os.path.normcase(os.path.normpath(os.path.realpath(cpath)))
                stored = _stale_get_hash(sid, real_cpath)
                if stored is not None:
                    if not os.path.exists(cpath):
                        # no stored hash or missing file → allow (stored exists but file missing treated as allow)
                        pass
                    else:
                        cur = _sha1_file(cpath)
                        if cur is not None and cur != stored:
                            return _deny(f"Stale write: {os.path.basename(cpath)} changed since you read it (another chat or worker edited it). Re-read it, then redo your edit.")
                        if cur is not None and cur == stored:
                            # compute expected post-write hash
                            new_sha = None
                            try:
                                if tool_name == "Write":
                                    content = tool_input.get("content")
                                    if isinstance(content, str):
                                        new_sha = _sha1_bytes(content.encode("utf-8"))
                                    elif isinstance(content, bytes):
                                        new_sha = _sha1_bytes(content)
                                    else:
                                        new_sha = None
                                elif tool_name == "Edit":
                                    old_string = tool_input.get("old_string")
                                    new_string = tool_input.get("new_string")
                                    replace_all = bool(tool_input.get("replace_all"))
                                    with open(cpath, "rb") as f:
                                        raw = f.read()
                                    text = raw.decode("utf-8", "replace")
                                    if not isinstance(old_string, str) or old_string not in text:
                                        _stale_set_hash(sid, real_cpath, None)
                                        stored = None  # allow
                                        new_sha = "__deleted__"
                                    else:
                                        if not isinstance(new_string, str):
                                            new_string = "" if new_string is None else str(new_string)
                                        if replace_all:
                                            new_text = text.replace(old_string, new_string)
                                        else:
                                            new_text = text.replace(old_string, new_string, 1)
                                        new_sha = _sha1_bytes(new_text.encode("utf-8"))
                                elif tool_name == "MultiEdit":
                                    edits = tool_input.get("edits")
                                    if isinstance(edits, list):
                                        with open(cpath, "rb") as f:
                                            raw = f.read()
                                        text = raw.decode("utf-8", "replace")
                                        ok = True
                                        for ed in edits:
                                            if not isinstance(ed, dict):
                                                ok = False; break
                                            o = ed.get("old_string")
                                            n = ed.get("new_string")
                                            ra = bool(ed.get("replace_all"))
                                            if not isinstance(o, str) or o not in text:
                                                ok = False; break
                                            if not isinstance(n, str):
                                                n = "" if n is None else str(n)
                                            text = text.replace(o, n) if ra else text.replace(o, n, 1)
                                        if not ok:
                                            _stale_set_hash(sid, real_cpath, None)
                                            new_sha = "__deleted__"
                                        else:
                                            new_sha = _sha1_bytes(text.encode("utf-8"))
                                    else:
                                        _stale_set_hash(sid, real_cpath, None)
                                        new_sha = "__deleted__"
                                else:
                                    # replace_file_content, write_to_file, others → delete entry
                                    _stale_set_hash(sid, real_cpath, None)
                                    new_sha = "__deleted__"
                            except Exception:
                                _stale_set_hash(sid, real_cpath, None)
                                new_sha = "__deleted__"
                            if new_sha is not None and new_sha != "__deleted__":
                                _stale_set_hash(sid, real_cpath, new_sha)
    except Exception:
        pass

    if principal:
        try:
            import governor
            turn_key = ""
            # Antigravity is governed by adapters/antigravity_governor.py; an event
            # with no session id would share one never-resetting "claude:" counter.
            if provider != "claude" or not event.get("session_id"):
                raise LookupError("governor: not a Claude Code event")
            r = governor.check("claude", event.get("session_id", ""), event.get("tool_name", ""), event.get("tool_input"), turn_key=turn_key)
            if isinstance(r, dict) and not r.get("allow", True):
                return _deny(r.get("reason", "Governor limit reached."))
        except Exception:
            pass

    if not filepath or not isinstance(filepath, str):
        return _allow()
    if cpath is None:
        cpath = os.path.normcase(os.path.normpath(_canon_path(filepath, cwd)))
    locks_data = load_locks()["locks"]
    lease = locks_data.get(cpath)
    if lease is None:
        for lk, lv in locks_data.items():
            if "::" not in lk and os.path.normcase(os.path.normpath(lk)) == cpath:
                lease = lv
                break
    if lease and lease.get("holder_id") != sid and lease.get("holder") != sid:
        rem = max(0, int(lease.get("expires_at", 0) - time.time()))
        return _deny(f"Subway lease: {filepath} is held by {lease.get('holder')} for "
                     f"'{lease.get('purpose')}' ({rem}s left). Wait, or coordinate through send_note.")

    prefix = f"{cpath}::"
    sym_leases = {k: v for k, v in locks_data.items() if os.path.normcase(os.path.normpath(k.split("::", 1)[0])) + "::" == prefix and "::" in k}
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
# Shared Context Tracks (~/.subway/tracks/)
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
