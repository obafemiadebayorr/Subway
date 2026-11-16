import json
"""Fastmail per-line delivery router (P2).

Dispatches a fastmail message to the right wake path for the target's line.
Every _deliver_* returns (ok, method) and never raises.
"""
import os
import re
import sys
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple

import wake_driver
import server as _s

HOP_CAP = int(os.environ.get("SUBWAY_MAIL_MAX_HOPS", "3"))

# Per-source delivery capability reported by delivery_coverage().
_DELIVERY_CAPABILITY: Dict[str, str] = {
    "claude": "live",       # push, falls back to spool
    "antigravity": "live",  # connectrpc/gui/resume
    "opencode": "live",     # push to live pids when running
    "pi": "spool",          # spool only
    "copilot": "none",
    "cursor": "none",
    "devin": "none",
}


def _deliver_no_channel(source: str, reason: str) -> Callable[[str, str], Tuple[bool, str]]:
    def _fn(chat_id: str, text: str) -> Tuple[bool, str]:
        return False, f"{source} has no live input channel: {reason}"
    return _fn


def _deliver_claude(chat_id: str, text: str) -> Tuple[bool, str]:
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
_deliver_cursor = _deliver_no_channel("cursor", "Cursor conversations are read-only in Subway")
_deliver_devin = _deliver_no_channel("devin", "Devin sessions are read-only in Subway")


# Registry: detected chat source -> deliver(chat_id, text[, project_dir]) handler.
# send_to_chat derives "unsupported/no channel" from absence in this registry.
# Entries defer to module attributes so tests/patches can replace deliverers.
CHAT_DELIVERERS: Dict[str, Callable[..., Tuple[bool, str]]] = {
    "claude": lambda chat_id, text: _deliver_claude(chat_id, text),
    "antigravity": lambda chat_id, text, project_dir="": _deliver_antigravity(chat_id, text, project_dir),
    "opencode": lambda chat_id, text: _deliver_opencode(chat_id, text),
    "pi": lambda chat_id, text: _deliver_pi(chat_id, text),
    "copilot": lambda chat_id, text: _deliver_copilot(chat_id, text),
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
            mapping_dir = os.path.join(getattr(_s, "SUBWAY_DIR", os.path.expanduser("~/.subway")), "pi_sessions")
            try:
                return os.path.isdir(mapping_dir) and bool(os.listdir(mapping_dir))
            except OSError:
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


def _queue_result(detail: str = "queued") -> Dict[str, Any]:
    return {"delivered": False, "method": "queued", "detail": detail}


def _wire_text(msg: Dict[str, Any]) -> str:
    mid = str(msg.get("id") or "")
    sender = str(msg.get("from") or msg.get("from_chat") or "unknown")
    body = str(msg.get("text") or "")
    if not mid:
        return body
    return (f"[fast mail {mid} from {sender}]\n{body}\n\n"
            f"Reply with reply_note(msg_id=\"{mid}\", text=...).")


def deliver(msg: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
    msg = msg or {}
    target = target or {}
    source = str(target.get("source") or target.get("provider") or msg.get("source") or "").lower()
    tid = str(target.get("chat_id") or target.get("id") or target.get("to") or msg.get("to") or "")
    text = _wire_text(msg)
    mode = str(msg.get("mode") or "fast").lower()
    hops = int(msg.get("hops") or 0)
    project_dir = str(target.get("project_dir") or target.get("project") or "")

    if hops >= HOP_CAP:
        return _queue_result(f"hop cap ({hops} >= {HOP_CAP})")
    if mode != "fast":
        return _queue_result("mode=normal")

    try:
        if source == "worker":
            ok, method = _deliver_worker(tid, text)
            return {"delivered": bool(ok), "method": method, "detail": "delivered" if ok else f"delivery failed via {method}"}
        deliverer = CHAT_DELIVERERS.get(source)
        if deliverer is None:
            # copilot/local/custom/unknown or anything not in chat delivery registry → queue only
            return _queue_result(f"source={source or 'unknown'}")
        if source == "antigravity":
            ok, method = deliverer(tid, text, project_dir)
        else:
            ok, method = deliverer(tid, text)
    except Exception as e:
        return {"delivered": False, "method": "error", "detail": str(e)}
    # For read-only sources with no input channel, queue rather than pretending delivery.
    if not ok and _DELIVERY_CAPABILITY.get(source) == "none":
        return _queue_result(f"source={source} has no input channel")
    return {"delivered": bool(ok), "method": method, "detail": "delivered" if ok else f"delivery failed via {method}"}


def _find_headless_bin(cli: str) -> Optional[str]:
    import shutil
    if cli == "pi":
        wrapper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "pi-deep")
        if os.path.isfile(wrapper) and os.access(wrapper, os.X_OK):
            return wrapper
        return shutil.which("pi")
    if cli == "opencode":
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
_AGY_HEADLESS_SETTINGS = '{"permissions":{"allow":["mcp(subway/*)"]}}'


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
        cmd = [binary, "run", "--session", sid]
        ref = _opencode_model_ref(model) if model not in skip else ""
        if ref:
            cmd.extend(["-m", ref])
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
        cmd = [binary, "--session-id", sid, "-p"]
        if model not in skip:
            cmd.extend(["--model", model])
        if effort and effort not in ("auto", "none"):
            cmd.extend(["--thinking", effort])
        return cmd + [text]
    if source == "antigravity":
        cmd = [binary, "--conversation", sid, "--print", text, "--output-format", "stream-json", "--print-timeout", "0", "--settings", _AGY_HEADLESS_SETTINGS]
        if model not in skip:
            cmd.extend(["--model", model])
        if effort and effort not in ("auto", "none"):
            cmd.extend(["--effort", effort])
        return cmd
    return []


HEADLESS_CONTINUE = {
    "opencode": ("opencode", lambda b, sid, text: [b, "run", "--session", sid, text]),
    "claude": ("claude", lambda b, sid, text: [b, "-p", "--resume", sid, text]),
    "pi": ("pi", lambda b, sid, text: [b, "--session", sid, "-p", text]),
    "antigravity": ("antigravity", lambda b, sid, text: [b, "--conversation", sid, "--print", text, "--output-format", "stream-json", "--print-timeout", "0", "--settings", _AGY_HEADLESS_SETTINGS]),
}
_headless_runs: Dict[str, Any] = {}


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
    prev = _headless_runs.get(f"{source}:{sid}")
    if prev is not None and prev.poll() is None:
        return False, "still replying to the previous message", {"pid": prev.pid}
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
    if source in ("pi", "claude", "opencode"):
        argv = [sys.executable, "-m", "transplant.stream_tee", source, "--"] + argv
        env = os.environ.copy()
        repo = os.path.dirname(os.path.abspath(__file__))
        env["PYTHONPATH"] = repo + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        kwargs["env"] = env
    offset = os.path.getsize(log_path) if os.path.exists(log_path) else 0
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(argv, stdout=log, stderr=log, **kwargs)
    _headless_runs[f"{source}:{sid}"] = proc
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass
    else:
        if proc.returncode != 0:
            try:
                with open(log_path, "rb") as f:
                    f.seek(max(0, os.path.getsize(log_path) - 500))
                    raw = f.read().decode("utf-8", "replace")
            except OSError:
                raw = ""
            tail = re.sub(r"\x1b\[[0-9;]*m", "", raw).strip()[-300:]
            return False, "headless exited: " + tail, {"pid": proc.pid, "log": log_path}
    return True, "headless", {"pid": proc.pid, "log": log_path, "offset": offset}



_HANDOFFS_FILE = "handoff_sessions.json"


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
    try:
        offset = os.path.getsize(log_path) if os.path.exists(log_path) else 0
        with open(log_path, "ab") as log:
            proc = subprocess.Popen(argv, stdout=log, stderr=log, **kwargs)
        _headless_runs[f"{clean_provider}:{target_sid}"] = proc
    except Exception as e:
        return False, f"failed to launch {clean_provider}: {e}", {}

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
        else:
            ok, method = deliverer(cid, text)
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
