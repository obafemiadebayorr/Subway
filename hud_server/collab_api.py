"""Collaboration session backend for the HUD Collaboration page.

Stdlib only. Session state lives in one JSON file (SUBWAY_COLLAB_FILE, default
~/.subway-context/collab.json) written atomically with 0600 permissions.
Join codes are never logged.

handlers.py wires it up with:

    if path.startswith("/api/collab/"):
        r = collab_api.handle_get(path)
        if r is not None: return _json(*r)
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

try:  # getpass can fail on odd platforms; identity is best-effort.
    import getpass
except Exception:  # pragma: no cover - stdlib import guard
    getpass = None  # type: ignore

PREFIX = "/api/collab/"
DEFAULT_FILE = "~/.subway-context/collab.json"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LEN = 6
SCOPE_KEYS = ("threads", "workers", "terminals")

_LOCK = threading.Lock()

Result = Tuple[int, Dict[str, Any]]


# --------------------------------------------------------------------------- io


def state_path() -> str:
    raw = os.environ.get("SUBWAY_COLLAB_FILE")
    if raw:
        return os.path.abspath(os.path.expanduser(raw))
    legacy = os.path.abspath(os.path.expanduser(DEFAULT_FILE))
    try:
        import subway_home  # type: ignore
        station_dir = subway_home.subway_dir()
    except Exception:
        station_dir = ""
    if station_dir:
        own = os.path.join(station_dir, "collab.json")
        # A station with its own SUBWAY_DIR keeps its own session; a default install keeps the legacy file.
        if os.environ.get("SUBWAY_DIR") or os.path.exists(own) or not os.path.exists(legacy):
            return own
    return legacy


def _load() -> Optional[Dict[str, Any]]:
    path = state_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("id") or not isinstance(data.get("members"), list):
        return None
    return data


def _save(session: Optional[Dict[str, Any]]) -> None:
    path = state_path()
    if session is None:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(session, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------- device


def _device_id() -> str:
    explicit = os.environ.get("SUBWAY_DEVICE_ID")
    if explicit:
        return explicit.strip()
    seed = "%s|%s" % (socket.gethostname(), platform.node())
    station_dir = os.environ.get("SUBWAY_DIR")
    if station_dir:
        # A second station on the same machine must not share the first one's device id.
        seed += "|" + os.path.abspath(os.path.expanduser(station_dir))
    return "dev-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]


def _device_kind() -> str:
    explicit = os.environ.get("SUBWAY_DEVICE_KIND")
    if explicit:
        return explicit.strip()
    system = platform.system()
    if system == "Darwin":
        return "Mac"
    if system == "Windows":
        return "PC"
    return "Linux"


def _device_name() -> str:
    explicit = os.environ.get("SUBWAY_DEVICE_NAME")
    if explicit:
        return explicit.strip()
    try:
        host = socket.gethostname().split(".")[0]
    except OSError:
        host = ""
    return host or _device_kind()


def _person() -> str:
    explicit = os.environ.get("SUBWAY_PERSON")
    if explicit:
        return explicit.strip()
    if getpass is not None:
        try:
            user = getpass.getuser()
        except Exception:
            user = ""
        if user:
            return user
    return "You"


CARD_KEYS = ("station_id", "person", "device", "version", "providers", "models_count", "capacity",
             "worker_capacity", "edge_eligible", "default_initiator")
CARD_MAX_BYTES = 2048


def _card_summary(card: Any) -> Dict[str, Any]:
    """The part of a station card a session member carries; full cards overflow the 32 KB tunnel body limit."""
    if not isinstance(card, dict):
        return {}
    out: Dict[str, Any] = {k: card[k] for k in CARD_KEYS if k in card}
    prov = out.get("providers")
    if prov is not None and len(json.dumps(prov, default=str)) > 1024:
        if isinstance(prov, dict):
            out["providers"] = sorted(str(k) for k in prov)[:24]
        elif isinstance(prov, list):
            out["providers"] = [p if isinstance(p, str) else str((p or {}).get("name") or (p or {}).get("id") or "") for p in prov][:24]
        else:
            out.pop("providers", None)
    for k in list(out):
        if len(json.dumps(out, default=str)) <= CARD_MAX_BYTES:
            break
        if k not in ("station_id", "person", "device"):
            out.pop(k, None)
    return out


def _station_card() -> Dict[str, Any]:
    """Best-effort local station card (phase 1: /api/peer/station). Never raises."""
    try:
        import station  # type: ignore

        card = station.get_station_card()
        return _card_summary(card)
    except Exception:
        return {}


def _me(role: str = "member", station_card: Any = None, peer_id: Any = None) -> Dict[str, Any]:
    card = station_card if isinstance(station_card, dict) else _station_card()
    pid = peer_id.strip() if isinstance(peer_id, str) and peer_id.strip() else None
    entry: Dict[str, Any] = {
        "device_id": _device_id(),
        "name": _device_name(),
        "person": _person(),
        "device_kind": _device_kind(),
        "online": True,
        "role": role,
        "station_card": card,
        "joined_at": time.time(),
    }
    if pid:
        entry["peer_id"] = pid
    return entry


# --------------------------------------------------------------------- helpers


def _norm_code(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return "".join(ch for ch in raw.upper() if ch.isalnum())


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def _norm_scopes(raw: Any) -> Dict[str, bool]:
    src = raw if isinstance(raw, dict) else {}
    return {key: bool(src.get(key, False)) for key in SCOPE_KEYS}


def _clean_name(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return " ".join(raw.split())[:60]


def _coach_members(raw: Any) -> List[Dict[str, Any]]:
    """Validate engine-supplied coach cards. Identity only: no tokens, no codes, bounded strings."""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in raw[:32]:
        if not isinstance(item, dict):
            continue
        did = _clean_name(item.get("device_id")) or str(item.get("device_id") or "").strip()[:64]
        if not did or did in seen:
            continue
        seen.add(did)
        card = item.get("station_card")
        grants = item.get("grants")
        out.append({
            "device_id": did,
            "name": _clean_name(item.get("name")) or did,
            "person": _clean_name(item.get("person")) or _clean_name(item.get("name")) or did,
            "device_kind": _clean_name(item.get("device_kind")) or "station",
            "role": "member",
            "train_role": "coach",
            "station_card": card if isinstance(card, dict) else {},
            "grants": grants if isinstance(grants, dict) else {},
        })
    return out


def _member_from_body(data: Dict[str, Any], role: str = "member") -> Dict[str, Any]:
    """Coupling, then names (Design 1): prefer explicit identity from join/start body."""
    person = _clean_name(data.get("person")) or _person()
    name = _clean_name(data.get("device_name")) or _device_name()
    kind = _clean_name(data.get("device_kind")) or _device_kind()
    did = data.get("device_id")
    device_id = did.strip() if isinstance(did, str) and did.strip() else _device_id()
    card = data.get("station_card")
    if isinstance(card, dict):
        card = _card_summary(card)
    else:
        # Phase 1 exposes /api/peer/station; local fallback uses station module.
        card = _station_card()
    peer_id = data.get("peer_id")
    pid = peer_id.strip() if isinstance(peer_id, str) and peer_id.strip() else None
    entry: Dict[str, Any] = {
        "device_id": device_id,
        "name": name,
        "person": person,
        "device_kind": kind,
        "online": True,
        "role": role,
        "station_card": card,
        "joined_at": time.time(),
    }
    if pid:
        entry["peer_id"] = pid
    # Local initiator (roadmap 38 pt 2): default to the station card's default_initiator.
    entry["local_initiator_chat"] = str(data.get("local_initiator_chat")
                                        or (card or {}).get("default_initiator")
                                        or "principal")
    return entry


def _public(session: Dict[str, Any]) -> Dict[str, Any]:
    me = _device_id()
    members: List[Dict[str, Any]] = []
    for m in session.get("members", []):
        if not isinstance(m, dict):
            continue
        entry = dict(m)
        entry["online"] = True
        entry["self"] = entry.get("device_id") == me
        # Station card shown at coupling (Design 3); keep it JSON-safe and bounded.
        card = entry.get("station_card")
        entry["station_card"] = card if isinstance(card, dict) else {}
        members.append(entry)
    mine = next((m for m in members if m.get("self")), None)
    scopes = _norm_scopes(session.get("scopes"))
    out = dict(session)
    out["members"] = members
    out["scopes"] = scopes
    out["scope"] = dict(scopes)
    out["role"] = (mine or {}).get("role") or ("host" if _is_host(session) else "member")
    out["code"] = session.get("code") or ""
    out["couple_uri"] = "subway://collab/couple?code=" + _norm_code(out["code"])
    out["engines"] = [m.get("device_id") for m in members if isinstance(m, dict) and not m.get("via")]
    out["coaches"] = [m.get("device_id") for m in members if isinstance(m, dict) and m.get("via")]
    out["shared_coaches"] = session.get("shared_coaches") or []
    out["billing_mode"] = session.get("billing_mode") or "A"
    out["master_device"] = session.get("master_device") or session.get("host_device") or ""
    out["is_master"] = out["master_device"] == me
    out["plans"] = {
        str(m.get("device_id")): m["plans"]
        for m in members
        if isinstance(m.get("plans"), dict) and (out["is_master"] or m.get("self"))
    }
    return out


def _is_host(session: Dict[str, Any]) -> bool:
    owner = session.get("host_device")
    if owner:
        return owner == _device_id()
    return session.get("host") == socket.gethostname()


def _find_member(session: Dict[str, Any], device_id: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(device_id, str) or not device_id:
        return None
    for m in session.get("members", []):
        if isinstance(m, dict) and m.get("device_id") == device_id:
            return m
    return None


def _err(status: int, message: str) -> Result:
    return status, {"error": message}


def _normalize_path(path: Any) -> str:
    if not isinstance(path, str):
        return ""
    clean = path.split("?", 1)[0].split("#", 1)[0]
    if len(clean) > 1 and clean.endswith("/"):
        clean = clean.rstrip("/")
    return clean


def _end_session(session: Dict[str, Any]) -> bool:
    _fanout_end(session)
    _save(None)
    return True


def _linked_peer_ids(session: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    for m in session.get("members", []):
        if not isinstance(m, dict):
            continue
        pid = m.get("peer_id")
        if isinstance(pid, str) and pid and pid not in ids:
            ids.append(pid)
    return ids


def _engine_peer_ids(session: Dict[str, Any]) -> List[str]:
    """The engines we talk to directly: members with a link home and no engine vouching for them.

    Coaches carry `via` (the peer_id of their own engine in OUR namespace) and are reached only
    through that engine, so a partner's coaches are never called directly by the host.
    """
    ids: List[str] = []
    for m in session.get("members", []):
        if isinstance(m, dict) and str(m.get("device_id") or "") == _device_id():
            continue
        if not isinstance(m, dict) or m.get("via"):
            continue
        pid = m.get("peer_id")
        if isinstance(pid, str) and pid and pid not in ids:
            ids.append(pid)
    return ids


def _coach_links(session: Optional[Dict[str, Any]], via: str) -> List[str]:
    """Engines: the peer_ids of our own coaches that this snapshot marks as ours."""
    if not session or not via:
        return []
    try:
        import peers  # type: ignore
    except Exception:
        return []
    out: List[str] = []
    for m in session.get("members", []):
        if not isinstance(m, dict) or str(m.get("via") or "") != str(via):
            continue
        p = peers.by_device(str(m.get("device_id") or ""))
        if p and p.get("peer_id") and p["peer_id"] not in out:
            out.append(p["peer_id"])
    return out


def _fanout(session: Dict[str, Any], removed_device_id: Optional[str] = None) -> None:
    """Best-effort: sync session state to paired ENGINES; each engine re-fans to its own coaches."""
    try:
        import peers  # type: ignore
    except Exception:
        return
    import threading
    ids = _engine_peer_ids(session)

    def _send() -> None:
        for pid in ids:
            payload = {"session": session, "removed_device_id": removed_device_id, "via": pid, "sender_is_host": True}
            try:
                peers.call(pid, "POST", "/api/peer/collab/sync", payload, timeout=8)
            except Exception:
                pass

    threading.Thread(target=_send, daemon=True).start()


def _fanout_end(session: Dict[str, Any]) -> None:
    """Tell the engines the session ended; they pass the end down to their coaches."""
    try:
        import peers  # type: ignore
    except Exception:
        return
    import threading
    ids = _engine_peer_ids(session)
    sid = session.get("id")

    def _send() -> None:
        for pid in ids:
            payload = {"session": None, "ended": True, "session_id": sid, "via": pid}
            try:
                peers.call(pid, "POST", "/api/peer/collab/sync", payload, timeout=8)
            except Exception:
                pass

    threading.Thread(target=_send, daemon=True).start()


_LAST_GATEWAY_SNAPSHOT: Optional[Dict[str, Any]] = None


def _relay(session: Optional[Dict[str, Any]], via: str, ended_id: Optional[str] = None) -> None:
    """Engine side: pass the host's snapshot (or its end) down to the coaches we vouched for."""
    global _LAST_GATEWAY_SNAPSHOT
    ended = session is None
    if session is not None:
        _LAST_GATEWAY_SNAPSHOT = session
        snap = session
    else:
        # End notifications carry no snapshot; reuse the last one we relayed.
        snap = _LAST_GATEWAY_SNAPSHOT
    links = _coach_links(snap, via)
    if not links:
        return
    try:
        import peers  # type: ignore
    except Exception:
        return
    import threading

    def _send() -> None:
        for pid in links:
            payload = ({"session": None, "ended": True, "session_id": ended_id}
                       if ended else {"session": snap, "removed_device_id": None,
                                      "you": str((peers.get(pid) or {}).get("device_id") or "")})
            try:
                peers.call(pid, "POST", "/api/peer/collab/sync", payload, timeout=8)
            except Exception:
                pass

    threading.Thread(target=_send, daemon=True).start()


def handle_get(path: str) -> Optional[Result]:
    """GET /api/collab/*. Returns None for anything outside the prefix."""
    route = _normalize_path(path)
    if not route.startswith(PREFIX):
        return None
    if route == PREFIX + "session":
        with _LOCK:
            session = _load()
            if session is None:
                return 200, {"session": None}
            return 200, {"session": _public(session)}
    if route == PREFIX + "usage":
        # Per-member token totals for the active session (roadmap 38 point 4: two names
        # on every cost). Linked stations' spend is what this station can measure today;
        # local members report 0 until per-worker attribution lands.
        with _LOCK:
            session = _load()
        if session is None:
            return 200, {"session_id": None, "billing_mode": None, "members": [], "total": 0}
        members: List[Dict[str, Any]] = []
        total = 0
        try:
            import peers as _peers
        except Exception:
            _peers = None
        for m in session.get("members", []):
            if not isinstance(m, dict):
                continue
            dev = str(m.get("device_id") or "")
            peer_id = None
            used = 0
            if _peers and dev:
                p = _peers.by_device(dev)
                if not p and m.get("peer_id"):
                    p = _peers.get(str(m.get("peer_id")))  # link device ids differ from collab device ids
                if p:
                    peer_id = p.get("peer_id")
                    try:
                        used = int(_peers._peer_tokens_today(p["peer_id"]) or 0)
                    except Exception:
                        used = 0
            total += used
            swarm_used = 0
            if _peers and peer_id:
                try:
                    swarm_used = int(_peers.swarm_tokens_today(peer_id) or 0)
                except Exception:
                    swarm_used = 0
            members.append({"device_id": dev, "person": m.get("person") or m.get("name") or "",
                            "peer_id": peer_id, "used_today": used, "swarm_today": swarm_used})
        swarm_total = sum(int(m.get("swarm_today") or 0) for m in members)
        return 200, {"session_id": session.get("id"), "billing_mode": session.get("billing_mode") or "A",
                     "master_device": session.get("master_device"), "members": members,
                     "total": total, "swarm_total": swarm_total, "ts": time.time()}
    return _err(404, "Unknown collaboration endpoint")


def dispatch_mode(sender_device: str, sender_peer_id: str = "", claimed_device: str = "") -> Dict[str, Any]:
    """How a linked station's spawn request is handled here (roadmap 38 point 4).

    Sender not in this station's live session -> mode "" (plain link grants apply).
    Mode A -> the request waits as an approval item for this station's local initiator.
    Mode B -> the master drives workers here directly.
    In A and B the workers run on, and are paid by, this station."""
    sess = _load()
    sender = str(sender_device or "").strip()
    # Link device ids differ from collab device ids: also match the claimed collab id or the link peer_id.
    found = None
    if isinstance(sess, dict):
        if sender:
            found = _find_member(sess, sender)
        if found is None and claimed_device:
            found = _find_member(sess, str(claimed_device).strip())
        if found is None and sender_peer_id:
            found = next((m for m in sess.get("members", []) if isinstance(m, dict)
                          and str(m.get("peer_id") or "") == str(sender_peer_id)), None)
    if not isinstance(sess, dict) or found is None:
        return {"mode": "", "review": False, "session_id": "", "local_initiator_chat": "", "paid_by": ""}
    mode = str(sess.get("billing_mode") or "A").strip().upper()
    if mode not in ("A", "B"):
        mode = "A"
    mine = _find_member(sess, _device_id()) or {}
    return {
        "mode": mode,
        "review": mode == "A",
        "session_id": str(sess.get("id") or ""),
        "local_initiator_chat": str(mine.get("local_initiator_chat") or "principal"),
        "paid_by": _device_name(),
    }


def handle_post(path: str, body: Optional[Dict[str, Any]] = None) -> Optional[Result]:
    """POST /api/collab/*. Returns None for anything outside the prefix."""
    route = _normalize_path(path)
    if not route.startswith(PREFIX):
        return None
    action = route[len(PREFIX):]
    # Train terms (item 38): stations couple into one session and uncouple to leave it.
    action = {"couple": "join", "uncouple": "leave"}.get(action, action)
    if action not in ("start", "join", "leave", "end", "remove", "scope", "handover",
                      "billing", "master", "local_initiator", "brief", "plans"):
        return _err(404, "Unknown collaboration endpoint")
    data: Dict[str, Any] = body if isinstance(body, dict) else {}
    if action == "join" and route.endswith("/couple") and str(data.get("station") or "").strip():
        # Couple THIS station into a session hosted on a linked station.
        return couple_remote(str(data["station"]), str(data.get("code") or ""))

    with _LOCK:
        session = _load()

        if action == "start":
            if session is not None:
                return _err(409, "A collaboration session is already active on this station")
            name = _clean_name(data.get("name"))
            if not name:
                return _err(400, "A session name is required")
            mode = str(data.get("billing_mode") or "A").strip().upper()
            if mode not in ("A", "B"):
                return _err(400, "billing_mode must be A or B")
            me = _member_from_body(data, "host")
            me["device_id"] = _device_id()  # host is this station
            new: Dict[str, Any] = {
                "id": "collab-" + secrets.token_hex(8),
                "name": name,
                "code": _new_code(),
                "host": socket.gethostname(),
                "host_device": me["device_id"],
                "master_device": me["device_id"],
                "billing_mode": mode,
                "members": [me],
                "scopes": _norm_scopes(data.get("scopes")),
                "started_at": time.time(),
                "revision": 1,
            }
            _save(new)
            _fanout(new)
            return 200, {"session": _public(new), "host": _device_name()}

        if action == "join":
            if session is None:
                return _err(404, "No collaboration session to join")
            code = _norm_code(data.get("code"))
            if not code or not secrets.compare_digest(code, _norm_code(session.get("code"))):
                return _err(400, "That join code is not valid")
            coaches = _coach_members(data.get("coaches"))
            incoming = _member_from_body(data, "member")
            if coaches:
                incoming["train_role"] = "engine"
            did = incoming.get("device_id") or _device_id()
            existing = _find_member(session, did)
            if existing is None:
                session.setdefault("members", []).append(incoming)
            else:
                existing.update({k: v for k, v in incoming.items() if k not in ("role",)})
                existing["online"] = True
                existing.setdefault("role", "member")
            # Train (item 38): the engine's shared coaches ride along, reachable only through it.
            host_device = session.get("host_device")
            via = str(data.get("peer_id") or "")
            if coaches and via:
                shared = []
                for coach in coaches:
                    coach["via"] = via
                    coach["train_role"] = "coach"
                    cid = coach.get("device_id")
                    if not cid or cid == host_device:
                        continue
                    found = _find_member(session, cid)
                    if found is None:
                        session.setdefault("members", []).append(coach)
                    else:
                        found.update(coach)
                        found["role"] = "member"
                    shared.append({"device_id": cid, "station": str(coach.get("person") or cid),
                                   "grants": coach.get("grants") or {}})
                if shared:
                    session["shared_coaches"] = shared
            session["revision"] = int(session.get("revision") or 1) + 1
            _save(session)
            _fanout(session)
            return 200, {"session": _public(session)}

        if session is None:
            return _err(404, "No collaboration session is active")

        if action == "leave":
            # A member may leave only themselves; host leaving ends the session for all.
            target = data.get("device_id")
            me = _device_id()
            did = target.strip() if isinstance(target, str) and target.strip() else me
            if did != me:
                return _err(403, "A member can only remove their own device with leave")
            member = _find_member(session, me)
            if member is None:
                return _err(404, "This device is not in the session")
            if member.get("role") == "host" or session.get("host_device") == me:
                ended = _end_session(session)
                return 200, {"session": None, "ended": ended}
            session["members"] = [m for m in session.get("members", []) if m.get("device_id") != me]
            session["revision"] = int(session.get("revision") or 1) + 1
            _save(session)
            _fanout(session)
            return 200, {"session": None}

        if action == "end":
            # Any member device can end the session (roadmap 38f).
            member = _find_member(session, _device_id())
            if member is None:
                return _err(403, "Only a session member can end this session")
            ended = _end_session(session)
            return 200, {"session": None, "ended": ended}

        if action == "remove":
            # Any member device can remove another (roadmap 38f).
            actor = _find_member(session, _device_id())
            if actor is None:
                return _err(403, "Only a session member can remove devices")
            target = data.get("device_id")
            if not isinstance(target, str) or not target:
                return _err(400, "A device_id is required")
            victim = _find_member(session, target)
            if victim is None:
                return _err(404, "That device is not in the session")
            if victim.get("device_id") == session.get("host_device"):
                # Removing the host ends the session (avoids a stranded, hostless session).
                ended = _end_session(session)
                return 200, {"session": None, "ended": ended}
            session["members"] = [m for m in session.get("members", []) if m.get("device_id") != target]
            session["key_rotated_at"] = time.time()
            session["key_generation"] = int(session.get("key_generation") or 1) + 1
            session["revision"] = int(session.get("revision") or 1) + 1
            _save(session)
            _fanout(session, removed_device_id=target)
            return 200, {"session": _public(session)}

        if action == "handover":
            # Already inside _LOCK: hand the loaded session in so handover() does not re-lock.
            return handover(str(data.get("to") or ""), from_peer="", session=session)
        if action == "billing":
            if session is None:
                return _err(404, "No collaboration session to configure")
            me_id = _device_id()
            master = str(session.get("master_device") or session.get("host_device") or "")
            if me_id != master and me_id != session.get("host_device"):
                return _err(403, "Only the master station may set the billing mode")
            mode = str(data.get("billing_mode") or "").strip().upper()
            if mode not in ("A", "B"):
                return _err(400, "billing_mode must be A or B")
            session["billing_mode"] = mode
            # Decision 2: master role moves only via explicit master-transfer action.
            target = str(data.get("master_device") or "").strip()
            if target:
                _set_master(session, target)
            session["revision"] = int(session.get("revision") or 1) + 1
            _save(session)
            _fanout(session)
            return 200, {"session": _public(session)}

        if action == "master":
            if session is None:
                return _err(404, "No collaboration session to configure")
            me_id = _device_id()
            if me_id != session.get("master_device") and me_id != session.get("host_device"):
                return _err(403, "Only the master may transfer the master role")
            target = str(data.get("master_device") or "").strip()
            if not target:
                return _err(400, "master_device is required")
            if not _find_member(session, target):
                return _err(404, "That device is not a session member")
            _set_master(session, target)
            session["revision"] = int(session.get("revision") or 1) + 1
            _save(session)
            _fanout(session)
            return 200, {"session": _public(session)}
        # action == "local_initiator"
        if action == "local_initiator":
            me_id = _device_id()
            if str(data.get("device_id") or me_id).strip() != me_id:
                return _err(403, "A member claims its local initiator only for itself")
            mine = _find_member(session, me_id)
            if mine is None:
                return _err(404, "You are not a member of this session")
            chat = _clean_name(data.get("local_initiator_chat")) or "principal"
            mine["local_initiator_chat"] = chat
            session["revision"] = int(session.get("revision") or 1) + 1
            _save(session)
            _fanout(session)
            return 200, {"session": _public(session)}
        if action == "brief":
            if session is None:
                return _err(404, "No collaboration session to brief")
            me_id = _device_id()
            master = str(session.get("master_device") or session.get("host_device") or "")
            if me_id != master:
                return _err(403, "Only the master may send the brief")
            text = str(data.get("text") or "").strip()
            if not text:
                return _err(400, "A brief text is required")
            try:
                import peers as _peers  # type: ignore
            except Exception:
                return _err(503, "Station links are unavailable")
            tag = "[COLLAB BRIEF " + str(session.get("id") or "") + "]"
            sender = "collab:" + str(session.get("id") or "")
            results: List[Dict[str, Any]] = []
            for m in session.get("members", []):
                if not isinstance(m, dict) or m.get("device_id") == me_id:
                    continue
                chat = str(m.get("local_initiator_chat") or "principal")
                card = m.get("station_card") or {}
                station = str(card.get("station_id") or "").lstrip("@").strip()
                if not station:
                    pid = str(m.get("peer_id") or "")
                    p = _peers.get(pid) if pid else None
                    station = _peers.slug(p) if p else ""
                if not station:
                    line = "no linked station on record for this member"
                else:
                    line = _peers.send_mail(chat + "@" + station, tag + " " + text,
                                            sender)
                m["brief"] = {"sent_at": time.time(), "status": line[:300]}
                results.append({"device_id": m.get("device_id"), "status": line[:300]})
            session["revision"] = int(session.get("revision") or 1) + 1
            _save(session)
            _fanout(session)
            return 200, {"session": _public(session), "deliveries": results}

        # action == "plans"
        if action == "plans":
            if session is None:
                return _err(404, "No collaboration session")
            plans = {}
            for m in session.get("members", []):
                if isinstance(m, dict) and isinstance(m.get("plans"), dict):
                    plans[str(m.get("device_id"))] = m["plans"]
            return 200, {"plans": plans}

        # action == "scope"
        key = data.get("key")
        if key not in SCOPE_KEYS:
            return _err(400, "Unknown scope; expected one of threads, workers, terminals")
        scopes = _norm_scopes(session.get("scopes"))
        if "on" in data:
            scopes[key] = bool(data.get("on"))
        elif "value" in data:
            scopes[key] = bool(data.get("value"))
        elif "enabled" in data:
            scopes[key] = bool(data.get("enabled"))
        else:
            return _err(400, "A boolean 'on' is required")
        session["scopes"] = scopes
        _save(session)
        return 200, {"session": _public(session)}

def _plan_stub(plan: Any, workers: Any) -> Dict[str, Any]:
    """<=4 KB summary stub kept in the session doc (decision 3); full plan lives on the track."""
    summary = str(plan or "")[:4096]
    while len(summary.encode("utf-8")) > 4096:
        summary = summary[: len(summary) // 2]
    if not isinstance(workers, list):
        workers = []
    return {"summary": summary, "workers": workers[:50], "updated_at": time.time()}

def accept_plan(from_peer: str, body: Optional[Dict[str, Any]] = None) -> Result:
    """A member station publishes its local initiator's plan (stub) into the session."""
    data: Dict[str, Any] = dict(body) if isinstance(body, dict) else {}
    pid = str(from_peer or "").strip()
    did = str(data.get("device_id") or "").strip()
    if not pid or not did:
        return _err(400, "peer and device_id are required")
    with _LOCK:
        session = _load()
        if session is None:
            return _err(404, "No collaboration session")
        member = _find_member(session, did)
        if member is None:
            return _err(404, "That device is not a session member")
        if str(member.get("peer_id") or "") != pid:
            return _err(403, "Plans must come through the member's own station link")
        member["plans"] = {did: _plan_stub(data.get("plan"), data.get("workers"))}
        session["revision"] = int(session.get("revision") or 1) + 1
        _save(session)
    return 200, {"ok": True}

def _set_master(session: Dict[str, Any], target: str) -> None:
    """Explicit master transfer (decision 2). Never called by handover/failover."""
    session["master_device"] = target

def apply_peer_sync(body: Optional[Dict[str, Any]] = None, from_peer: str = "") -> Result:
    """Apply a session snapshot received over a station link (peer fanout).

    This keeps member stations in sync with the hosting station's session state.
    Only intended for peer-authenticated routes (/api/peer/collab/sync).
    """
    data: Dict[str, Any] = body if isinstance(body, dict) else {}
    incoming = data.get("session")
    with _LOCK:
        if incoming is None:
            ended_id = data.get("session_id")
            cur = _load()
            if cur is None:
                return 200, {"ok": True, "session": None}
            # Only the session we are in can be ended, and only by naming it.
            if not ended_id or cur.get("id") != ended_id:
                return 200, {"ok": True, "ignored": True}
            via = str(cur.get("engine_via") or data.get("via") or "")
            _save(None)
            if via:
                _relay(None, via, ended_id=ended_id)  # our coaches learn the session ended too
            return 200, {"ok": True, "session": None}
        if not isinstance(incoming, dict) or not incoming.get("id"):
            return _err(400, "A valid session snapshot is required")
        cur = _load()
        me = _device_id()
        ids = {me}
        # A coach is listed under the id of its engine's link to it; the engine names that card ("you").
        you = str(data.get("you") or "").strip()
        if you and any(isinstance(m, dict) and m.get("device_id") == you and m.get("via")
                       for m in incoming.get("members", [])):
            ids.add(you)
            incoming["self_device"] = you
        elif cur and cur.get("id") == incoming.get("id") and cur.get("self_device"):
            ids.add(str(cur["self_device"]))
            incoming["self_device"] = cur["self_device"]
        in_incoming = any(isinstance(m, dict) and m.get("device_id") in ids for m in incoming.get("members", []))
        if not in_incoming:
            if cur and cur.get("id") == incoming.get("id"):
                # We were removed from this session: drop it locally.
                _save(None)
                return 200, {"ok": True, "session": None, "removed": True}
            # A snapshot of a session we are not in must never replace ours.
            return _err(403, "This station is not a member of that session")
        if cur and cur.get("id") != incoming.get("id"):
            return _err(409, "This station is already coupled to another session")
        # Monotonic revision wins; prevents stale fanout from rewinding state.
        if cur and cur.get("id") == incoming.get("id"):
            try:
                if int(incoming.get("revision") or 0) < int(cur.get("revision") or 0):
                    return 200, {"ok": True, "session": _public(cur), "stale": True}
            except Exception:
                pass
        # Never trust/persist remote join codes in clear text on member stations
        # beyond what is needed for display; keep snapshot as-is for fidelity.
        incoming.setdefault("scopes", _norm_scopes(incoming.get("scopes")))
        if cur and cur.get("id") == incoming.get("id"):
            # Keep our own link ids for members (peer_id is per-station); a host snapshot never carries ours.
            known = {str(m.get("device_id") or ""): m.get("peer_id") for m in cur.get("members", [])
                     if isinstance(m, dict) and m.get("peer_id")}
            for m in incoming.get("members", []):
                # peer_id is per-station: our own known link id always wins, even when the
                # snapshot carries one from the sender's namespace (e.g. after a hand-back).
                k = known.get(str(m.get("device_id") or "")) if isinstance(m, dict) else None
                if isinstance(m, dict) and k:
                    m["peer_id"] = k
        if from_peer:
            # The sender of a host snapshot is the host: remember our link to it, so a later
            # hand-back from another engine can still reach it (_engine_peer_ids reads peer_id).
            # Fanout always carries `via`, so via alone must not suppress the stamp; but an
            # engine relaying someone else's snapshot (sender != host) must never stamp it.
            host = str(incoming.get("host_device") or "")
            sender_dev = ""
            if host:
                try:
                    import peers  # type: ignore
                    sender_dev = str((peers.get(from_peer) or {}).get("device_id") or "")
                except Exception:
                    sender_dev = ""
            if host and (sender_dev == host or data.get("sender_is_host") is True or (not sender_dev and not data.get("via"))):
                for m in incoming.get("members", []):
                    if isinstance(m, dict) and m.get("device_id") == host and not m.get("via"):
                        m["peer_id"] = from_peer
        via = str(data.get("via") or incoming.get("engine_via") or "")
        if via:
            incoming["engine_via"] = via  # our host-namespace peer_id; coaches hang off it
        _save(incoming)
        if via:
            _relay(incoming, via)  # engines pass the snapshot down; coaches have no coaches
        return 200, {"ok": True, "session": _public(incoming)}


def couple_from_peer(peer_id: str, body: Optional[Dict[str, Any]] = None) -> Result:
    """A linked station asks to couple into the session hosted here. Its link, not its body, decides its peer_id."""
    data: Dict[str, Any] = dict(body) if isinstance(body, dict) else {}
    pid = str(peer_id or "").strip()
    did = str(data.get("device_id") or "").strip()
    if not pid or not did:
        return _err(400, "A linked station and its device_id are required")
    with _LOCK:
        session = _load()
        if session is not None:
            if did == session.get("host_device"):
                return _err(403, "A linked station cannot couple as the host device")
            other = _find_member(session, did)
            if other is not None and str(other.get("peer_id") or "") not in ("", pid):
                return _err(403, "That device is coupled through another station")
    data["peer_id"] = pid
    data["device_id"] = did
    result = handle_post("/api/collab/join", data)
    status, payload = result
    if isinstance(payload, dict) and "session" not in payload:
        # Refusal: attach the mode so the caller can decide before re-coupling.
        with _LOCK:
            session = _load()
        if session is not None:
            payload = dict(payload)
            payload["billing_mode"] = session.get("billing_mode") or "A"
        return status, payload
    return result


def couple_remote(station: str, code: str) -> Result:
    """Couple this station into a session hosted on a linked station (by slug or peer_id)."""
    try:
        import peers  # type: ignore
    except Exception:
        return _err(503, "Station links are unavailable")
    station = str(station or "").lstrip("@").strip()
    p = peers.get(station) or peers.by_slug(station)
    if not p:
        return _err(404, "No linked station called " + station)
    if not _norm_code(code):
        return _err(400, "A couple code is required")
    body = _member_from_body({}, "member")  # this station's own identity and station card
    body.pop("peer_id", None)
    body["code"] = code
    # Train (item 38): this engine hands the host the coaches its owner chose to share.
    try:
        import peers as _peers  # type: ignore
        if _peers.train_role() == "engine":
            body["coaches"] = _peers.shared_coach_members()
    except Exception:
        body["coaches"] = []
    res = peers.call(p["peer_id"], "POST", "/api/peer/collab/couple", body, timeout=15)
    status = int(res.get("status") or 0)
    if status != 200:
        msg = (res.get("data") or {}).get("error") or res.get("error") or "the linked station refused"
        return _err(status if status >= 400 else 502, str(msg))
    # The host fans the full session back to us over /api/peer/collab/sync; return its public view now.
    return 200, {"session": (res.get("data") or {}).get("session"), "host_station": peers.slug(p)}

HANDOVER_MISSES = 3  # consecutive unanswered pings before a backup engine takes the hub
_HOST_MISSES: Dict[str, int] = {}


def _set_host(session: Dict[str, Any], to_device_id: str) -> Dict[str, Any]:
    """Point the session at a new host engine and fix the two members' roles. Caller holds the lock."""
    old = str(session.get("host_device") or "")
    to = _find_member(session, to_device_id)
    session["host_device"] = to_device_id
    session["host"] = str((to or {}).get("name") or session.get("host") or "")
    session["handover_from"] = old
    session["handover_at"] = time.time()
    session["revision"] = int(session.get("revision") or 1) + 1
    for m in session.get("members", []):
        if not isinstance(m, dict):
            continue
        if m.get("device_id") == to_device_id:
            m["role"] = "host"
        elif m.get("device_id") == old:
            m["role"] = "member"
    _save(session)
    _fanout(session)
    return session


def _handover_ok(session: Dict[str, Any], to: str) -> str:
    """Why this handover target is not acceptable, or '' when it is."""
    if not to:
        return "A target device_id is required"
    if to == str(session.get("host_device") or ""):
        return "This device already hosts the session"
    target = _find_member(session, to)
    if target is None:
        return "That device is not in this session"
    if target.get("via"):
        return "Hand over to an engine, not a coach: a coach rides under its own engine"
    return ""


def handover(to_device_id: str, from_peer: str = "", session: Optional[Dict[str, Any]] = None) -> Result:
    """Move the session host to another engine. Host-only; a linked engine may ask for itself."""
    to = str(to_device_id or "").strip()
    if session is None:
        with _LOCK:
            session = _load()
            if session is None:
                return _err(404, "No collaboration session is active")
            me = _device_id()
            if str(session.get("host_device") or "") != me:
                return _err(403, "Only the host can hand over this session")
            if from_peer:
                try:
                    import peers  # type: ignore
                    caller = peers.get(from_peer) or {}
                except Exception:
                    caller = {}
                if str(caller.get("device_id") or "") != to:
                    return _err(403, "A linked engine may only ask the hub for itself")
            reason = _handover_ok(session, to)
            if reason:
                return _err(400, reason)
            session = _set_host(session, to)
            return 200, {"session": _public(session), "host_device": to}
    me = _device_id()
    if str(session.get("host_device") or "") != me:
        return _err(403, "Only the host can hand over this session")
    if from_peer:
        try:
            import peers  # type: ignore
            caller = peers.get(from_peer) or {}
        except Exception:
            caller = {}
        if str(caller.get("device_id") or "") != to:
            return _err(403, "A linked engine may only ask the hub for itself")
    reason = _handover_ok(session, to)
    if reason:
        return _err(400, reason)
    session = _set_host(session, to)
    return 200, {"session": _public(session), "host_device": to}


def _take_over(session: Dict[str, Any], me: str) -> Result:
    """Backup engine promotes itself after the hub stops answering. Caller holds no lock."""
    with _LOCK:
        cur = _load()
        if cur is None or cur.get("id") != session.get("id"):
            return _err(409, "The session changed while taking over")
        if str(cur.get("host_device") or "") == me:
            return 200, {"session": _public(cur), "host_device": me}
        cur = _set_host(cur, me)
        return 200, {"session": _public(cur), "host_device": me}


def maybe_handover() -> str:
    """Heartbeat hook: promote exactly one backup engine when the host engine stops answering.

    Returns '' (nothing to do), 'host' (this station took the hub), or 'waiting'.
    """
    session = _load()
    if not session:
        return ""
    me = _device_id()
    host = str(session.get("host_device") or "")
    if not host or host == me:
        return ""
    mine = _find_member(session, me)
    if mine is None or mine.get("via"):
        return ""  # only engines in the session may take the hub
    try:
        import peers  # type: ignore
    except Exception:
        return ""
    link = peers.by_device(host)
    if not link:
        return ""  # the hub is not a linked station here, so it cannot be pinged
    res = peers.call(link["peer_id"], "GET", "/api/peer/station", timeout=5)
    if res.get("status"):
        _HOST_MISSES.pop(host, None)
        return ""
    misses = int(_HOST_MISSES.get(host) or 0) + 1
    _HOST_MISSES[host] = misses
    if misses < HANDOVER_MISSES:
        return "waiting"
    engines = [m for m in session.get("members", [])
               if isinstance(m, dict) and m.get("device_id") and not m.get("via") and m.get("device_id") != host]
    # Same choice on every station: oldest engine still in the session, device_id breaks ties.
    engines.sort(key=lambda m: (float(m.get("joined_at") or 0), str(m.get("device_id") or "")))
    if not engines or engines[0].get("device_id") != me:
        return "waiting"
    _HOST_MISSES.pop(host, None)
    status, _ = _take_over(session, me)
    return "host" if status == 200 else "waiting"
