"""Swap a conversation's provider (roadmap items 72-73): the history is transplanted into the new
provider's native session (transplant/engine.py), with a primer launch as fallback. Only an explicit
swap calls this; a plain chat send never does."""
from __future__ import annotations
import os
import subprocess
from typing import Any, Dict, List, Optional

MIN_TURNS, MAX_TURNS = 5, 12
DEFAULT_CONTEXT_TOKENS = 128000

# A1: provider turn timestamps can trail the host clock (remote ISO strings, ms epochs).
# The away-delta keeps this much grace on its left edge so recent turns are not silently
# dropped as "(no new turns)"; worst case it re-includes a few turns from just before the
# switch, which is far cheaper than losing them.
_DELTA_SKEW_GRACE = 90.0


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
        if seg.get("method") not in (engine.TRANSPLANT, engine.PRIMER):
            chat = c if c and str(c.get("id") or "") == seg.get("session_id") else _resolve(seg["session_id"], seg.get("provider", ""))
            if chat:
                try:
                    turns.extend(ir.from_chat(chat).get("turns") or [])
                except Exception:
                    pass
        else:
            turns.extend(engine.sent_turns(seg))
    turns.sort(key=lambda t: float(t.get("ts") or 0))
    return _dedupe_turns(turns)


def _count_exchanges(turns: List[dict]) -> int:
    """Count logical exchanges (user-initiated turns) in a turn list."""
    return sum(1 for t in turns if t.get("role") == "user")


def _trim_to_user_boundary(turns: List[dict], k: int) -> List[dict]:
    """Keep at most the last k logical exchanges, starting on role=='user' with no orphaned assistant turns."""
    if not turns or k <= 0:
        return []
    user_indices = [i for i, t in enumerate(turns) if t.get("role") == "user"]
    if not user_indices:
        return []
    start = user_indices[-k] if len(user_indices) > k else user_indices[0]
    return turns[start:]


def _dedupe_turns(turns: List[dict]) -> List[dict]:
    """A8: a turn recorded in both a native store and an engine segment survives the
    ts-sort as a duplicate. Drop exact repeats (provider + ts + text head) keeping the first."""
    seen = set()
    out: List[dict] = []
    for t in turns:
        key = (str(t.get("provider") or ""), str(t.get("ts") or ""),
               str(t.get("text") or t.get("content") or "")[:200])
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def tail_turns(rec: dict, c: Optional[dict], k: int, full: bool = False) -> List[dict]:
    """Collect up to the last k logical exchanges across segments, oldest first.

    When full=True, delegates to full_turns(rec, c). Otherwise walks segments backward
    reading only turns=k_remaining from native stores when a reader exists, falling back
    to engine.sent_turns(seg) only when no reader exists."""
    if full:
        return full_turns(rec, c)
    k = int(k or 0)
    if k <= 0:
        return []

    from transplant import engine, ir
    try:
        from cli_tools import chats as _chats
    except Exception:
        _chats = None

    collected: List[dict] = []
    for seg in reversed(rec.get("segments") or []):
        k_remaining = k - _count_exchanges(collected)
        if k_remaining <= 0:
            break
        seg_turns: List[dict] = []
        if seg.get("method") not in (engine.TRANSPLANT, engine.PRIMER):
            sid = seg.get("session_id") or ""
            prov = seg.get("provider", "")
            got_reader = False
            if _chats is not None and hasattr(_chats, "chat_turns") and sid:
                try:
                    raw = _chats.chat_turns(sid, source=prov, turns=k_remaining)
                    extracted = raw.get("turns") if isinstance(raw, dict) else raw
                    if extracted:
                        seg_turns = list(extracted)
                        got_reader = True
                except Exception:
                    pass
            if not got_reader:
                chat = c if c and str(c.get("id") or "") == sid else _resolve(sid, prov)
                if chat:
                    try:
                        seg_turns = list(ir.from_chat(chat).get("turns") or [])
                        got_reader = True
                    except Exception:
                        pass
            if not got_reader:
                seg_turns = list(engine.sent_turns(seg))
        else:
            seg_turns = list(engine.sent_turns(seg))
        if seg_turns:
            collected = seg_turns + collected

    collected.sort(key=lambda t: float(t.get("ts") or 0))
    return _trim_to_user_boundary(_dedupe_turns(collected), k)


def _estimate_tokens(turns: List[dict]) -> int:
    """Rough token estimate (~4 chars/token) for a list of IR turns."""
    total_chars = 0
    for t in turns:
        total_chars += len(str(t.get("text") or t.get("content") or ""))
    return total_chars // 4


def build_handoff_stats(
    source_conv: Optional[dict],
    trimmed: Optional[Any],
    target_provider: str,
    k: int,
    full: bool,
    method: str = "transplant",
) -> Dict[str, Any]:
    """Build handoff telemetry stats summarizing extraction, trimming, and pipeline stages."""
    src_turns = list((source_conv or {}).get("turns") or [])
    if isinstance(trimmed, dict):
        trim_turns = list(trimmed.get("turns") or [])
    else:
        trim_turns = list(trimmed or [])

    total_turns = _count_exchanges(src_turns) or len(src_turns)
    active_turns = _count_exchanges(trim_turns) or len(trim_turns)
    archived_turns = max(0, total_turns - active_turns)
    if total_turns == 0:
        slice_range = [0, 0]
    elif full:
        slice_range = [1, total_turns]
    else:
        slice_range = [archived_turns + 1, total_turns]

    raw_tokens = _estimate_tokens(src_turns)
    normalized_tokens = _estimate_tokens(trim_turns)
    reduction_pct = round((1.0 - normalized_tokens / raw_tokens) * 100.0, 1) if raw_tokens > 0 else 0.0

    return {
        "target_provider": target_provider,
        "method": method,
        "k": k,
        "full": bool(full),
        "total_turns": total_turns,
        "active_turns": active_turns,
        "archived_turns": archived_turns,
        "slice_range": slice_range,
        "raw_tokens": raw_tokens,
        "normalized_tokens": normalized_tokens,
        "reduction_pct": reduction_pct,
        "stages": ["extract", "align", "sanitize", "compile", "hydrate"],
    }


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
    import provider_lines as fl
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

    cur = engine.current_segment(rec)
    if target and cur and target.get("session_id") == cur.get("session_id") and target.get("provider") == cur.get("provider"):
        # Already there (e.g. a second send from the old tab): plain message, no repeated delta.
        res = _send_to_segment(c, cid, root, target, text, resolved_source, clean_target, model, effort, project_dir, 0)
        if res is not None:
            return res
        rec, target = engine.conversation_for(root)[1] or rec, None
    if target:
        # A1: for a resumed target, `end` is None — use the timestamp of when this segment
        # was last deactivated (last_end), so the away-delta covers only the time away
        # instead of re-sending the whole segment history. Clock-skew grace: provider
        # timestamps can trail the host clock, so keep a small window on the left edge
        # rather than silently dropping recent turns as "(no new turns)".
        since = float(target.get("end") or target.get("last_end") or target.get("start") or 0)
        since -= _DELTA_SKEW_GRACE
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

    turns, full = _readback_settings()
    selected_turns = tail_turns(rec, c, turns, full=full)
    conv = {"id": root, "source": resolved_source, "project_dir": project_dir,
            "title": str((c or {}).get("title") or ""), "turns": selected_turns, "git": {}}
    preface = f"[SUBWAY: this conversation moved here from {resolved_source}. Read the repo for current state.]"
    if hdr:
        preface += "\n" + hdr
    res = engine.transplant(c, clean_target, text, model=model, effort=effort, project_dir=project_dir,
                            preface=preface, root=root, finder=fl._find_headless_bin, conv=conv, turns=turns, full=full)
    handoff_stats = build_handoff_stats(conv, selected_turns, clean_target, turns, full, method=res.get("method") or "transplant")
    if res.get("method") == engine.TRANSPLANT:
        if res.get("ok"):
            _set_principal(c, cid, res["chat_id"], f"Cross-provider transplant from {resolved_source} to {clean_target}")
        return _result(bool(res.get("ok")), clean_target, res["chat_id"], "transplant", res.get("detail", ""),
                       note=res.get("note", ""), exchanges=res.get("exchanges"), handoff_stats=handoff_stats)

    # Primer: the provider isn't mapped (or its probe failed); start a new session with context in the prompt.
    seed_prompt = handoff_text(resolved_source, cid, text, project_dir)
    ok, target_cid, info = fl._launch_handoff_session(clean_target, target_model=model, project_dir=project_dir,
                                                      seed_prompt=seed_prompt, effort=effort)
    if not ok:
        return _result(False, clean_target, cid, "primer", f"cross-provider takeover launch failed: {target_cid}",
                       note=res.get("note", ""), handoff_stats=handoff_stats)
    engine.add_segment(root, clean_target, target_cid, target_cid, engine.PRIMER, reason=res.get("reason", ""))
    engine.note_sent(root, target_cid, text, (info or {}).get("log") or "", (info or {}).get("offset") or 0)
    _set_principal(c, cid, target_cid, f"Cross-provider takeover from {resolved_source} to {clean_target}")
    return _result(True, clean_target, target_cid, "primer", f"handed off to {clean_target}",
                   note=res.get("note", ""), handoff_stats=handoff_stats)


def preview_swap(chat_id: str, to_provider: str = "") -> Dict[str, Any]:
    """Read-only dry run of Stages 1-4 for chat_id -> to_provider without launching any CLI."""
    from transplant import engine

    turns, full = _readback_settings()
    c = _resolve(chat_id, "")
    resolved_source = str((c or {}).get("source") or (c or {}).get("provider") or "")
    root, rec = engine.conversation_for(chat_id)
    if rec is None:
        rec = {
            "id": root or chat_id,
            "segments": [{"session_id": chat_id, "provider": resolved_source, "method": "native"}],
        }
    project_dir = str(rec.get("project_dir") or (c or {}).get("_project_path") or (c or {}).get("cwd") or "")
    selected_turns = tail_turns(rec, c, turns, full=full)
    all_turns = full_turns(rec, c) if not full else selected_turns
    source_conv = {
        "id": root or chat_id,
        "source": resolved_source,
        "project_dir": project_dir,
        "title": str((c or {}).get("title") or ""),
        "turns": all_turns,
        "git": {},
    }
    stats = build_handoff_stats(source_conv, selected_turns, to_provider, turns, full, method="transplant")
    return {
        "ok": True,
        "chat_id": chat_id,
        "to_provider": to_provider,
        "handoff_stats": stats,
        "turns": selected_turns,
    }


def _send_to_segment(c, cid, root, target, msg, resolved_source, clean_target, model, effort, project_dir, n_delta):
    """Deliver into an existing segment. None when its session is gone (segment dropped)."""
    import time
    import provider_lines as fl
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


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fork_cut(turns: List[dict], cut_ts, tid: str = "") -> List[dict]:
    """Turns up to and including the fork turn, cut by position.

    The turn whose id matches `tid` wins; otherwise the last turn with a numeric ts <= cut_ts. Turns
    after the cut are dropped even when they share its ts or have no ts."""
    if tid:
        try:
            from cli_tools.chats import turn_id
            for i in range(len(turns) - 1, -1, -1):
                t = turns[i]
                if turn_id(t.get("role", ""), t.get("ts"), t.get("text") or "") == tid:
                    return turns[:i + 1]
        except Exception:
            pass
    cut = _num(cut_ts)
    if cut is None:
        return []
    idx = -1
    for i, t in enumerate(turns):
        ts = _num(t.get("ts"))
        if ts is not None and ts <= cut:
            idx = i
    return turns[:idx + 1]


def fork_chat(c, cid, resolved_source, target, text, cut_ts, tid="", model="", effort="", project_dir="") -> dict:
    """Start a real new chat carrying this conversation up to cut_ts (inclusive)."""
    import provider_lines as fl
    from transplant import engine

    root, rec = engine.conversation_for(cid)
    if rec is None:  # read-only view of the plain chat; nothing is persisted unless the fork launches
        rec = {"segments": [{"session_id": cid, "provider": resolved_source, "method": "native"}]}
    project_dir = project_dir or (rec or {}).get("project_dir") or str((c or {}).get("_project_path") or (c or {}).get("cwd") or "")
    turns = fork_cut(full_turns(rec, c), cut_ts, tid)
    if not turns:
        return _result(False, target, cid, "fork", "nothing to fork before that turn")

    hdr = git_header(project_dir)
    preface = (f"[SUBWAY: this chat is a FORK of a {resolved_source} conversation, branched at an earlier turn. "
               "Read the repo for current state; later turns of the original are not included.]")
    if hdr:
        preface += "\n" + hdr

    fid = engine.fork_conversation(cid, tid, target)
    conv = {"id": fid, "source": resolved_source, "project_dir": project_dir,
            "title": str((c or {}).get("title") or ""), "turns": turns, "git": {}}
    turns_k, full = _readback_settings()
    res = engine.transplant(c, target, text, model=model, effort=effort, project_dir=project_dir,
                            preface=preface, root=fid, finder=fl._find_headless_bin, conv=conv, turns=turns_k, full=full)
    if res.get("method") == engine.TRANSPLANT and res.get("ok"):
        if engine.conversation_for(res.get("chat_id", ""))[0] is None:
            engine.add_segment(fid, target, res["chat_id"], res["chat_id"], engine.TRANSPLANT)
        return _result(True, target, res["chat_id"], "transplant", res.get("detail", ""),
                       note=res.get("note", ""), fork_id=fid, exchanges=res.get("exchanges"))

    # Primer fallback: seed with full context up to the fork point.
    seed = preface + "\n\nConversation so far:\n" + engine.delta_text(turns) + "\n\n----------------------------------------\n\nUser message: " + text
    ok, target_cid, info = fl._launch_handoff_session(target, target_model=model, project_dir=project_dir,
                                                      seed_prompt=seed, effort=effort)
    if not ok:
        engine.discard_fork(fid)
        return _result(False, target, cid, "primer", f"fork launch failed: {target_cid}")
    engine.add_segment(fid, target, target_cid, target_cid, engine.PRIMER, reason=res.get("reason", ""))
    engine.note_sent(fid, target_cid, text, (info or {}).get("log") or "", (info or {}).get("offset") or 0)
    return _result(True, target, target_cid, "primer", res.get("detail", ""),
                   note=res.get("note", ""), fork_id=fid)


def continue_engine_session(root: str, seg: dict, text: str, model: str = "", effort: str = "") -> Dict[str, Any]:
    """A plain send into a session the engine made (it may have no chat reader)."""
    import provider_lines as fl
    from transplant import engine
    _r, rec = engine.conversation_for(seg["session_id"])
    cwd = seg.get("cwd") or (rec or {}).get("project_dir") or ""
    res = engine.continue_segment(root, seg, text, cwd=cwd, model=model, effort=effort, finder=fl._find_headless_bin)
    return _result(bool(res.get("ok")), seg.get("provider", ""), seg["session_id"], "headless", res.get("detail", ""),
                   source=seg.get("provider", ""), pid=res.get("pid"))


def swap(chat_id: str, to_provider: str, text: str = "", model: str = "", effort: str = "") -> Dict[str, Any]:
    import provider_lines as fl
    return fl.send_to_chat(chat_id, text or "Continue this conversation from where it left off.", target_provider=to_provider, model=model, effort=effort, swap=True)
