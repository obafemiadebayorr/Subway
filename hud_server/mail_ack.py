"""Delivery acknowledgement for chat-targeted mail, for every provider.

A delivered message counts as done only once the chat answers it:
- providers with a chat reader (chat_readers: claude, opencode, antigravity, copilot, pi, cursor, devin, ...): the turns
  after the delivered message are read through the shared reader; a real assistant turn is a reply, an assistant turn
  that is only an API/limit error is an error;
- agents Subway cannot read: the delivered text asks the agent to call the mail_ack tool, which sets item["acked_at"].
Paths and parsing are OS-neutral (the readers own the storage formats).
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, Optional

_SAFE = re.compile(r"[A-Za-z0-9 .,:;()+%_#/@!?=-]")
_LIMIT = re.compile(r"usage limit|hit your limit|limit reached|rate.?limit|quota|insufficient|\b429\b|overloaded|credit balance|resource.?exhausted", re.I)


def snippet(text: str, n: int = 40) -> str:
    out = []
    for ch in str(text or "").strip():
        if not _SAFE.fullmatch(ch):
            break
        out.append(ch)
        if len(out) >= n:
            break
    return "".join(out).strip()


def _ts(turn: Dict[str, Any]) -> float:
    raw = turn.get("ts") or turn.get("timestamp")
    if raw in (None, ""):
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw) / (1000.0 if raw > 1e12 else 1.0)
    try:
        return _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _is_error(turn: Dict[str, Any]) -> bool:
    if turn.get("error") or turn.get("isApiErrorMessage"):
        return True
    text = str(turn.get("text") or "")
    return not turn.get("tool") and 0 < len(text) < 400 and bool(_LIMIT.search(text))


def _resolve(chat_id: str, source: str) -> Optional[Dict[str, Any]]:
    try:
        from chat_readers import dispatch
        chat = dispatch.resolve(chat_id, source or "all")
    except Exception:
        return None
    return chat if isinstance(chat, dict) and chat.get("id") else None


def _turns(chat: Dict[str, Any]) -> Optional[list]:
    try:
        from chat_readers import common
        return list(common.chat_turns(chat, segment="latest") or [])
    except Exception:
        return None


def can_read(chat_id: str, source: str) -> bool:
    """True when a chat reader can show this chat's turns; otherwise the agent must acknowledge with mail_ack."""
    chat = _resolve(chat_id, source)
    return bool(chat) and _turns(chat) is not None


def receipt_line(mail_id: str) -> str:
    return f"\n\n(Subway: when you have handled this, call the mail_ack tool with mail_id=\"{mail_id}\".)"


def reply_after(item: Dict[str, Any]) -> str:
    """'reply' | 'error' | 'pending' for a delivered item (item["awaiting_reply"] holds chat_id, source, since)."""
    a = item.get("awaiting_reply") or {}
    since = float(a.get("since") or 0)
    if since > 0 and float(item.get("acked_at") or 0) >= since:
        return "reply"
    chat = _resolve(str(a.get("chat_id") or ""), str(a.get("source") or ""))
    turns = _turns(chat) if chat else None
    if not turns:
        return "pending"
    snip = snippet((item.get("task") or {}).get("prompt") or "")
    start = None
    if len(snip) >= 8:
        for i, t in enumerate(turns):
            if snip in str(t.get("text") or "") and (not _ts(t) or _ts(t) >= since - 5):
                start = i + 1
    if start is None:
        start = next((i for i, t in enumerate(turns) if _ts(t) >= since), len(turns))
    saw_error = False
    for t in turns[start:]:
        if str(t.get("role") or "") != "assistant":
            continue
        if _is_error(t):
            saw_error = True
            continue
        return "reply"
    return "error" if saw_error else "pending"
