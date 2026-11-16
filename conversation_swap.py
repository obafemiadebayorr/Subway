"""Swap a conversation's provider (roadmap items 72-73): the history is transplanted into the new
provider's native session (transplant/engine.py), with a primer launch as fallback. Only an explicit
swap calls this; a plain chat send never does."""
from __future__ import annotations
import os
import subprocess
from typing import Any, Dict, List, Optional

MIN_TURNS, MAX_TURNS = 5, 12
DEFAULT_CONTEXT_TOKENS = 128000


def turns_for(budget_chars: int) -> int:
    """5-12 exchanges sized to the budget (~1500 chars per decisions exchange)."""
    return max(MIN_TURNS, min(MAX_TURNS, int(budget_chars) // 1500))


def budget_chars(context_tokens: int = DEFAULT_CONTEXT_TOKENS) -> int:
    """About 15% of the target's context window, in chars (4 chars/token), capped at 24000."""
    return max(6000, min(24000, int(context_tokens * 0.15 * 4)))


def git_header(project_dir: str) -> str:
    """Branch, last 3 commits and uncommitted diffstat, so the new provider reads disk, not transcript."""
    if not project_dir:
        return ""
    def run(*args: str) -> str:
        try:
            r = subprocess.run(["git", "-C", project_dir, *args], capture_output=True, text=True, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""
    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    if not branch:
        return ""
    log = run("log", "--oneline", "-3")
    stat = "\n".join(run("diff", "--stat", "HEAD").splitlines()[-15:])
    parts = [f"Branch: {branch}"]
    if log:
        parts.append("Recent commits:\n" + log)
    parts.append("Uncommitted changes:\n" + (stat or "(none)"))
    return "\n".join(parts)


def context_digest(chat_id: str, since_last: bool = False, context_tokens: int = DEFAULT_CONTEXT_TOKENS) -> str:
    """Decisions digest of the last 5-12 whole exchanges (read_chat slices by exchange, never mid-tool-loop).
    since_last=True (switching back) returns only what happened since this reader's last read."""
    limit = budget_chars(context_tokens)
    try:
        from cli_tools import chats as _chats
        kw: Dict[str, Any] = {"mode": "decisions", "turns": turns_for(limit), "max_chars": limit}
        if since_last:
            kw["since"] = "last"
        dig = _chats.t_read_chat(chat_id, **kw)
        if isinstance(dig, str) and dig and not dig.startswith(("Error", "[ERROR]")):
            return dig.strip()[:limit]
    except Exception:
        pass
    return ""


def handoff_text(from_source: str, cid: str, text: str, project_dir: str, since_last: bool = False) -> str:
    parts = []
    dig = context_digest(cid, since_last=since_last)
    hdr = git_header(project_dir)
    if dig or hdr:
        block = f"[SUBWAY CONTEXT HANDOFF from {from_source} ({cid})]\n"
        if hdr:
            block += hdr + "\n\n"
        if dig:
            block += ("What happened since you last had this conversation:\n" if since_last else "Recent decisions and summary:\n") + dig + "\n"
        parts.append(block + "----------------------------------------")
    parts.append(f"User message: {text}")
    return "\n\n".join(parts)


def _set_principal(c: Optional[dict], cid: str, target_cid: str, note: str) -> None:
    try:
        import thread_state
        thread_id = str((c or {}).get("thread_root") or (c or {}).get("thread") or cid)
        thread_state.set_principal(thread_id, target_cid, note=note, brief="auto")
    except Exception:
        pass


def _resolve(chat_id: str, source: str = "") -> Optional[dict]:
    try:
        import server as _s
        try:
            return _s.resolve(chat_id, strict=True, source=source) if source else _s.resolve(chat_id, strict=True)
        except TypeError:
            return _s.resolve(chat_id, strict=True)
    except Exception:
        return None


def full_turns(rec: dict, c: Optional[dict]) -> List[dict]:
    """Every readable turn of a conversation across its segments, oldest first.

    Native and primer segments are read from their own store; transplanted segments (which may
    have no reader) contribute the messages sent into them plus the replies from their run logs."""
    from transplant import engine, ir
    turns: List[dict] = []
    for seg in rec.get("segments") or []:
        if seg.get("method") != engine.TRANSPLANT:
            chat = c if c and str(c.get("id") or "") == seg.get("session_id") else _resolve(seg["session_id"], seg.get("provider", ""))
            if chat:
                try:
                    turns.extend(ir.from_chat(chat).get("turns") or [])
                except Exception:
                    pass
        else:
            turns.extend(engine.sent_turns(seg))
    turns.sort(key=lambda t: float(t.get("ts") or 0))
    return turns


def _result(ok: bool, provider: str, chat_id: str, method: str, detail: str = "", note: str = "", **more) -> Dict[str, Any]:
    out = {"ok": ok, "delivered": ok, "via": "handoff", "provider": provider, "chat_id": chat_id,
           "handoff_chat_id": chat_id, "method": method, "detail": detail, "note": note}
    if not ok:
        out["error"] = detail or note or "swap failed"
    out.update(more)
    return out


def _readback_settings():
    """(exchanges, full) for a provider move, from settings; 12 / False when unreadable."""
    try:
        from hud_server import settings_api
        turns = int(settings_api.value("transplant_turns") or 12)
        full = bool(settings_api.value("transplant_full"))
    except Exception:
        return 12, False
    return max(5, min(20, turns)), full


def swap_chat(c: Optional[dict], cid: str, resolved_source: str, clean_target: str, text: str, model: str = "", effort: str = "", project_dir: str = "") -> Dict[str, Any]:
    """Move conversation cid (current provider resolved_source) to clean_target and send text there.

    First visit to a provider: transplant the recent history into its native store and resume it
    (primer launch when the provider isn't mapped or its probe fails). A provider the conversation
    already visited: send only what happened since, into that same session."""
    import fastmail_lines as fl
    from transplant import engine

    root, rec = engine.conversation_for(cid)
    if rec is None:
        root, rec = engine.start_conversation(cid, resolved_source, project_dir)
    project_dir = project_dir or rec.get("project_dir") or ""
    if project_dir and not os.path.isdir(project_dir):
        alt = str((c or {}).get("_project_path") or (c or {}).get("cwd") or "")
        if alt and os.path.isdir(alt):
            project_dir = alt
    hdr = git_header(project_dir)
    target = engine.segment_for(rec, clean_target)
    if target is not None and engine.segment_full(target, model):
        target = None  # near its context limit: transplant into a fresh session instead of appending

    if target and target is engine.current_segment(rec):
        # Already there (e.g. a second send from the old tab): plain message, no repeated delta.
        res = _send_to_segment(c, cid, root, target, text, resolved_source, clean_target, model, effort, project_dir, 0)
        if res is not None:
            return res
        rec, target = engine.conversation_for(root)[1] or rec, None
    if target:
        since = float(target.get("end") or target.get("start") or 0)
        delta_turns = [t for t in full_turns(rec, c) if float(t.get("ts") or 0) > since and t.get("provider") != clean_target]
        delta = engine.delta_text(delta_turns)
        away = sorted({str(t.get("provider") or "") for t in delta_turns if t.get("provider")}) or [resolved_source]
        block = f"[SUBWAY: this conversation continued on {', '.join(away)} while you were away]\n"
        if hdr:
            block += hdr + "\n\n"
        block += ("What happened since you last had it:\n" + delta + "\n") if delta else "(no new turns)\n"
        msg = block + "----------------------------------------\n\nUser message: " + text
        res = _send_to_segment(c, cid, root, target, msg, resolved_source, clean_target, model, effort, project_dir, len(delta_turns))
        if res is not None:
            return res
        rec = engine.conversation_for(root)[1] or rec  # stale session dropped: start a new one below

    conv = {"id": root, "source": resolved_source, "project_dir": project_dir,
            "title": str((c or {}).get("title") or ""), "turns": full_turns(rec, c), "git": {}}
    preface = f"[SUBWAY: this conversation moved here from {resolved_source}. Read the repo for current state.]"
    if hdr:
        preface += "\n" + hdr
    turns, full = _readback_settings()
    res = engine.transplant(c, clean_target, text, model=model, effort=effort, project_dir=project_dir,
                            preface=preface, root=root, finder=fl._find_headless_bin, conv=conv, turns=turns, full=full)
    if res.get("method") == engine.TRANSPLANT:
        if res.get("ok"):
            _set_principal(c, cid, res["chat_id"], f"Cross-provider transplant from {resolved_source} to {clean_target}")
        return _result(bool(res.get("ok")), clean_target, res["chat_id"], "transplant", res.get("detail", ""),
                       note=res.get("note", ""), exchanges=res.get("exchanges"))

    # Primer: the provider isn't mapped (or its probe failed); start a new session with context in the prompt.
    seed_prompt = handoff_text(resolved_source, cid, text, project_dir)
    ok, target_cid, info = fl._launch_handoff_session(clean_target, target_model=model, project_dir=project_dir,
                                                      seed_prompt=seed_prompt, effort=effort)
    if not ok:
        return _result(False, clean_target, cid, "primer", f"cross-provider takeover launch failed: {target_cid}",
                       note=res.get("note", ""))
    engine.add_segment(root, clean_target, target_cid, target_cid, engine.PRIMER, reason=res.get("reason", ""))
    engine.note_sent(root, target_cid, text, (info or {}).get("log") or "", (info or {}).get("offset") or 0)
    _set_principal(c, cid, target_cid, f"Cross-provider takeover from {resolved_source} to {clean_target}")
    return _result(True, clean_target, target_cid, "primer", f"handed off to {clean_target}", note=res.get("note", ""))


def _send_to_segment(c, cid, root, target, msg, resolved_source, clean_target, model, effort, project_dir, n_delta):
    """Deliver into an existing segment. None when its session is gone (segment dropped)."""
    import time
    import fastmail_lines as fl
    from transplant import engine
    started = time.time()
    if target.get("method") == engine.TRANSPLANT:
        res = engine.continue_segment(root, target, msg, cwd=project_dir, model=model, effort=effort, finder=fl._find_headless_bin)
        ok, detail, method = bool(res.get("ok")), res.get("detail", ""), "switch_back"
    elif clean_target in fl.HEADLESS_CONTINUE and not fl.is_online({"source": clean_target, "chat_id": target["session_id"]}):
        # No open window: a live push would only queue. Resume the session headless so it replies now.
        h_ok, h_detail, _info = fl._continue_headless(clean_target, target["session_id"], msg, project_dir, model=model, effort=effort)
        if not h_ok and target.get("method") != engine.NATIVE and "not found" in h_detail.lower():
            engine.drop_segment(root, target["session_id"])
            return None
        ok, detail, method = h_ok, h_detail, "headless"
        if h_ok and target.get("method") != engine.NATIVE and (_info or {}).get("log"):
            engine.note_sent(root, target["session_id"], msg, _info["log"], _info.get("offset") or 0)
    else:
        res = fl.send_to_chat(target["session_id"], msg, source=clean_target, model=model, effort=effort)
        if res.get("method") == "unresolved" and target.get("method") != engine.NATIVE:
            engine.drop_segment(root, target["session_id"])
            return None
        ok, detail, method = bool(res.get("delivered") or res.get("ok")), res.get("detail", ""), res.get("method", "switch_back")
    if ok:
        engine.activate(root, target["session_id"], at=started)
        _set_principal(c, cid, target["session_id"], f"Cross-provider switch-back from {resolved_source} to {clean_target}")
    return _result(ok, clean_target, target["session_id"], method, detail,
                   note=f"back on its {clean_target} session, {n_delta} new turns" if n_delta else "")


def continue_engine_session(root: str, seg: dict, text: str, model: str = "", effort: str = "") -> Dict[str, Any]:
    """A plain send into a session the engine made (it may have no chat reader)."""
    import fastmail_lines as fl
    from transplant import engine
    _r, rec = engine.conversation_for(seg["session_id"])
    cwd = seg.get("cwd") or (rec or {}).get("project_dir") or ""
    res = engine.continue_segment(root, seg, text, cwd=cwd, model=model, effort=effort, finder=fl._find_headless_bin)
    return _result(bool(res.get("ok")), seg.get("provider", ""), seg["session_id"], "headless", res.get("detail", ""),
                   source=seg.get("provider", ""), pid=res.get("pid"))


def swap(chat_id: str, to_provider: str, text: str = "", model: str = "", effort: str = "") -> Dict[str, Any]:
    import fastmail_lines as fl
    return fl.send_to_chat(chat_id, text or "Continue this conversation from where it left off.", target_provider=to_provider, model=model, effort=effort, swap=True)
