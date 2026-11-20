"""Fastmail — direct agent-to-agent messages with delivery honesty.

Messages are stored one JSON file per message under
`<SUBWAY_DIR>/fastmail/messages/<id>.json` (atomic tmp + os.replace, mirroring
`store._save_json`). Events are appended to `<SUBWAY_DIR>/fastmail/events.jsonl`
(append-only, mirroring `postmail._append_mail_event`). All writes happen under
`server._state_lock("fastmail")`.

A state advances to `delivered` only when the underlying primitive returned
success; the sender's receipt always states the delivery method.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import server as _s

VALID_STATES = ("queued", "delivered", "read", "replied", "failed", "suppressed")
_STATE_TS_KEY = {"delivered": "delivered_at", "read": "read_at", "replied": "replied_at"}


def _norm_station(name: str) -> str:
    """Loose station-id normalization for mute matching (A3/A6): lowercase, strip @ and -latest suffix."""
    n = str(name or "").strip().lower().lstrip("@")
    return n[:-len("-latest")] if n.endswith("-latest") else n


def _fastmail_dir() -> str:
    path = os.path.join(_s.SUBWAY_DIR, "fastmail")
    os.makedirs(path, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)
    return path


def _messages_dir() -> str:
    path = os.path.join(_fastmail_dir(), "messages")
    os.makedirs(path, exist_ok=True)
    return path


def _events_path() -> str:
    _fastmail_dir()
    return os.path.join(_fastmail_dir(), "events.jsonl")


def new_message_id() -> str:
    return f"fm_{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_message(msg: Dict[str, Any]) -> None:
    path = os.path.join(_messages_dir(), f"{msg['id']}.json")
    with _s._state_lock("fastmail"):
        _atomic_write_json(path, msg)


def load_message(msg_id: str) -> Dict[str, Any]:
    try:
        path = os.path.join(_messages_dir(), f"{msg_id}.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def list_messages(scope: str = "all", state: str = "", chat_id: str = "",
                  limit: int = 50) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    mtimes: Dict[str, float] = {}
    try:
        names = os.listdir(_messages_dir())
    except OSError:
        return []
    for name in names:
        if not name.endswith(".json"):
            continue
        msg = load_message(name[:-5])
        if not msg:
            continue
        try:
            mtimes[msg["id"]] = os.stat(os.path.join(_messages_dir(), name)).st_mtime
        except OSError:
            mtimes[msg["id"]] = 0.0
        if state and msg.get("state") != state:
            continue
        if chat_id and chat_id not in (msg.get("to"), msg.get("from")):
            continue
        out.append(msg)
    out.sort(key=lambda m: (str(m.get("created_at") or ""), mtimes.get(m.get("id"), 0.0)), reverse=True)
    return out[: max(0, limit)]


def append_event(kind: str, msg: Dict[str, Any]) -> None:
    event = {
        "ts": _now_iso(),
        "kind": kind,
        "id": msg.get("id"),
        "state": msg.get("state"),
        "to": msg.get("to"),
        "from": msg.get("from"),
        "text_sha256": hashlib.sha256(str(msg.get("text") or "").encode("utf-8")).hexdigest(),
        "thread_id": msg.get("thread_id"),
    }
    line = json.dumps(event) + "\n"
    path = _events_path()
    with _s._state_lock("fastmail"):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
        except OSError as e:
            _s.log(f"failed to append fastmail event for {msg.get('id')}: {e}")


def set_state(msg_id: str, state: str, detail: str = "") -> bool:
    if state not in VALID_STATES:
        return False
    with _s._state_lock("fastmail"):
        msg = load_message(msg_id)
        if not msg:
            return False
        msg["state"] = state
        ts_key = _STATE_TS_KEY.get(state)
        now = _now_iso()
        if ts_key:
            msg[ts_key] = now
        if detail:
            delivery = msg.get("delivery") or {}
            delivery["detail"] = detail
            if state == "delivered":
                delivery["method"] = detail
            msg["delivery"] = delivery
        _atomic_write_json(os.path.join(_messages_dir(), f"{msg_id}.json"), msg)
    append_event(state, msg)
    try:
        import fastmail_broadcast
        fastmail_broadcast.notify_activity()
    except Exception:
        pass
    return True


def receipt(msg: Dict[str, Any]) -> str:
    delivery = msg.get("delivery") or {}
    method = delivery.get("method") or "unknown"
    source = str(msg.get("source") or "").upper() or "UNKNOWN"
    state = msg.get("state") or "queued"
    read = "read" if msg.get("read_at") else "read pending"
    reply = "replied" if msg.get("replied_at") else "reply pending"
    parts = [f"{msg.get('id')} → {source} {msg.get('to')}", f"{state} ({method})"]
    if state not in ("read", "replied"):
        parts.append(read)
    parts.append(reply)
    return " · ".join(parts)


def create_message(target: Dict[str, Any], text: str, from_chat: str = "",
                   mode: str = "fast", reply_to: str = "") -> Dict[str, Any]:
    msg_id = new_message_id()
    parent = load_message(reply_to) if reply_to else {}
    thread_id = (parent.get("thread_id") if parent else None) or msg_id
    hops = int(parent.get("hops") or 0) + 1 if parent else 0
    msg: Dict[str, Any] = {
        "id": msg_id,
        "from": from_chat,
        "to": target.get("to") or target.get("chat_id") or target.get("id") or "",
        "source": target.get("source") or target.get("provider") or "",
        "text": text,
        "reply_to": reply_to or None,
        "thread_id": thread_id,
        "broadcast_id": None,
        "mode": mode,
        "state": "queued",
        "hops": hops,
        "created_at": _now_iso(),
        "delivered_at": None,
        "read_at": None,
        "replied_at": None,
        "delivery": {"method": "", "detail": "", "attempts": 0},
        "unverified": False,
        "parent": reply_to or None,
        "shared_chat": None,
    }
    save_message(msg)
    append_event("created", msg)
    return msg

MAIL_DEFAULTS: Dict[str, Any] = {
    "mode": "fast",
    "wake_offline": "ask",
    "offline_budget_tokens": 20000,
    "offline_budget_seconds": 120,
    "broadcast_wait_seconds": 20,
    "broadcast_max_targets": 12,
    "max_hops": 3,
    "rate_limit_per_hour": 20,
    "mute": False,
    "muted_senders": "",
}


def load_settings() -> Dict[str, Any]:
    """Effective mail.* settings from config.json, with defaults for missing/corrupt entries."""
    try:
        cfg = _s._load_json("config.json")
    except Exception:
        cfg = None
    if not isinstance(cfg, dict):
        cfg = {}
    nested = cfg.get("mail") if isinstance(cfg.get("mail"), dict) else {}

    def _raw(key: str) -> Any:
        v = cfg.get(f"mail.{key}")
        if v is None:
            v = nested.get(key)
        return v

    out = dict(MAIL_DEFAULTS)
    for key, default in MAIL_DEFAULTS.items():
        v = _raw(key)
        if v is None:
            continue
        try:
            if isinstance(default, bool):
                if isinstance(v, str):
                    v = v.strip().lower() in ("1", "true", "yes", "on")
                out[key] = bool(v)
            elif isinstance(default, int):
                out[key] = int(v)
            else:
                out[key] = str(v)
        except (TypeError, ValueError):
            continue
    if out["mode"] not in ("normal", "fast"):
        out["mode"] = MAIL_DEFAULTS["mode"]
    if out["wake_offline"] not in ("off", "ask", "on"):
        out["wake_offline"] = MAIL_DEFAULTS["wake_offline"]
    out["muted_senders"] = str(out["muted_senders"] or "")
    return out


def rate_count(sender: str, now: float) -> int:
    """Count that sender's created events in the last hour from events.jsonl."""
    if not sender:
        return 0
    path = _events_path()
    count = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return 0
    for line in lines:
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("kind") != "created":
            continue
        if _norm_station(str(ev.get("from") or "")) != _norm_station(str(sender)):
            # A6: rate-limit accounting is per normalized station identity.
            continue
        try:
            ts = _dt.datetime.fromisoformat(str(ev.get("ts") or "")).timestamp()
        except Exception:
            continue
        if 0 <= now - ts <= 3600:
            count += 1
    return count


def _recent_duplicate(msg: Dict[str, Any], now: float) -> bool:
    path = _events_path()
    want = hashlib.sha256(str(msg.get("text") or "").encode("utf-8")).hexdigest()
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False
    for line in lines:
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("kind") != "created":
            continue
        if _norm_station(str(ev.get("from") or "")) != _norm_station(str(msg.get("from") or "")):
            # A6: duplicate suppression also matches normalized identities.
            continue
        if str(ev.get("to") or "") != str(msg.get("to") or ""):
            continue
        if str(ev.get("text_sha256") or "") != want:
            continue
        try:
            ts = _dt.datetime.fromisoformat(str(ev.get("ts") or "")).timestamp()
        except Exception:
            continue
        if 0 <= now - ts <= 60:
            return True
    return False


def enforce_guards(msg: Dict[str, Any]) -> Optional[str]:
    """Return a refusal reason, or None if the message may be sent."""
    settings = load_settings()
    sender = str(msg.get("from") or "")
    to = str(msg.get("to") or "")
    now = time.time()

    if sender and to and sender == to:
        # A6 note: self-send stays an EXACT same-chat rule — station-level normalization
        # here would refuse legitimate same-station/different-chat replies (test_reply_note).
        return "self-send refused"

    try:
        hops = int(msg.get("hops") or 0)
    except (TypeError, ValueError):
        hops = 0
    if hops >= int(settings["max_hops"]):
        return f"hop cap reached ({settings['max_hops']})"

    muted = [s.strip() for s in str(settings.get("muted_senders") or "").split(",") if s.strip()]
    if muted:
        muted_n = {_norm_station(m) for m in muted}
        if to and _norm_station(to) in muted_n:
            # Original semantics (tested): a muted station is never woken — no delivery to it.
            return f"station `{to}` is muted"
        # A3: and mail FROM a muted station is refused — it must not spam everyone else.
        if sender and _norm_station(sender) in muted_n:
            return f"sender `{sender}` is muted"

    thread_id = str(msg.get("thread_id") or "")
    if not thread_id and msg.get("reply_to"):
        parent = load_message(str(msg.get("reply_to")))
        thread_id = str((parent or {}).get("thread_id") or "")
    if thread_id:
        try:
            import thread_state
            tset = thread_state.get_thread_settings(thread_id) or {}
            if tset.get("mail.mute") or tset.get("mute"):
                return "thread mailbox is muted"
        except Exception:
            pass

    if _recent_duplicate(msg, now):
        return "duplicate within 60s suppressed"

    if sender and rate_count(sender, now) >= int(settings["rate_limit_per_hour"]):
        return f"rate limit exceeded ({settings['rate_limit_per_hour']}/hour)"

    return None


def http_get(scope: str = "outbox", state: str = "", limit: int = 50) -> Dict[str, Any]:
    """HUD payload: always both keys, counts include every state (0 when empty)."""
    counts = {s: 0 for s in VALID_STATES}
    everything = list_messages(scope=scope, limit=100000)
    for m in everything:
        st = str(m.get("state") or "")
        if st in counts:
            counts[st] += 1
    messages = [m for m in everything if not state or m.get("state") == state]
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    messages = messages[: max(0, limit)]
    muted = [s.strip() for s in str(load_settings().get("muted_senders") or "").split(",") if s.strip()]
    return {"messages": messages, "counts": counts, "muted_senders": muted}
