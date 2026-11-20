import json
"""Per-provider line deliverers, headless execution, and send_to_chat.

Every _deliver_* returns (ok, method) and never raises.
Mail-only routing (HOP_CAP/_wire_text/_queue_result/deliver) lives in
fastmail_lines.py, which re-exports this module's symbols.
"""
import os
import re
import sys
import threading
import time
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple

import wake_driver
import server as _s


# Per-source delivery capability reported by delivery_coverage().
_DELIVERY_CAPABILITY: Dict[str, str] = {
    "claude": "live",       # push, falls back to spool
    "antigravity": "live",  # connectrpc/gui/resume
    "opencode": "live",     # push to live pids when running
    "local": "live",
    "pi": "spool",          # spool only
    "copilot": "headless",  # copilot --resume -p, no open-window channel
    "codex": "headless",    # codex exec resume
    "cursor": "none",
    "devin": "none",
}


def _deliver_no_channel(source: str, reason: str) -> Callable[[str, str], Tuple[bool, str]]:
    def _fn(chat_id: str, text: str) -> Tuple[bool, str]:
        return False, f"{source} has no live input channel: {reason}"
    return _fn


def _canonical_chat_id(chat_id: str, source: str = "") -> str:
    """A4: wake spools are keyed by canonical chat id — only canonical ids are drained.
    An alias (`@claude-main`) spooled raw sits there forever. Everything must be live:
    a resolvable alias returns its canonical id; an UNRESOLVABLE alias returns ""
    so the deliverer fails loudly (`unresolved`) instead of silently stranding the
    message in a spool file no one ever drains."""
    clean = (chat_id or "").strip()
    if not (clean.startswith("@") or clean.lower().endswith("-latest")):
        return chat_id
    try:
        hit = (_s.load_aliases() or {}).get(clean.lstrip("@").lower())
        if hit:
            return str(hit)
    except Exception:
        pass
    try:
        c = _s.resolve(clean, strict=True, source=source) if source else _s.resolve(clean, strict=True)
        return str(c.get("id") or "")
    except Exception:
        return ""


def _deliver_claude(chat_id: str, text: str) -> Tuple[bool, str]:
    chat_id = _canonical_chat_id(chat_id, "claude")
    if not chat_id:
        return False, "unresolved: send to a chat id, not an unresolvable alias"
    try:
        if wake_driver.wake_push_enqueue(chat_id, text, kind="fastmail"):
            return True, "push"
    except Exception:
        pass
    try:
        ev = wake_driver.WakeEvent(kind="fastmail", chat_id=chat_id, text=text)
        if wake_driver.wake_spool_enqueue(chat_id, ev):
            return True, "spool"
    except Exception:
        pass
    return False, "spool"


def _deliver_pi(chat_id: str, text: str, kind: str = "fastmail") -> Tuple[bool, str]:
    # Live rule: a spool file is only drained when the pi session next calls Subway —
    # spooling into a closed session's file (the 'No reply after 4 minutes' case)
    # pretends delivery while the message rots. Spool live/mid-turn sessions; refuse
    # dead ones loudly.
    if not is_online({"source": "pi", "chat_id": chat_id}):
        return False, "chat is closed: pi delivers only to a running session — start a new chat or spin a worker"
    try:
        ev = wake_driver.WakeEvent(kind=kind, chat_id=chat_id, text=text)
        if wake_driver.wake_spool_enqueue(chat_id, ev):
            return True, "spool"
    except Exception:
        pass
    return False, "spool"


def _deliver_antigravity(chat_id: str, text: str, project_dir: str = "", sender_id: str = "fastmail") -> Tuple[bool, str]:
    try:
        if wake_driver.antigravity_connectrpc_wake(chat_id, text, project_dir, sender_id=sender_id):
            return True, "connectrpc"
    except Exception:
        pass
    try:
        if wake_driver.agy_gui_wake(chat_id, text):
            return True, "gui"
    except Exception:
        pass
    try:
        if wake_driver.antigravity_resume(text, chat_id, project_dir):
            return True, "resume"
    except Exception:
        pass
    return False, "none"


def _opencode_session_directory(session_id: str) -> str:
    try:
        con = _s._opencode_db() if hasattr(_s, "_opencode_db") else None
    except Exception:
        con = None
    if con is None:
        try:
            from chat_readers import opencode as _oc
            con = _oc._opencode_db() if hasattr(_oc, "_opencode_db") else None
        except Exception:
            con = None
    if con is None:
        return ""
    try:
        row = con.execute("select directory from session_v2 where id = ?", (session_id,)).fetchone()
        return str(row[0] or "") if row else ""
    except Exception:
        return ""
    finally:
        try:
            con.close()
        except Exception:
            pass


def _opencode_cwd_pids(directory: str) -> List[int]:
    """Pids of running opencode processes whose working directory is `directory` (macOS/Linux, via lsof)."""
    try:
        out = subprocess.run(["lsof", "-a", "-d", "cwd", "-c", "opencode", "-Fpn"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    want = os.path.realpath(directory)
    pids: List[int] = []
    pid = 0
    for line in out.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid and os.path.realpath(line[1:]) == want and pid not in pids:
            pids.append(pid)
    return pids


def _opencode_live_pids(session_id: str) -> List[int]:
    """Map an OpenCode station to live pids. Returns [] when unknown (queue only)."""
    directory = _opencode_session_directory(session_id)
    if not directory:
        return []
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/v", "/fo", "csv"], capture_output=True,
                                 text=True, timeout=5).stdout
            pids: List[int] = []
            for line in out.splitlines():
                if "opencode" in line.lower():
                    parts = [p.strip('" ') for p in line.split(",")]
                    for p in parts:
                        if p.isdigit():
                            pids.append(int(p))
                            break
            return pids
        out = subprocess.run(["ps", "-o", "pid=,command=", "-ax"], capture_output=True,
                             text=True, timeout=5).stdout
        pids = []
        for line in out.splitlines():
            line = line.strip()
            if not line or "opencode" not in line.lower():
                continue
            parts = line.split(None, 1)
            if not parts or not parts[0].isdigit():
                continue
            cmd = parts[1] if len(parts) > 1 else ""
            # Cheap, safe match: the command line references the session's directory.
            if directory and directory in cmd:
                pids.append(int(parts[0]))
        for p in _opencode_cwd_pids(directory):
            if p not in pids:
                pids.append(p)
        return pids
    except Exception:
        return []


def _deliver_opencode(chat_id: str, text: str) -> Tuple[bool, str]:
    try:
        session_id = chat_id.strip().lstrip("@")
        if session_id.startswith("opencode-"):
            session_id = session_id[len("opencode-"):]
        pids = _opencode_live_pids(session_id)
        if not pids:
            return False, "no live pid"
        ok_any = False
        for pid in pids:
            try:
                if wake_driver.wake_push_enqueue(f"opencode-{pid}", text, kind="fastmail"):
                    ok_any = True
            except Exception:
                continue
        return (True, "push") if ok_any else (False, "push")
    except Exception:
        return False, "no live pid"


def _deliver_worker(worker_id: str, text: str) -> Tuple[bool, str]:
    try:
        from cli_tools import t_instruct_worker
        res = t_instruct_worker(worker_id=worker_id, text=text, now=True)
    except Exception as e:
        return False, f"instruct_worker error: {e}"
    if isinstance(res, str) and (res.startswith("Error") or res.startswith("Refused")):
        return False, res
    return True, "instruct"


_deliver_copilot = _deliver_no_channel("copilot", "Copilot chat history is read-only in Subway")
_deliver_codex = _deliver_no_channel("codex", "Codex sessions have no live input channel")
_deliver_cursor = _deliver_no_channel("cursor", "Cursor conversations are read-only in Subway")
_deliver_devin = _deliver_no_channel("devin", "Devin sessions are read-only in Subway")
_deliver_local = _deliver_no_channel("local", "Local sessions use headless continue")


# Registry: detected chat source -> deliver(chat_id, text[, project_dir]) handler.
# send_to_chat derives "unsupported/no channel" from absence in this registry.
# Entries defer to module attributes so tests/patches can replace deliverers.
CHAT_DELIVERERS: Dict[str, Callable[..., Tuple[bool, str]]] = {
    "claude": lambda chat_id, text: _deliver_claude(chat_id, text),
    "antigravity": lambda chat_id, text, project_dir="": _deliver_antigravity(chat_id, text, project_dir),
    "opencode": lambda chat_id, text: _deliver_opencode(chat_id, text),
    "local": lambda chat_id, text: _deliver_local(chat_id, text),
    "pi": lambda chat_id, text: _deliver_pi(chat_id, text),
    "copilot": lambda chat_id, text: _deliver_copilot(chat_id, text),
    "codex": lambda chat_id, text: _deliver_codex(chat_id, text),
    "cursor": lambda chat_id, text: _deliver_cursor(chat_id, text),
    "devin": lambda chat_id, text: _deliver_devin(chat_id, text),
}


def _detected_chat_sources() -> Tuple[str, ...]:
    try:
        from chat_readers import dispatch as _dispatch
        srcs = getattr(_dispatch, "DETECTED_CHAT_SOURCES", None)
        if srcs:
            return tuple(srcs)
    except Exception:
        pass
    try:
        import chat_readers.dispatch as _dispatch
        srcs = getattr(_dispatch, "DETECTED_CHAT_SOURCES", None)
        if srcs:
            return tuple(srcs)
    except Exception:
        pass
    return tuple(CHAT_DELIVERERS.keys())


def delivery_coverage() -> Dict[str, str]:
    """Return {source: "live"|"spool"|"none"} for every source chat_readers can detect."""
    cov: Dict[str, str] = {}
    for source in _detected_chat_sources():
        cov[source] = _DELIVERY_CAPABILITY.get(source, "none" if source in CHAT_DELIVERERS else "none")
    return cov


def loose_chat_dir() -> str:
    """A fresh, empty folder for a chat that belongs to no project (never the HUD's own cwd or $HOME)."""
    import secrets
    base = os.path.join(os.path.expanduser("~"), ".subway", "chats")
    path = os.path.join(base, time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2))
    os.makedirs(path, exist_ok=True)
    return path


def claude_waiter_alive(cid: str) -> bool:
    """True when an open Claude window has a live wake waiter that will turn a push into a reply."""
    try:
        import wake_driver
        safe = "".join(ch for ch in cid if ch.isalnum() or ch in "-_@.")
        with open(os.path.join(wake_driver.get_spool_dir(), f"{safe}.waiter")) as f:
            pid = int(f.read().split()[0])
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def claude_window_closed(cid: str, path: str = "", idle_seconds: float = 90.0) -> bool:
    """True only on positive evidence that no Claude window holds this chat.

    Claude registers every open interactive window in its sessions list, so that list is the
    evidence. A recent transcript write is not: a headless reply that just finished looks the same.
    If the list can't be read we report "open" (push), never a second writer."""
    try:
        from chat_readers import claude_open_sessions
        sessions = claude_open_sessions()
    except Exception:
        return False
    if not isinstance(sessions, dict):
        return False
    return not any(cid == k or cid in str(k) or cid in str((v or {}).get("id", "")) for k, v in sessions.items())


def claude_headless_running(cid: str) -> bool:
    """A headless `claude -p` run for this chat is still going (it isn't in the open-window list)."""
    try:
        out = subprocess.run(["pgrep", "-f", f"--(resume|session-id)[ =]{cid}"], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return False
    return any(pid.strip() and int(pid) != os.getpid() for pid in out.split())


def is_online(target: Dict[str, Any]) -> bool:
    source = str((target or {}).get("source") or (target or {}).get("provider") or "").lower()
    tid = str((target or {}).get("chat_id") or (target or {}).get("id") or (target or {}).get("to") or "")
    try:
        if source == "claude":
            from chat_readers import claude_open_sessions
            sessions = claude_open_sessions() or {}
            return any(tid == k or tid in str(k) or tid in str((v or {}).get("id", "")) for k, v in sessions.items())
        if source == "antigravity":
            pid = wake_driver.agy_window_pid(tid)
            if pid and wake_driver.agy_window_registered(pid):
                return True
            try:
                from chat_readers import _agy_hubs
                return bool(_agy_hubs())
            except Exception:
                return False
        if source == "opencode":
            session_id = tid.strip().lstrip("@")
            if session_id.startswith("opencode-"):
                session_id = session_id[len("opencode-"):]
            return bool(_opencode_live_pids(session_id))
        if source == "pi":
            # Live check for THE chat: ~/.subway/pi_sessions/<pid> = sessionId is written
            # by each RUNNING pi's wake extension. The old check — "does the dir have any
            # entries" — returned online forever once mappings accumulated (3,316 stale
            # pid files here), so dead sessions read as live and messages were spooled
            # into graveyards no one drains (the 'No reply after 4 minutes' case).
            mapping_dir = os.path.join(getattr(_s, "SUBWAY_DIR", os.path.expanduser("~/.subway")), "pi_sessions")
            try:
                names = os.listdir(mapping_dir)
            except OSError:
                return False
            tid_clean = tid.strip().lstrip("@")
            if not tid_clean:
                return False
            for name in names:
                try:
                    pid = int(name)
                except ValueError:
                    continue
                try:
                    with open(os.path.join(mapping_dir, name), "r", encoding="utf-8", errors="replace") as f:
                        sid = f.read().strip()
                except OSError:
                    continue
                if sid and (sid == tid_clean or tid_clean in sid):
                    # Mapping matches; the session is live only if its pi process still runs.
                    try:
                        os.kill(pid, 0)
                        return True
                    except OSError:
                        continue
            return False
        if source == "worker":
            try:
                w = _s._load_worker(tid)
            except Exception:
                return False
            return bool(w) and str(w.get("status") or "") in getattr(_s, "ACTIVE_WORKER_STATES", ())
        return False
    except Exception:
        return False



def _find_headless_bin(cli: str) -> Optional[str]:
    import shutil
    if cli == "pi":
        if os.name != "nt":
            wrapper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "pi-deep")
            if os.path.isfile(wrapper) and os.access(wrapper, os.X_OK):
                return wrapper
        return shutil.which("pi")
    if cli in ("opencode", "local"):
        return shutil.which("opencode") or shutil.which("opencode2")
    if cli == "antigravity":
        find_agy = getattr(_s, "find_agy_binary", None)
        return (find_agy() if callable(find_agy) else None) or shutil.which("agy")
    return shutil.which(cli)


def _opencode_model_ref(model: str) -> str:
    """OpenCode's -m wants provider/model. The catalog lists its own models bare (iceman-free ->
    opencode/iceman-free). A model from another provider (e.g. a Claude id left in the composer)
    is dropped, so the session keeps its own model instead of failing."""
    model = str(model or "").strip()
    if not model or "/" in model:
        return model
    try:
        import local_models
        known = set(local_models._opencode_models(block=False) or [])
    except Exception:
        known = set()
    return f"opencode/{model}" if model in known else ""


# Headless Antigravity can't prompt, so it auto-denies MCP calls. Allow only Subway's own MCP server,
# for these background sends only (--settings is per-session and never saved). Edits and shell stay denied.
def _agy_headless_settings() -> str:
    """Build per-session Antigravity headless permissions from config (shell stays denied)."""
    allow = ["mcp(subway/*)"]
    try:
        cfg = _s._load_json("config.json")
        extra = cfg.get("headless_allow") if isinstance(cfg, dict) else None
        if isinstance(extra, list):
            for item in extra:
                if isinstance(item, str) and item.strip() and item not in allow:
                    allow.append(item.strip())
    except Exception:
        pass
    return json.dumps({"permissions": {"allow": allow}})


def _build_headless_cmd(
    source: str,
    binary: str,
    sid: str,
    text: str,
    model: str = "",
    effort: str = "",
    new_session: bool = False,
) -> List[str]:
    skip = {"", "auto", "default", "inherit"}
    if source == "opencode":
        # Subcommand-scoped flags must follow 'run'; never hand them to top-level
        # 'opencode' (that is the 'Unrecognized flag: --format / -m' error).
        cmd = [binary, "run"]
        # --session only accepts a real ses_* id. A launched 'subway-*' sid is a
        # placeholder; let opencode create the session and let stream_tee record
        # the launched->real id mapping (SUBWAY_LAUNCH_SID / _record_launch_id).
        if not new_session or sid.startswith("ses_"):
            cmd.extend(["--session", sid])
        ref = _opencode_model_ref(model) if model not in skip else ""
        if ref:
            cmd.extend(["-m", ref])
        return cmd + [text]
    if source == "local":
        cmd = [binary, "run"]
        if not new_session or sid.startswith("ses_"):
            cmd.extend(["--session", sid])
        if model not in skip:
            mref = model if model.startswith("ollama/") else f"ollama/{model}"
            cmd.extend(["-m", mref])
        return cmd + [text]
    if source == "claude":
        flag = "--session-id" if new_session else "--resume"
        cmd = [binary, "-p", flag, sid]
        if model not in skip:
            cmd.extend(["--model", model])
        if effort and effort not in ("auto", "none"):
            cmd.extend(["--effort", effort])
        return cmd + [text]
    if source == "pi":
        flag = "--session-id" if new_session else "--session"
        cmd = [binary, flag, sid, "-p"]
        if model not in skip:
            cmd.extend(["--model", model])
        if effort and effort not in ("auto", "none"):
            cmd.extend(["--thinking", effort])
        return cmd + [text]
    if source == "copilot":
        cmd = [binary] if new_session else [binary, "--resume", sid]
        cmd.extend(["-p", text, "--output-format", "json"])
        if model not in skip:
            cmd.extend(["--model", model])
        return cmd
    if source == "codex":
        cmd = [binary, "exec", "--json"] + (["-m", model] if model not in skip else [])
        return cmd + ([text] if new_session else ["resume", sid, text])
    if source == "antigravity":
        if new_session:
            cmd = [binary, "--print", text, "--output-format", "stream-json", "--print-timeout", "0", "--settings", _agy_headless_settings()]
        else:
            cmd = [binary, "--conversation", sid, "--print", text, "--output-format", "stream-json", "--print-timeout", "0", "--settings", _agy_headless_settings()]
        if model not in skip:
            cmd.extend(["--model", model])
        if effort and effort not in ("auto", "none"):
            cmd.extend(["--effort", effort])
        return cmd
    if source == "cursor":
        cmd = [binary, "-p"] if new_session else [binary, "-p", "--resume", sid]
        if model not in skip:
            cmd.extend(["--model", model])
        return cmd + [text]
    return []


HEADLESS_CONTINUE = {
    "opencode": ("opencode", lambda b, sid, text: [b, "run", "--session", sid, text]),
    "local": ("opencode", lambda b, sid, text: [b, "run", "--session", sid, text]),
    "claude": ("claude", lambda b, sid, text: [b, "-p", "--resume", sid, text]),
    "pi": ("pi", lambda b, sid, text: [b, "--session", sid, "-p", text]),
    "copilot": ("copilot", lambda b, sid, text: [b, "--resume", sid, "-p", text, "--output-format", "json"]),
    "codex": ("codex", lambda b, sid, text: [b, "exec", "--json", "resume", sid, text]),
    "antigravity": ("antigravity", lambda b, sid, text: [b, "--conversation", sid, "--print", text, "--output-format", "stream-json", "--print-timeout", "0", "--settings", _agy_headless_settings()]),
    "cursor": ("agent", lambda b, sid, text: [b, "-p", "--resume", sid, text]),
}
_headless_runs: Dict[str, Any] = {}
_headless_meta: Dict[str, Dict[str, Any]] = {}
_headless_lock = threading.Lock()


class _PendingRun:
    pid = 0

    def poll(self) -> None:
        return None


def _handoff_aliases(sid: str) -> List[str]:
    """Return sid plus any launched/real session aliases from handoff_sessions.json."""
    aliases: List[str] = [sid]
    if not sid:
        return aliases
    hmap = _load_handoff_map()
    mapped = hmap.get(sid)
    if mapped and mapped not in aliases:
        aliases.append(mapped)
    for launched, real in hmap.items():
        if real == sid and launched not in aliases:
            aliases.append(launched)
    return aliases


def _record_headless_meta(key: str, proc: Any, log_path: str, offset: int, text: str) -> None:
    """Remember where a headless run streams its output so the HUD can tail it live."""
    _headless_meta[key] = {"log": log_path, "offset": int(offset or 0),
                           "text": text or "", "started": time.time()}


def latest_run(chat_id: str) -> Optional[Dict[str, Any]]:
    """Newest headless run for a chat: {"log", "offset", "text", "started", "running"} or None.

    Accepts a bare session id or a "<source>-<sid>" chat id, matching how the
    launcher strips the source prefix from chat ids.
    """
    cid = (chat_id or "").strip().lstrip("@")
    if not cid:
        return None
    hmap = _load_handoff_map()
    best_key: Optional[str] = None
    best_started = -1.0
    with _headless_lock:
        for key in list(_headless_meta):
            source, _, sid = key.partition(":")
            cands = [sid]
            mapped = hmap.get(sid)
            if mapped:
                cands.append(mapped)
            for launched, real in hmap.items():
                if real == sid and launched not in cands:
                    cands.append(launched)
            if any(c == cid or (source and f"{source}-{c}" == cid) for c in cands):
                try:
                    started = float(_headless_meta[key].get("started") or 0.0)
                except (TypeError, ValueError):
                    started = 0.0
                if started > best_started:
                    best_started, best_key = started, key
        if best_key is None:
            return None
        meta = dict(_headless_meta[best_key])
        proc = _headless_runs.get(best_key)
    meta["running"] = bool(proc is not None and proc.poll() is None)
    return meta


def live_from_run(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Live reply text for a headless run: the CLI-noise-stripped tail of its run log."""
    text = ""
    try:
        from transplant import engine as _eng
        text = _eng._strip_cli_noise(_eng._log_tail(meta["log"], meta.get("offset") or 0))
    except Exception:
        text = ""
    return {"text": text, "user": meta.get("text") or "", "running": bool(meta.get("running"))}


def is_pending_launch(chat_id: str) -> bool:
    """True when chat_id is a launch not yet backed by any transcript (no registry: never)."""
    return False


def _continue_headless(source: str, chat_id: str, text: str, project_dir: str = "",
                       model: str = "", effort: str = "") -> Tuple[bool, str, Dict[str, Any]]:
    """Run `<cli> <resume> <sid> <text>` detached so the chat gets the message and replies in its own transcript."""
    import subprocess
    entry = HEADLESS_CONTINUE.get(source)
    if not entry:
        return False, f"no headless continue for {source or 'this source'}", {}
    cli, build = entry
    sid = chat_id.strip().lstrip("@")
    if sid.startswith(f"{source}-"):
        sid = sid[len(source) + 1:]
    run_key = f"{source}:{sid}"
    sentinel = _PendingRun()
    with _headless_lock:
        for cand in _handoff_aliases(sid):
            prev = _headless_runs.get(f"{source}:{cand}")
            if prev is not None and prev.poll() is None:
                return False, "still replying to the previous message", {"pid": prev.pid}
        _headless_runs[run_key] = sentinel
    try:
        binary = _find_headless_bin(cli)
        if not binary:
            return False, f"{cli} not found on PATH", {}
        log_dir = os.path.join(_s.SUBWAY_DIR, "chat_runs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"{source}-{sid}.log")
        kwargs: Dict[str, Any] = {"cwd": project_dir if project_dir and os.path.isdir(project_dir) else None,
                                  "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
        else:
            kwargs["start_new_session"] = True
        argv = _build_headless_cmd(source, binary, sid, text, model=model, effort=effort) if (model or effort) else build(binary, sid, text)
        argv = _tee_wrap(source, sid, argv, kwargs)
        offset = os.path.getsize(log_path) if os.path.exists(log_path) else 0
        with open(log_path, "ab") as log:
            proc = subprocess.Popen(argv, stdout=log, stderr=log, **kwargs)
        with _headless_lock:
            _headless_runs[run_key] = proc
            _record_headless_meta(run_key, proc, log_path, offset, text)
    finally:
        with _headless_lock:
            if _headless_runs.get(run_key) is sentinel:
                _headless_runs.pop(run_key, None)
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass
    else:
        try:
            with open(log_path, "rb") as f:
                f.seek(max(0, os.path.getsize(log_path) - 800))
                raw = f.read().decode("utf-8", "replace")
        except OSError:
            raw = ""
        tail = re.sub(r"\x1b\[[0-9;]*m", "", raw).strip()
        # A fast exit can be a finished short turn (fine) or a fatal startup
        # condition (not fine). Distinguish by the log: provider-auth failures
        # print the /login banner and die before producing any reply, and pi
        # exits 0 even then — so exit code alone never catches it (the HUD
        # reported "New Pi chat" success while every spawn died instantly).
        fatal = re.search(r"No API key found|log into a provider via OAuth", tail)
        if fatal:
            hint = tail[-300:]
            if "deepai" in tail:
                hint = ("the deepai provider is app-managed and does not exist in the bare "
                        "pi CLI — create the chat inside the DeepAI Code app, or pick a "
                        "provider with a real key (auth.json). Log: " + log_path)
            return False, "headless exited before any reply: " + hint, {"pid": proc.pid, "log": log_path}
        if proc.returncode != 0:
            return False, "headless exited: " + tail[-300:], {"pid": proc.pid, "log": log_path}
    return True, "headless", {"pid": proc.pid, "log": log_path, "offset": offset}



_HANDOFFS_FILE = "handoff_sessions.json"


def _deepai_headless_ready(target_model: str) -> Tuple[bool, Any]:
    """Gate + env for DeepAI-app models in HUD-started pi chats (settings: headless_deepai).

    The deepai provider exists only through the app's own local proxy (its
    models.json baseUrl, auth $T3_DEEPAI_TOKEN). Using that path bills exactly
    like in-app chats — 0-theo models stay free — because it IS the app's path.
    Returns (False, reason) when the toggle is off, the snapshot is missing, or
    the proxy is down (app closed): a loud failure, never a re-route to a
    billed path. Returns (True, env-dict) when ready to spawn.
    """
    from hud_server import settings_api as _sa
    try:
        enabled = _sa.value("headless_deepai")
    except Exception:
        enabled = False
    wants_deepai = str(target_model or "").strip().lower().startswith("deepai/")
    env_path = os.path.join(_s.SUBWAY_DIR, "app_env.json")
    snap = {}
    if os.path.isfile(env_path):
        try:
            data = json.load(open(env_path, "r", encoding="utf-8"))
            snap = data.get("env") if isinstance(data, dict) else {}
        except Exception:
            snap = {}
    snap = snap if isinstance(snap, dict) else {}
    if not wants_deepai and not enabled:
        return True, {}  # other providers never needed this
    if not enabled:
        if wants_deepai:
            return False, ("DeepAI models are app-managed; the bare pi CLI has no deepai provider. "
                           "Create the chat inside the DeepAI Code app, or enable "
                           "Settings → Chats → 'DeepAI models in HUD-started chats' "
                           "to use them through the app's own proxy (same billing as in-app).")
        return True, {}
    if not snap:
        return False, ("headless_deepai is on, but no app env snapshot exists yet. Open any DeepAI "
                       "app chat once (its wake extension snapshots the app env), then retry.")
    # Proxy health: the app's own models.json names the baseUrl; any HTTP answer means it's up.
    cfg_dir = ""
    try:
        from local_models import pi_config_dir as _pcd
        cfg_dir = _pcd()
    except Exception:
        cfg_dir = ""
    mjson = os.path.join(cfg_dir, "models.json") if cfg_dir else ""
    base = ""
    if mjson and os.path.isfile(mjson):
        try:
            data = json.load(open(mjson, "r", encoding="utf-8"))
            base = str(((data.get("providers") or {}).get("deepai") or {}).get("baseUrl") or "")
        except Exception:
            base = ""
    if base:
        import urllib.request as _ur
        try:
            _ur.urlopen(base, timeout=2.0).read(1)
        except Exception as e:
            if isinstance(e, _ur.HTTPError):
                pass  # the proxy answered — it's up
            else:
                return False, ("headless_deepai is on, but the DeepAI app's local proxy is not "
                               "reachable (" + base + "). Open the app, then retry.")
    return True, {k: str(v) for k, v in snap.items() if re.match(r"^(T3_|PI_|DEEPAI)", k)}


def _load_handoff_map() -> Dict[str, str]:
    path = os.path.join(_s.SUBWAY_DIR, _HANDOFFS_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_handoff_map(data: Dict[str, str]) -> None:
    path = os.path.join(_s.SUBWAY_DIR, _HANDOFFS_FILE)
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def record_real_session(source: str, launched_sid: str, real_id: str) -> None:
    """Map a launched (placeholder) session id to the provider's real conversation id."""
    if not launched_sid or not real_id:
        return
    try:
        data = _load_handoff_map()
        data[launched_sid] = real_id
        _save_handoff_map(data)
    except Exception:
        pass


def _get_chat_context_digest(chat_id: str, max_chars: int = 1600) -> str:
    try:
        from cli_tools import chats as _chats
        dig = _chats.t_read_chat(chat_id, mode="decisions", turns=6, max_chars=max_chars)
        if isinstance(dig, str) and dig and not dig.startswith("Error") and not dig.startswith("[ERROR]"):
            return dig.strip()[:max_chars]
    except Exception:
        pass
    try:
        dig = _s.t_read_chat(chat_id, mode="decisions", turns=6)
        if isinstance(dig, str) and dig and not dig.startswith("Error") and not dig.startswith("[ERROR]"):
            return dig.strip()[:max_chars]
    except Exception:
        pass
    return ""


def _tee_wrap(source: str, sid: str, argv: List[str], kwargs: Dict[str, Any]) -> List[str]:
    """Run a headless CLI through stream_tee so its answer reaches the live relay (key "<source>-<sid>",
    the same name as its chat_runs log) as it is written. Providers stream_tee can't read run unwrapped."""
    from transplant.stream_tee import supports as _tee_supports
    if not argv or not _tee_supports(source):
        return argv
    from subway_env import headless_env
    env = dict(kwargs.get("env") or headless_env())
    repo = os.path.dirname(os.path.abspath(__file__))
    env["PYTHONPATH"] = repo + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["SUBWAY_LIVE_KEY"] = f"{source}-{sid}"
    env["SUBWAY_LAUNCH_SID"] = sid
    if "SUBWAY_DIR" not in env:
        env["SUBWAY_DIR"] = os.environ.get("SUBWAY_DIR", getattr(_s, "SUBWAY_DIR", os.path.join(os.path.expanduser("~"), ".subway")))
    kwargs["env"] = env
    return [sys.executable, "-m", "transplant.stream_tee", source, "--"] + list(argv)


def _launch_handoff_session(
    target_provider: str,
    target_model: str = "",
    project_dir: str = "",
    seed_prompt: str = "",
    effort: str = "",
) -> Tuple[bool, str, Dict[str, Any]]:
    import uuid
    import subprocess
    clean_provider = target_provider.lower().strip()
    entry = HEADLESS_CONTINUE.get(clean_provider)
    if not entry:
        return False, f"target provider '{target_provider}' not supported for handoff", {}
    cli, build = entry
    binary = _find_headless_bin(cli)
    if not binary:
        return False, f"{cli} binary not found on PATH", {}

    hex_id = uuid.uuid4().hex[:8]
    if clean_provider in ("antigravity", "claude"):
        target_sid = str(uuid.uuid4())
    else:
        target_sid = f"subway-{hex_id}"

    target_cid = target_sid

    log_dir = os.path.join(_s.SUBWAY_DIR, "chat_runs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{clean_provider}-{target_sid}.log")
    kwargs: Dict[str, Any] = {
        "cwd": project_dir if project_dir and os.path.isdir(project_dir) else None,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True

    argv = _build_headless_cmd(
        clean_provider,
        binary,
        target_sid,
        seed_prompt,
        model=target_model,
        effort=effort,
        new_session=True,
    )
    extra_env = {}
    if clean_provider == "pi":
        ok_toggle, why = _deepai_headless_ready(target_model)
        if not ok_toggle:
            return False, why, {}
        extra_env = why or {}
    try:
        offset = os.path.getsize(log_path) if os.path.exists(log_path) else 0
        from subway_env import headless_env
        spawn_env = headless_env(kwargs.get("env"))
        if extra_env:
            spawn_env.update(extra_env)
        kwargs["env"] = spawn_env
        with open(log_path, "ab") as log:
            argv = _tee_wrap(clean_provider, target_sid, argv, kwargs)
            proc = subprocess.Popen(argv, stdout=log, stderr=log, **kwargs)
        with _headless_lock:
            _headless_runs[f"{clean_provider}:{target_sid}"] = proc
            _record_headless_meta(f"{clean_provider}:{target_sid}", proc, log_path, offset, seed_prompt)
    except Exception as e:
        return False, f"failed to launch {clean_provider}: {e}", {}

    # Fail loudly when the spawn dies of a fatal startup condition (e.g. the
    # app-managed deepai provider does not exist in the bare pi CLI). The
    # process exits 0 in that case, so success was reported while every
    # "New Pi chat" died instantly and the panel matched the dead tab to a
    # wrong chat (false history). A quick finished turn is still success.
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass
    else:
        try:
            with open(log_path, "rb") as f:
                f.seek(max(0, os.path.getsize(log_path) - 800))
                raw = f.read().decode("utf-8", "replace")
        except OSError:
            raw = ""
        tail = re.sub(r"\x1b\[[0-9;]*m", "", raw).strip()
        if re.search(r"No API key found|log into a provider via OAuth", tail):
            hint = tail[-300:]
            if "deepai" in tail:
                hint = ("the deepai provider is app-managed and does not exist in the bare "
                        "pi CLI — create the chat inside the DeepAI Code app, or pick a "
                        "provider with a real key (auth.json). Log: " + log_path)
            return False, "headless exited before any reply: " + hint, {"pid": proc.pid, "log": log_path}
        if proc.returncode != 0:
            return False, "headless exited: " + tail[-300:], {"pid": proc.pid, "log": log_path}

    return True, target_cid, {"pid": proc.pid, "log": log_path, "sid": target_sid, "offset": offset}


def send_to_chat(
    chat_id: str,
    text: str,
    source: str = "",
    model: str = "",
    effort: str = "",
    target_provider: str = "",
    swap: bool = False,
) -> Dict[str, Any]:
    """The HUD user's own text straight into a chat (never wrapped as fastmail)."""
    # Sessions the transplant engine made may have no chat reader: route them through the engine.
    try:
        from transplant import engine as _engine
        eng_root, eng_seg = _engine.session_segment(chat_id)
        if eng_seg is None and not swap:
            _root, _rec = _engine.conversation_for(chat_id)
            if _rec and (_rec.get("segments") or []):
                _active = _rec["segments"][-1]
                _cur_prov = str(_rec.get("current_provider") or _active.get("provider") or "").lower()
                _want = target_provider.strip().lower()
                if _active.get("method") == _engine.TRANSPLANT and (not _want or _want == _cur_prov):
                    eng_root, eng_seg = _root, _active
    except Exception:
        eng_root, eng_seg = None, None
    if eng_seg is not None:
        import conversation_swap
        eng_target = target_provider.strip().lower()
        if swap and eng_target and eng_target != eng_seg.get("provider"):
            return conversation_swap.swap_chat(None, eng_seg["session_id"], eng_seg.get("provider", ""), eng_target, text,
                                               model=model, effort=effort, project_dir=eng_seg.get("cwd", ""))
        if eng_target and eng_target != eng_seg.get("provider"):
            model, effort = "", ""
        return conversation_swap.continue_engine_session(eng_root, eng_seg, text, model=model, effort=effort)
    try:
        try:
            c = _s.resolve(chat_id, strict=True, source=source) if source else _s.resolve(chat_id, strict=True)
        except TypeError:
            c = _s.resolve(chat_id, strict=True)
    except Exception as e:
        return {"delivered": False, "method": "unresolved", "detail": str(e).splitlines()[0] if str(e) else "chat not found"}
    resolved_source = str(c.get("source") or source or "").lower()
    cid = str(c.get("id") or chat_id)
    project_dir = str(c.get("project_dir") or c.get("_project_path") or c.get("cwd") or c.get("project") or "")

    clean_target = target_provider.strip().lower()
    ignored = bool(clean_target and clean_target != resolved_source and not swap)
    if ignored:
        model = ""
        effort = ""
    moved = False
    if swap and clean_target == resolved_source:
        try:
            from transplant import engine as _engine
            _root, _rec = _engine.conversation_for(cid)
            moved = bool(_rec and len(_rec.get("segments") or []) > 1)
        except Exception:
            moved = False
    if swap and clean_target and (clean_target != resolved_source or moved):
        import conversation_swap
        return conversation_swap.swap_chat(c, cid, resolved_source, clean_target, text, model=model, effort=effort, project_dir=project_dir)
    deliverer = CHAT_DELIVERERS.get(resolved_source)
    def _with_note(ret: Dict[str, Any]) -> Dict[str, Any]:
        if ignored:
            ret["note"] = "target provider ignored: use Switch provider to move this conversation"
        return ret
    if deliverer is None:
        detail = f"{resolved_source or 'this'} chats have no live delivery path yet"
        return _with_note({"delivered": False, "ok": False, "method": "unsupported", "source": resolved_source,
                "detail": detail, "error": detail})
    try:
        if resolved_source == "antigravity":
            ok, method = deliverer(cid, text, project_dir)
        elif resolved_source == "pi":
            # Only enqueue to Pi's live spool if a Pi window is actually open; otherwise continue headless via pi --session.
            if is_online({"source": "pi", "chat_id": cid}):
                ok, method = _deliver_pi(cid, text, kind="chat")
            else:
                ok, method = False, "no live pi window"
        elif resolved_source == "claude" and claude_window_closed(cid, (c or {}).get("path", "")) and claude_headless_running(cid):
            busy = "A reply is still running in this chat; send again when it finishes"
            return _with_note({"delivered": False, "ok": False, "method": "busy", "source": resolved_source, "chat_id": cid,
                               "detail": busy, "error": busy})
        elif resolved_source == "claude" and claude_window_closed(cid, (c or {}).get("path", "")):
            # A push only lands in an inbox an open window drains; with no window it would sit there unanswered.
            ok, method = False, "no open window"
        else:
            ok, method = deliverer(cid, text)
            if ok and resolved_source == "claude" and not claude_waiter_alive(cid):
                method = "queued: the Claude tab is idle with no listener, it reads this on its next turn"
    except Exception as e:
        ok, method = False, f"error: {e}"
    if not ok and resolved_source in HEADLESS_CONTINUE:
        try:
            h_ok, h_method, info = _continue_headless(resolved_source, cid, text, project_dir, model=model, effort=effort)
        except Exception as e:
            h_ok, h_method, info = False, f"launch failed: {e}", {}
        if h_ok:
            return _with_note({"delivered": True, "ok": True, "method": "headless", "source": resolved_source, "chat_id": cid,
                    "pid": info.get("pid"), "detail": "continued headless (no open window)"})
        return _with_note({"delivered": False, "ok": False, "method": "headless", "source": resolved_source, "chat_id": cid,
                "detail": f"live: {method}; headless: {h_method}", "error": f"live: {method}; headless: {h_method}"})
    if not ok and resolved_source in CHAT_DELIVERERS and _DELIVERY_CAPABILITY.get(resolved_source) == "none":
        err = str(method) if method else f"{resolved_source} has no live input channel"
        if "has no live input channel" not in err:
            err = f"{resolved_source} has no live input channel: {err}"
        return _with_note({"delivered": False, "ok": False, "method": "unsupported", "source": resolved_source,
                "chat_id": cid, "detail": err, "error": err})
    return _with_note({"delivered": bool(ok), "method": method, "source": resolved_source, "chat_id": cid,
            "detail": "delivered" if ok else f"delivery failed via {method}"})
