"""Fastmail broadcast — "ask the room" (P6).

Fan one question out to the active chats in this project, collect the replies,
and return a digest. Replies to a broadcast are never re-broadcast and never
wake a third party.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List

import chat_readers
import fastmail
import server as _s

_cond = threading.Condition()


def _settings() -> Dict[str, Any]:
    cfg = _s._load_json("config.json") or {}
    mail = cfg.get("mail") if isinstance(cfg.get("mail"), dict) else {}

    def _val(key: str, default: Any) -> Any:
        v = mail.get(key, default)
        try:
            from hud_server import settings_api as _settings_api
            v = _settings_api.value(f"mail.{key}", cfg)
            if v is None:
                v = mail.get(key, default)
        except Exception:
            pass
        return v

    try:
        cap = int(_val("broadcast_max_targets", 8))
    except (TypeError, ValueError):
        cap = 8
    muted = str(_val("muted_senders", "") or "")
    return {"broadcast_max_targets": max(0, cap),
            "muted_senders": [m.strip() for m in muted.split(",") if m.strip()]}


def _muted_ids() -> set:
    return set(_settings()["muted_senders"])


def targets(project: str = "", caller_id: str = "") -> List[Dict[str, Any]]:
    try:
        chats = chat_readers.all_chats(source="all", include_subagents=False)
    except Exception:
        chats = []
    me = caller_id or str((_s.detect_self() or {}).get("id") or "")
    if not project:
        try:
            det = _s.detect_self() or {}
            names = chat_readers._project_names(det.get("project"))
        except Exception:
            names = []
    else:
        names = chat_readers._project_names(project)
    muted = _muted_ids()
    out: List[Dict[str, Any]] = []
    for c in chats:
        cid = str(c.get("id") or "")
        if not cid or cid == me:
            continue
        if c.get("subagent"):
            continue
        if cid in muted or any(cid.startswith(m) or m.startswith(cid) for m in muted):
            continue
        if names:
            cnames = chat_readers._project_names(c.get("project"))
            if not any(n in names for n in cnames):
                continue
        out.append(c)
    out.sort(key=lambda c: str(c.get("updated") or ""), reverse=True)
    return out


def send(text: str, targets: List[Dict[str, Any]], mode: str = "normal",
         project: str = "") -> Dict[str, Any]:
    import fastmail_lines
    cap = _settings()["broadcast_max_targets"]
    skipped = max(0, len(targets) - cap)
    chosen = list(targets)[:cap]
    det = _s.detect_self() or {}
    sender = _s._short_id(det["id"]) if det.get("id") else _s._self_id()
    root_id = None
    sent = []
    for t in chosen:
        target = {"to": t.get("id"), "id": t.get("id"), "chat_id": t.get("id"),
                  "source": t.get("source") or t.get("provider") or "",
                  "project": t.get("project") or ""}
        msg = fastmail.create_message(target, text, from_chat=sender,
                                      mode=(mode or "normal"))
        if root_id is None:
            root_id = msg["id"]
        msg["broadcast_id"] = root_id
        msg["thread_id"] = root_id
        # A message that is itself a reply to a broadcast is never re-broadcast.
        ensure_not_broadcast_reply(msg)
        fastmail.save_message(msg)
        try:
            result = fastmail_lines.deliver(msg, target)
            if result.get("delivered"):
                fastmail.set_state(msg["id"], "delivered", result.get("method") or "")
        except Exception:
            pass
        sent.append(msg)
    return {"broadcast_id": root_id or "", "messages": sent,
            "targets": chosen, "skipped": skipped, "sender": sender}


def ensure_not_broadcast_reply(msg: Dict[str, Any]) -> None:
    """Hard guard: a reply to a broadcast is collected, never re-broadcast or woken."""
    if not msg:
        return
    reply_to = msg.get("reply_to") or msg.get("parent")
    if not reply_to:
        return
    parent = fastmail.load_message(reply_to) if isinstance(reply_to, str) else None
    parent_broadcast = bool(parent and (parent.get("broadcast_id") or parent.get("thread_id") == parent.get("id")))
    if parent_broadcast:
        raise ValueError("broadcast replies are never re-broadcast")


def notify_activity() -> None:
    with _cond:
        _cond.notify_all()


def _replies_for(broadcast_id: str) -> List[Dict[str, Any]]:
    out = []
    try:
        msgs = fastmail.list_messages(limit=500)
    except Exception:
        return out
    for m in msgs:
        if m.get("thread_id") == broadcast_id and m.get("reply_to") and not m.get("broadcast_id"):
            out.append(m)
    return out


def collect(broadcast_id: str, timeout: float) -> List[Dict[str, Any]]:
    deadline = time.monotonic() + max(0.0, float(timeout))
    with _cond:
        while True:
            replies = _replies_for(broadcast_id)
            try:
                total = len([m for m in fastmail.list_messages(limit=500)
                             if m.get("broadcast_id") == broadcast_id])
            except Exception:
                total = 0
            if total and len({str(r.get("from")) for r in replies}) >= total:
                return replies
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _replies_for(broadcast_id)
            _cond.wait(remaining)


def digest(broadcast_id: str, replies: List[Dict[str, Any]], skipped: int) -> str:
    try:
        msgs = [m for m in fastmail.list_messages(limit=500) if m.get("broadcast_id") == broadcast_id]
    except Exception:
        msgs = []
    total = len(msgs)
    names = []
    for m in msgs:
        to = str(m.get("to") or "")
        names.append(to[:8] if to else "?")
    seen = set()
    uniq_replies = []
    for r in replies:
        key = str(r.get("from") or r.get("id"))
        if key in seen:
            continue
        seen.add(key)
        uniq_replies.append(r)
    lines = [f"Ask the room → {total} chats ({', '.join(names)})",
             f"broadcast {broadcast_id} · {len(uniq_replies)} of {total} replied"]
    if skipped:
        lines.append(f"({skipped} chat(s) skipped: broadcast_max_targets cap)")
    for r in uniq_replies:
        who = str(r.get("from") or "?")[:12]
        body = str(r.get("text") or "").replace("\n", " ").strip()
        if len(body) > 400:
            body = body[:400] + "…"
        lines.append(f"  • {who}: {body}")
    return "\n".join(lines)


def t_ask_room(text: str, project: str = "", wait_seconds: int = 0, mode: str = "",
               track: str = "main") -> str:
    if not str(text or "").strip():
        return "Error: text is required."
    me = str((_s.detect_self() or {}).get("id") or "")
    tgts = targets(project=project, caller_id=me)
    if not tgts:
        return "Ask the room → no target chats found."
    cap = _settings()["broadcast_max_targets"]
    skipped = max(0, len(tgts) - cap)
    result = send(text, tgts, mode=(mode or "normal"), project=project)
    broadcast_id = result["broadcast_id"]
    if int(wait_seconds or 0) <= 0:
        names = ", ".join(str(t.get("id") or "")[:8] for t in result["targets"])
        return (f"Ask the room → broadcast {broadcast_id} to {len(result['targets'])} chats "
                f"({names}); {skipped} skipped. Poll with fastmail_status(broadcast_id=\"{broadcast_id}\").")
    replies = collect(broadcast_id, float(wait_seconds))
    return digest(broadcast_id, replies, skipped)
