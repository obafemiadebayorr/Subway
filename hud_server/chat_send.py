"""Shared chat-send path: used by the HUD POST /api/chat route and the
server-side mail queue (postmail), so queued messages get the same
attachment/cut/swap handling as direct sends."""
from __future__ import annotations

import sys
from typing import Any, Dict, Tuple

import server as _s

try:
    from hud_server import attachments
except Exception:
    import attachments


def send(payload: dict) -> Tuple[int, Dict[str, Any]]:
    """Send a message into a chat exactly as POST /api/chat does.

    Returns (http_status, json_body) exactly as the route responds today.
    The scope check (pairing full-access) stays in the route, not here.
    """
    chat_id = str(payload.get("chat_id") or "").strip()
    text = str(payload.get("text") or "").strip()
    source = str(payload.get("source") or "").strip().lower()
    model = str(payload.get("model") or "").strip()
    effort = str(payload.get("effort") or "").strip()
    provider = str(payload.get("provider") or "").strip().lower()
    atts_in = payload.get("attachments")
    if atts_in is not None and not isinstance(atts_in, list):
        return 400, {"error": "attachments must be a list of file paths"}
    att_paths = [str(p) for p in (atts_in or []) if str(p or "").strip()]
    valid_atts, att_err = attachments.validate(att_paths or None)
    if att_err:
        return 400, {"error": att_err}
    if valid_atts:
        text += attachments.section(valid_atts)
    apps_in = payload.get("apps")
    if isinstance(apps_in, list) and apps_in:
        labels = [
            f"{a.get('name') or a.get('id')} ({a.get('station') or 'here'})" if isinstance(a, dict) else str(a)
            for a in apps_in if a
        ]
        if labels:
            text += "\n\n[Attached apps: " + ", ".join(labels) + " — use Subway MCP tool app_action]"
    if not chat_id or not text:
        return 400, {"error": "chat_id and text are required"}
    import provider_lines
    # Tests patch fastmail_lines.send_to_chat; honour that patched function.
    send_to_chat = provider_lines.send_to_chat
    if "fastmail_lines" in sys.modules:
        _patched = getattr(sys.modules["fastmail_lines"], "send_to_chat", None)
        if _patched is not None and _patched is not provider_lines.send_to_chat:
            send_to_chat = _patched
    cut_hidden = None
    _eng = None
    try:
        from transplant import engine as _eng
        _cut = _eng.cut_for(chat_id)
        if _cut and not _cut.get("sent"):
            from cli_tools import chats as _chats
            _rows = (_chats.chat_turns(chat_id, turns=100, apply_cut=False) or {}).get("turns") or []
            _tids = [r.get("tid") for r in _rows]
            if _cut["tid"] in _tids:
                _at = _tids.index(_cut["tid"])
                cut_hidden = _tids[_at + 1:]
                _last = str(_rows[_at].get("text") or "")[:160].replace("\n", " ")
                # Same session, told to forget the reverted turns (works for every provider).
                text = ("[The user reverted this conversation. Disregard every message after this one: "
                        f"\u00ab{_last}\u00bb. Code changes made since are not undone.]\n\n" + text)
    except Exception:
        cut_hidden = None
    if bool(payload.get("swap")) and provider:
        import conversation_swap
        res = conversation_swap.swap(chat_id, provider, text=text, model=model, effort=effort)
    else:
        res = send_to_chat(
            chat_id, text, source=source, model=model, effort=effort, target_provider=provider,
            swap=False,
        )
    if not isinstance(res, dict):
        res = {"delivered": False, "detail": str(res)}
    if not res.get("delivered") and not res.get("ok"):
        status = 409 if res.get("method") == "unsupported" else 502
        return status, {"error": str(res.get("detail") or "not delivered")}
    if cut_hidden is not None:
        _eng.mark_cut_sent(chat_id, cut_hidden)
    _s.mark_hud_cache_stale()
    return 200, res
