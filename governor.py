#!/usr/bin/env python3
"""Governor: limits principal probe calls per turn, requiring worker delegation."""
import contextlib
import json
import os
import re
import sys
import time

try:
    import fcntl
except ImportError:
    fcntl = None

DEFAULT_LIMIT = 6

ALWAYS_ALLOWED = {  # bare tool names (strip any "mcp__<server>__" prefix first)
    "spin_worker", "spin_batch", "spin_swarm", "spin_iteration", "run_concurrent",
    "approve_worker", "reject_worker", "stop_worker", "worker_status", "await_worker",
    "collect_reports", "on_worker_done", "instruct_worker", "escalate_worker",
    "hud_status", "check_notes", "send_note", "broadcast_track", "join_track",
    "read_track", "answer_permission", "permission_requests", "watch_workers",
    "whoami", "quota_status", "list_locks",
    "AskUserQuestion", "TodoWrite", "ToolSearch", "Skill", "ExitPlanMode", "EnterPlanMode",
}

LANDING_RE = re.compile(
    r"^\s*(git (apply|status|diff --stat)|(node bin/)?subway-context(\.js)? (run|approve|workers|governor)\b)"
)

_SHELL_TOOLS = {
    "bash", "run_command", "shell", "terminal", "execute_command",
    "run_terminal", "run_shell_command", "sh", "zsh", "cmd",
}


def _subway_dir() -> str:
    srv = sys.modules.get("server")
    return (
        getattr(srv, "SUBWAY_DIR", None)
        or os.environ.get("SUBWAY_DIR")
        or os.path.expanduser("~/.subway-context")
    )


def bare(tool_name) -> str:
    name = str(tool_name or "")
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3:
            return parts[2]
    return name


def settings() -> dict:
    """Read config.json governor settings. Missing = defaults."""
    cfg = {}
    p = os.path.join(_subway_dir(), "config.json")
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
    except Exception:
        cfg = {}
    gov = cfg.get("governor")
    if not isinstance(gov, dict):
        gov = {}
    enabled = gov.get("enabled", True)
    limit = gov.get("probe_calls_per_turn", DEFAULT_LIMIT)
    try:
        limit = int(limit)
    except Exception:
        limit = DEFAULT_LIMIT
    providers = gov.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    return {
        "enabled": bool(enabled),
        "probe_calls_per_turn": limit,
        "providers": dict(providers),
    }


def is_probe(tool_name, tool_input=None) -> bool:
    """False if bare name in ALWAYS_ALLOWED; for shell tools, False if command matches LANDING_RE; else True."""
    b = bare(tool_name)
    if b in ALWAYS_ALLOWED:
        return False
    if b.lower() in _SHELL_TOOLS or "bash" in b.lower() or "shell" in b.lower():
        cmd = ""
        if isinstance(tool_input, dict):
            for k in ("command", "CommandLine", "cmd", "input", "script"):
                v = tool_input.get(k)
                if isinstance(v, str):
                    cmd = v
                    break
        elif isinstance(tool_input, str):
            cmd = tool_input
        if cmd and LANDING_RE.search(cmd):
            return False
    return True


@contextlib.contextmanager
def _state_lock():
    if fcntl is None:
        yield
        return
    sdir = _subway_dir()
    lock_file = os.path.join(sdir, ".governor.lock")
    try:
        os.makedirs(sdir, exist_ok=True)
        fd = open(lock_file, "a+")
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        try:
            yield
        finally:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                fd.close()
            except Exception:
                pass
    except Exception:
        yield


def _state_path() -> str:
    return os.path.join(_subway_dir(), "governor_state.json")


def _read_state() -> dict:
    p = _state_path()
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    sessions = data.get("sessions")
                    if isinstance(sessions, dict):
                        return data
    except Exception:
        pass
    return {"sessions": {}}


def _write_state(data: dict) -> None:
    p = _state_path()
    try:
        sdir = os.path.dirname(p)
        os.makedirs(sdir, exist_ok=True)
        # Drop entries older than 7 days
        cutoff = time.time() - (7 * 86400)
        sessions = data.get("sessions", {})
        cleaned = {}
        for k, v in sessions.items():
            if isinstance(v, dict):
                at_val = v.get("at", 0)
                try:
                    if float(at_val) >= cutoff:
                        cleaned[k] = v
                except Exception:
                    pass
        data["sessions"] = cleaned
        tmp = f"{p}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, p)
    except Exception:
        pass


def turn(provider, session_id, turn_key="") -> None:
    """New user turn: count back to 0."""
    try:
        with _state_lock():
            st = _read_state()
            sessions = st.setdefault("sessions", {})
            key = f"{provider}:{session_id}"
            sessions[key] = {
                "used": 0,
                "turn_key": str(turn_key or ""),
                "at": time.time(),
            }
            _write_state(st)
    except Exception:
        pass


def check(provider, session_id, tool_name, tool_input=None, turn_key="") -> dict:
    """Check if tool call is allowed for this provider turn."""
    cfg = settings()
    limit = cfg.get("probe_calls_per_turn", DEFAULT_LIMIT)
    try:
        # Check global and per-provider enabled flags
        if not cfg.get("enabled", True):
            return {"allow": True, "used": 0, "limit": limit, "reason": ""}
        prov_map = cfg.get("providers", {})
        if provider in prov_map and not prov_map[provider]:
            return {"allow": True, "used": 0, "limit": limit, "reason": ""}

        key = f"{provider}:{session_id}"
        probe = is_probe(tool_name, tool_input)

        with _state_lock():
            st = _read_state()
            sessions = st.setdefault("sessions", {})
            sess = sessions.get(key)
            if not isinstance(sess, dict):
                sess = {"used": 0, "turn_key": str(turn_key or ""), "at": time.time()}

            stored_turn = sess.get("turn_key", "")
            used = int(sess.get("used", 0))

            # turn_key given and different from stored -> reset
            if turn_key and turn_key != stored_turn:
                used = 0
                sess["turn_key"] = str(turn_key)

            if not probe:
                sess["at"] = time.time()
                sessions[key] = sess
                _write_state(st)
                return {"allow": True, "used": used, "limit": limit, "reason": ""}

            used += 1
            sess["used"] = used
            sess["at"] = time.time()
            sessions[key] = sess
            _write_state(st)

            if used <= limit:
                return {"allow": True, "used": used, "limit": limit, "reason": ""}
            else:
                reason = (
                    f"[GOVERNOR] {used-1}/{limit} probe calls used this turn. "
                    f"Delegate: spin_worker with the exact code and edit. "
                    f"Still allowed: spin/approve/status/notes, and landing "
                    f"(git apply, git status, git diff --stat, subway-context run)."
                )
                return {"allow": False, "used": used, "limit": limit, "reason": reason}
    except Exception:
        return {"allow": True, "used": 0, "limit": limit, "reason": ""}


def status() -> list:
    """Return status list: [{"key": "provider:session", "used", "limit", "at"}]."""
    try:
        cfg = settings()
        limit = cfg.get("probe_calls_per_turn", DEFAULT_LIMIT)
        with _state_lock():
            st = _read_state()
            sessions = st.get("sessions", {})
            out = []
            for k, v in sessions.items():
                if isinstance(v, dict):
                    out.append({
                        "key": k,
                        "used": int(v.get("used", 0)),
                        "limit": limit,
                        "at": v.get("at", 0),
                    })
            return out
    except Exception:
        return []
