"""Agent-to-agent dialogue layer for Subway Context.

Threaded conversations between stations (agents/workers/user) with optional
consensus proposals. Messages addressed to running workers are delivered via
the existing instruct_worker(now=True) path; others queue in the thread.

Storage: ~/.subway-context/agent_chats/<safe-name>.json (atomic, locked).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import server as _s

AGENT_CHATS_DIRNAME = "agent_chats"
_SAFE_CHAT_RE = re.compile(r"[^a-zA-Z0-9._@-]+")


def _ensure_agent_chats_dir() -> str:
    _s._ensure_subway_dir()
    path = os.path.join(_s.SUBWAY_DIR, AGENT_CHATS_DIRNAME)
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _safe_chat_id(chat_id: str) -> str:
    raw = str(chat_id or "").strip()
    if not raw:
        raise ValueError("chat_id is required")
    safe = _SAFE_CHAT_RE.sub("-", raw).strip("-._")
    if not safe:
        safe = "chat-" + uuid.uuid4().hex[:8]
    return safe[:120]


def _chat_path(chat_id: str) -> str:
    return os.path.join(_ensure_agent_chats_dir(), f"{_safe_chat_id(chat_id)}.json")


def _load_chat(chat_id: str) -> Dict[str, Any]:
    path = _chat_path(chat_id)
    if not os.path.exists(path):
        return {"chat_id": chat_id, "status": "open", "messages": [], "participants": [], "created_at": None}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        data.setdefault("chat_id", chat_id)
        data.setdefault("status", "open")
        data.setdefault("messages", [])
        data.setdefault("participants", [])
        return data
    except Exception:
        # Corrupt file: preserve by returning a fresh view; caller may overwrite under lock.
        return {"chat_id": chat_id, "status": "open", "messages": [], "participants": [], "created_at": None,
                "_corrupt": True}


def _save_chat(chat_id: str, data: Dict[str, Any]) -> None:
    _ensure_agent_chats_dir()
    _s._write_json_atomic(_chat_path(chat_id), data)


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _is_user_station(station: str) -> bool:
    s = str(station or "").lower()
    return s in ("user", "terminal", "hud") or "hud" in s or s.startswith("user-")


def _caller_station() -> str:
    try:
        det = _s._detected_chat()
        if det and det.get("id"):
            return str(det["id"])
    except Exception:
        pass
    try:
        sid = _s._self_id()
        if sid:
            return str(sid)
    except Exception:
        pass
    return "terminal"


def _resolve_station(token: str) -> Tuple[str, Dict[str, Any]]:
    """Resolve a station alias/id to (canonical_id, info)."""
    t = str(token or "").strip()
    if not t:
        return "", {}
    if t.lower() == "user":
        return "user", {"id": "user", "kind": "user"}
    if t.startswith("worker_"):
        return t, {"id": t, "kind": "worker"}
    # Try strict resolve for chat stations/aliases
    try:
        r = _s.resolve(t, strict=True)
        rid = str(r.get("id") or t)
        return rid, {"id": rid, "kind": "station", "source": r.get("source"), "title": r.get("title")}
    except Exception:
        # Fall back to worker lookup by id/prefix-ish exact, then raw token
        try:
            w = _s._load_worker(t)
            if w and w.get("id"):
                return str(w["id"]), {"id": str(w["id"]), "kind": "worker", "role": w.get("role")}
        except Exception:
            pass
        return t, {"id": t, "kind": "unknown"}


def _parse_to(to: Any) -> List[str]:
    if to is None:
        return []
    if isinstance(to, list):
        items = [str(x).strip() for x in to if str(x).strip()]
        return items
    s = str(to).strip()
    if not s:
        return []
    # comma or whitespace separated
    parts = [p.strip() for p in re.split(r"[,\s]+", s) if p.strip()]
    return parts


def _deliver_to_running_worker(worker_id: str, text: str, chat_id: str, from_station: str) -> Dict[str, Any]:
    """Deliver via existing instruct_worker(now=True) path when worker is running."""
    try:
        w = _s._load_worker(worker_id)
    except Exception:
        w = None
    if not w:
        return {"delivered": False, "reason": "worker not found", "worker_id": worker_id}
    status = str(w.get("status") or "")
    if status not in _s.ACTIVE_WORKER_STATES:
        return {"delivered": False, "reason": f"worker not running ({status})", "worker_id": worker_id}
    if status != "running":
        # instruct_worker requires running? it checks ACTIVE_WORKER_STATES; "now" recall sets instruction.
        # Still try the existing path as specified.
        pass
    msg = f"[AGENT_CHAT:{chat_id}] from {from_station}: {text}"
    try:
        from cli_tools import t_instruct_worker
        res = t_instruct_worker(worker_id=worker_id, text=msg, now=True)
    except Exception as e:
        return {"delivered": False, "reason": f"instruct_worker error: {e}", "worker_id": worker_id}
    if isinstance(res, str) and (res.startswith("Refused") or res.startswith("Error")):
        return {"delivered": False, "reason": res, "worker_id": worker_id}
    return {"delivered": True, "reason": res if isinstance(res, str) else "ok", "worker_id": worker_id}


def _consensus_verdict(chat: Dict[str, Any], proposal_msg: Dict[str, Any], track: str = "main") -> Optional[str]:
    """If all addressed stations replied to proposal, write verdict to shared track."""
    proposal_id = proposal_msg.get("id")
    required = [str(x) for x in (proposal_msg.get("consensus_required") or []) if x]
    if not proposal_id or not required:
        return None
    if proposal_msg.get("verdict_written"):
        return str(proposal_msg.get("verdict") or "")
    replies: Dict[str, Dict[str, Any]] = {}
    for m in chat.get("messages", []):
        if m.get("reply_to") != proposal_id:
            continue
        sender = str(m.get("from_station") or "")
        if sender in required:
            # latest reply wins for that sender
            replies[sender] = m
    if not all(r in replies for r in required):
        return None
    approvals = []
    rejections = []
    reasons = []
    for r in required:
        msg = replies[r]
        text = str(msg.get("text") or "").strip()
        low = text.lower()
        if low.startswith("approve"):
            approvals.append(r)
            if len(text) > len("approve"):
                reasons.append(f"{r}: {text}")
        elif low.startswith("reject"):
            rejections.append(r)
            reasons.append(f"{r}: {text}")
        else:
            # Treat ambiguous as rejection-with-reason for safety? Or count as response but unclear.
            rejections.append(r)
            reasons.append(f"{r}: (ambiguous) {text}")
    verdict = "approved" if approvals and not rejections else "rejected"
    summary = (f"[CONSENSUS {verdict.upper()}] chat={chat.get('chat_id')} proposal={proposal_id} "
               f"by={proposal_msg.get('from_station')} approvals={len(approvals)}/{len(required)}")
    if reasons:
        summary += " | " + " ; ".join(reasons)[:500]
    try:
        _s._broadcast_track_internal(summary, track=track, station_id="consensus",
                                     decisions=[f"consensus:{verdict}:{chat.get('chat_id')}:{proposal_id}"])
    except Exception:
        # don't fail the reply; verdict can be retried on next message
        return None
    proposal_msg["verdict_written"] = True
    proposal_msg["verdict"] = verdict
    proposal_msg["verdict_at"] = _now_iso()
    return verdict


def t_agent_chat(chat_id: str = "", to: Any = "", text: str = "", reply_to: str = "",
                 join: bool = True, list: bool = False, close: bool = False,
                 consensus: bool = False, track: str = "main", from_station: str = "",
                 read: bool = False, limit: int = 50) -> str:
    """Threaded agent-to-agent conversation.

    - list=True: show open conversations.
    - read=True + chat_id (or chat_id with no text): read thread messages.
    - close=True + chat_id: archive/close a conversation.
    - Otherwise: post a message to chat_id. join=True is accepted for API symmetry.
    - consensus=True: message is a proposal; `to` lists required responders.
      Replies starting with approve/reject (+reason) are tallied; when all respond,
      a verdict is written to the shared track.
    - Messages to running workers are delivered via instruct_worker(now=True).
    """
    if list:
        _ensure_agent_chats_dir()
        root = os.path.join(_s.SUBWAY_DIR, AGENT_CHATS_DIRNAME)
        items = []
        try:
            for name in sorted(os.listdir(root)):
                if not name.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(root, name), encoding="utf-8", errors="replace") as f:
                        d = json.load(f)
                    if not isinstance(d, dict):
                        continue
                    status = str(d.get("status") or "open")
                    if status == "closed":
                        continue
                    msgs = d.get("messages") or []
                    items.append(f"- `{d.get('chat_id') or name[:-5]}` [{status}] messages={len(msgs)} "
                                 f"participants={','.join(d.get('participants') or []) or '-'}")
                except Exception:
                    items.append(f"- `{name[:-5]}` [unreadable]")
        except Exception:
            pass
        return "## Open Agent Chats\n" + ("\n".join(items) if items else "(none)")

    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return "Error: chat_id is required (or use list=True)."

    if close:
        with _s._state_lock(f"agent_chat:{_safe_chat_id(chat_id)}"):
            chat = _load_chat(chat_id)
            if not chat.get("messages") and not chat.get("created_at"):
                return f"Agent chat `{chat_id}` not found."
            chat["status"] = "closed"
            chat["closed_at"] = _now_iso()
            _save_chat(chat_id, chat)
        return f"[AGENT_CHAT] Closed `{chat_id}`."

    text = str(text or "").strip()
    if read or (not text and not consensus):
        try:
            lim = max(1, min(int(limit or 50), 500))
        except Exception:
            lim = 50
        with _s._state_lock(f"agent_chat:{_safe_chat_id(chat_id)}"):
            chat = _load_chat(chat_id)
        if not chat.get("messages") and not chat.get("created_at"):
            return f"Agent chat `{chat_id}` not found or empty."
        msgs = (chat.get("messages") or [])[:][-lim:]
        out = [f"## Agent Chat `{chat.get('chat_id', chat_id)}` [{chat.get('status','open')}]",
               f"participants={','.join(chat.get('participants') or []) or '-'} messages={len(chat.get('messages') or [])}"]
        for m in msgs:
            mid = m.get("id", "?")
            frm = m.get("from_station", "?")
            to_s = ",".join(m.get("to") or []) or "-"
            rt = f" reply_to={m.get('reply_to')}" if m.get("reply_to") else ""
            flags = []
            if m.get("user"):
                flags.append("user")
            if m.get("consensus"):
                flags.append("proposal")
                if m.get("verdict"):
                    flags.append(f"verdict={m.get('verdict')}")
            fl = f" [{' '.join(flags)}]" if flags else ""
            out.append(f"\n- [{m.get('ts','')}] #{mid} {frm} → {to_s}{rt}{fl}\n  {m.get('text','')}")
        return "\n".join(out)

    if not text:
        return "Error: text is required to post a message."

    sender = str(from_station or "").strip() or _caller_station()
    sender_is_user = _is_user_station(sender)
    to_tokens = _parse_to(to)
    resolved_targets: List[str] = []
    target_info: List[Dict[str, Any]] = []
    for tok in to_tokens:
        rid, info = _resolve_station(tok)
        if rid:
            resolved_targets.append(rid)
            target_info.append(info)

    # consensus proposals require explicit addressed stations
    if consensus and not resolved_targets:
        return "Error: consensus=True requires `to` with one or more addressed stations."

    msg_id = uuid.uuid4().hex[:10]
    msg: Dict[str, Any] = {
        "id": msg_id,
        "from_station": sender,
        "to": resolved_targets,
        "text": text,
        "reply_to": str(reply_to or "").strip() or None,
        "ts": _now_iso(),
        "user": bool(sender_is_user),
    }
    if consensus:
        msg["consensus"] = True
        seen = set()
        required = []
        for r in resolved_targets:
            if r not in seen:
                seen.add(r)
                required.append(r)
        msg["consensus_required"] = required

    deliveries = []
    verdict = None
    with _s._state_lock(f"agent_chat:{_safe_chat_id(chat_id)}"):
        chat = _load_chat(chat_id)
        if chat.get("status") == "closed":
            return f"Error: agent chat `{chat_id}` is closed."
        if not chat.get("created_at"):
            chat["created_at"] = _now_iso()
        chat["chat_id"] = chat_id
        parts = set(chat.get("participants") or [])
        parts.add(sender)
        for r in resolved_targets:
            parts.add(r)
        chat["participants"] = sorted(parts)

        # Validate reply_to exists if provided
        if msg["reply_to"]:
            known = {m.get("id") for m in chat.get("messages", [])}
            if msg["reply_to"] not in known:
                # keep message but note; don't hard-fail to avoid losing coordination
                msg["reply_to_missing"] = True

        chat.setdefault("messages", []).append(msg)

        # Consensus verdict check if this is a reply to a proposal
        if msg["reply_to"]:
            for m in chat.get("messages", []):
                if m.get("id") == msg["reply_to"] and m.get("consensus"):
                    verdict = _consensus_verdict(chat, m, track=track)
                    break

        _save_chat(chat_id, chat)

        # Mid-run delivery for running workers (after persisting the message)
        for rid, info in zip(resolved_targets, target_info):
            if rid.startswith("worker_") or info.get("kind") == "worker":
                deliveries.append(_deliver_to_running_worker(rid, text, chat_id, sender))

    out = [f"[AGENT_CHAT] `{chat_id}` msg={msg_id} from={_s._short_id(sender) if hasattr(_s, '_short_id') else sender}"]
    if resolved_targets:
        out.append(f"to={','.join(resolved_targets)}")
    if consensus:
        out.append("proposal=consensus")
    if msg.get("reply_to"):
        out.append(f"reply_to={msg['reply_to']}")
        if msg.get("reply_to_missing"):
            out.append("(reply target not found)")
    for d in deliveries:
        if d.get("delivered"):
            out.append(f"delivered→{d['worker_id']} (running, via instruct_worker)")
        elif d.get("worker_id"):
            out.append(f"queued→{d['worker_id']} ({d.get('reason', 'not running')})")
    if verdict:
        out.append(f"verdict={verdict} (written to track '{track}')")
    return " ".join(out)
