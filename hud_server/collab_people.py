"""Collab People: station-local members, one-time invites, scope shares, and audit API.

Identity is supplied by the authenticated HTTP handler, never by JSON payload fields.
The station's paired-device registry remains the source of truth for active principals.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

PREFIX = "/api/collab/people"
SHARES_PREFIX = "/api/collab/people/shares"
ROLES = {"owner", "admin", "member", "observer"}
SCOPES = {"threads", "workers", "terminals"}
INVITE_TTL = 15 * 60
MAX_AUDIT = 5000
RATE_LIMITS = {"invite_create": (5, 3600), "invite_accept": (10, 900)}

Result = Tuple[int, Dict[str, Any]]
_LOCAL_LOCK = threading.RLock()


def _path() -> str:
    override = os.environ.get("SUBWAY_TEAMS_FILE")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    import subway_home
    new_path = os.path.join(subway_home.subway_dir(), "collab_people.json")
    if not os.path.exists(new_path):
        old_path = os.path.join(subway_home.subway_dir(), "collaboration.json")
        if os.path.exists(old_path):
            os.replace(old_path, new_path)
    return new_path


def _empty() -> Dict[str, Any]:
    return {"version": 1, "teams": [], "sessions": [], "audit": [], "audit_seq": 0, "rate_limits": {}}


def _load() -> Dict[str, Any]:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError):
        return {**_empty(), "_corrupt": True}
    if not isinstance(data, dict) or not isinstance(data.get("teams"), list):
        return {**_empty(), "_corrupt": True}
    teams = data.get("teams")
    sessions = data.get("sessions", [])
    audit = data.get("audit", [])
    valid_teams = isinstance(teams, list) and all(
        isinstance(t.get("id"), str) and isinstance(t.get("name"), str)
        and isinstance(t.get("owner_device_id"), str)
        and isinstance(t.get("members"), list) and all(isinstance(m, dict) for m in t["members"])
        and isinstance(t.get("invites", []), list) and all(isinstance(i, dict) for i in t.get("invites", []))
        for t in teams if isinstance(t, dict)
    ) and all(isinstance(t, dict) for t in teams)
    valid_sessions = isinstance(sessions, list) and all(
        isinstance(s.get("id"), str) and isinstance(s.get("team_id"), str)
        and isinstance(s.get("name"), str) and isinstance(s.get("offers", {}), dict)
        and isinstance(s.get("grants", {}), dict)
        for s in sessions if isinstance(s, dict)
    ) and all(isinstance(s, dict) for s in sessions)
    if (not valid_teams or not valid_sessions or not isinstance(audit, list)
            or any(not isinstance(e, dict) for e in audit)):
        return {**_empty(), "_corrupt": True}
    data["version"] = 1
    data["sessions"] = sessions
    data["audit"] = audit
    try:
        data["audit_seq"] = max(0, int(data.get("audit_seq") or 0))
    except (TypeError, ValueError):
        return {**_empty(), "_corrupt": True}
    limits = data.get("rate_limits", {})
    data["rate_limits"] = limits if isinstance(limits, dict) else {}
    return data


def _save(data: Dict[str, Any]) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".collaboration-", dir=os.path.dirname(path))
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True, separators=(",", ":"))
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        # SSE is a change hint only. It carries no names, invite material, or session content;
        # clients refresh authorized state through the ordinary API.
        try:
            rows = data.get("audit") or []
            last = rows[-1] if rows else {}
            if isinstance(last, dict) and last.get("team_id"):
                import station
                import sse_stream
                sse_stream.publish("collab_people", {
                    "action": str(last.get("action") or "updated"),
                    "team_id": str(last.get("team_id") or ""),
                    "station_id": station.get_station_id(),
                    "cursor": int(last.get("seq") or 0),
                })
        except Exception:
            # SSE is an opportunistic hint; durable state is read via GET and no mutation rolls back.
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextlib.contextmanager
def _locked():
    """Serialize team state across threads and, where available, station processes."""
    with _LOCAL_LOCK:
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lock_path = path + ".lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
            except ImportError:  # Windows: in-process lock remains available.
                pass
            yield
        finally:
            try:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
            except ImportError:
                pass
            os.close(fd)


def _err(code: int, msg: str, reason: str = "") -> Result:
    out: Dict[str, Any] = {"error": msg}
    if reason:
        out["reason"] = reason
    return code, out


def _hit_limit(data: Dict[str, Any], key: str, limit: int, window: int) -> bool:
    now = time.time()
    rows = data.setdefault("rate_limits", {}).get(key, [])
    if not isinstance(rows, list):
        rows = []
    clean = []
    for value in rows:
        try:
            stamp = float(value)
            if now - stamp < window:
                clean.append(stamp)
        except (TypeError, ValueError):
            continue
    rows = clean
    if len(rows) >= limit:
        data["rate_limits"][key] = rows
        return True
    rows.append(now)
    data["rate_limits"][key] = rows
    return False


def _is_expired(value: Any) -> bool:
    try:
        return float(value) < time.time()
    except (TypeError, ValueError):
        return True


def _audit(data: Dict[str, Any], actor: str, action: str, team_id: str,
           target: str = "", outcome: str = "ok") -> None:
    rows = data.get("audit")
    if not isinstance(rows, list):
        rows = []
    seq = max(int(data.get("audit_seq") or 0), max((int(e.get("seq") or 0) for e in rows), default=0)) + 1
    data["audit_seq"] = seq
    rows.append({"seq": seq, "id": secrets.token_hex(12), "at": time.time(), "actor": actor,
                 "action": action, "team_id": team_id, "target": target,
                 "outcome": outcome})
    data["audit"] = rows[-MAX_AUDIT:]


def _team(data: Dict[str, Any], team_id: str) -> Optional[Dict[str, Any]]:
    return next((t for t in data["teams"] if isinstance(t, dict) and t.get("id") == team_id), None)


def _member(team: Dict[str, Any], actor: str) -> Optional[Dict[str, Any]]:
    return next((m for m in team.get("members", []) if isinstance(m, dict)
                 and m.get("device_id") == actor and m.get("status", "active") == "active"), None)


def _active_device(device_id: str) -> bool:
    if not device_id or device_id == "master":
        return False
    try:
        import pairing
        return any(d.get("device_id") == device_id and not d.get("revoked")
                   for d in pairing.list_devices())
    except Exception:
        return False


def _role(team: Dict[str, Any], actor: str, master: bool) -> str:
    if master:
        return "master"
    m = _member(team, actor)
    return str(m.get("role")) if m else ""


def _admin(team: Dict[str, Any], actor: str, master: bool) -> bool:
    return master or _role(team, actor, False) in ("owner", "admin")


def _summary(team: Dict[str, Any], actor: str, master: bool) -> Dict[str, Any]:
    return {"id": team["id"], "name": team["name"], "created_at": team["created_at"],
            "owner_device_id": team["owner_device_id"], "role": _role(team, actor, master),
            "members": [{k: m.get(k) for k in ("device_id", "name", "role", "joined_at")}
                        for m in team.get("members", []) if m.get("status", "active") == "active"]}


def _split(path: str) -> List[str]:
    parts = [unquote(p) for p in urlsplit(path).path.strip("/").split("/") if p]
    # /api/collab/people/shares/... -> ["api", "shares", ...]; /api/collab/people/... -> ["api", "people", ...]
    if parts[:3] == ["api", "collab", "people"]:
        rest = parts[3:]
        if rest[:1] == ["shares"]:
            return ["api", "shares"] + rest[1:]
        return ["api", "people"] + rest
    return parts


def handle_get(path: str, actor: str, master: bool = False) -> Optional[Result]:
    parts = _split(path)
    if not parts or parts[0] not in ("api",):
        return None
    if len(parts) < 2 or parts[1] not in ("people", "shares"):
        return None
    if parts[1] == "people" and len(parts) == 2:
        with _locked():
            data = _load()
            if data.get("_corrupt"):
                return _err(500, "Team state is corrupt; refusing to overwrite it", "state_corrupt")
            rows = [_summary(t, actor, master) for t in data["teams"]
                    if isinstance(t, dict) and (master or _member(t, actor))]
            return 200, {"teams": rows}
    if parts[1] == "people" and len(parts) == 3:
        with _locked():
            data = _load()
            if data.get("_corrupt"):
                return _err(500, "Team state is corrupt; refusing to overwrite it", "state_corrupt")
            team = _team(data, parts[2])
            if not team:
                return _err(404, "Team not found")
            if not master and not _member(team, actor):
                return _err(403, "Active team membership is required", "not_a_member")
            return 200, {"team": _summary(team, actor, master)}
    if parts[1] == "people" and len(parts) == 4 and parts[3] == "audit":
        with _locked():
            data = _load()
            if data.get("_corrupt"):
                return _err(500, "Team state is corrupt; refusing to overwrite it", "state_corrupt")
            team = _team(data, parts[2])
            if not team:
                return _err(404, "Team not found")
            if not _admin(team, actor, master):
                return _err(403, "Team owner or admin role is required", "forbidden")
            query = parse_qs(urlsplit(path).query)
            raw_after = (query.get("after") or ["0"])[0]
            raw_limit = (query.get("limit") or ["100"])[0]
            try:
                after = max(0, int(raw_after))
                limit = max(1, min(int(raw_limit), 500))
            except ValueError:
                return _err(400, "after and limit must be integers")
            events = [e for e in data.get("audit", []) if e.get("team_id") == team["id"] and
                      int(e.get("seq") or 0) > after]
            page = events[:limit]
            return 200, {"events": page, "next": page[-1]["seq"] if len(events) > limit and page else None}
    if parts[1] == "people" and len(parts) == 4 and parts[3] == "shares":
        with _locked():
            data = _load()
            if data.get("_corrupt"):
                return _err(500, "Team state is corrupt; refusing to overwrite it", "state_corrupt")
            team = _team(data, parts[2])
            if not team:
                return _err(404, "Team not found")
            if not master and not _member(team, actor):
                return _err(403, "Active team membership is required", "not_a_member")
            sessions = [s for s in data["sessions"] if s.get("team_id") == team["id"] and not s.get("ended_at")]
            return 200, {"sessions": [{"id": s["id"], "team_id": s["team_id"], "name": s["name"],
                                          "created_at": s["created_at"]} for s in sessions]}
    if parts[1] == "shares" and len(parts) == 3:
        with _locked():
            data = _load()
            if data.get("_corrupt"):
                return _err(500, "Team state is corrupt; refusing to overwrite it", "state_corrupt")
            session = next((s for s in data["sessions"] if s.get("id") == parts[2]), None)
            if not session:
                return _err(404, "Session not found")
            team = _team(data, session["team_id"])
            if not team or (not master and not _member(team, actor)):
                return _err(403, "Active team membership is required", "not_a_member")
            grants = session.get("grants", {}).get(actor, {})
            offered = session.get("offers", {}).get(actor, [])
            return 200, {"session": {"id": session["id"], "team_id": session["team_id"],
                                      "name": session["name"], "created_at": session["created_at"],
                                      "ended_at": session.get("ended_at"), "offered_scopes": offered,
                                      "my_consent": grants}}
    return _err(404, "Unknown team endpoint")


def handle_post(path: str, body: Any, actor: str, master: bool = False,
                actor_name: str = "Device") -> Optional[Result]:
    parts = _split(path)
    if not parts or parts[0] != "api" or len(parts) < 2 or parts[1] not in ("people", "shares"):
        return None
    data_body = body if isinstance(body, dict) else {}
    "".join(parts[2:])
    with _locked():
        data = _load()
        if data.get("_corrupt"):
            return _err(500, "Team state is corrupt; refusing to overwrite it", "state_corrupt")
        # Creating a team establishes the owner from the authenticated identity.
        if parts[1] == "people" and len(parts) == 2:
            name = " ".join(str(data_body.get("name") or "").split())[:80]
            if not name:
                return _err(400, "Team name is required")
            if not master and not _active_device(actor):
                return _err(403, "An active paired device is required", "device_inactive")
            team = {"id": "team_" + secrets.token_hex(12), "name": name,
                    "created_at": time.time(), "owner_device_id": actor if not master else "master",
                    "members": [{"device_id": actor if not master else "master", "name": actor_name[:80],
                                 "role": "owner", "joined_at": time.time(), "status": "active"}],
                    "invites": [], "version": 1}
            data["teams"].append(team)
            _audit(data, "master" if master else actor, "team.create", team["id"])
            _save(data)
            return 201, {"team": _summary(team, actor, master)}

        # Invite acceptance is addressed by one-time secret and authenticated member identity.
        if parts[1] == "people" and len(parts) == 5 and parts[3:] == ["invites", "accept"]:
            requested_team_id = parts[2]
            raw = str(data_body.get("invite") or "")
            digest = hashlib.sha256(raw.encode()).hexdigest()
            if not raw or not _active_device(actor):
                return _err(403, "An active paired device and invite are required", "device_inactive")
            if _hit_limit(data, f"invite_accept:{actor}", *RATE_LIMITS["invite_accept"]):
                _save(data)
                return _err(429, "Too many invite attempts; try again later", "rate_limited")
            for team in data["teams"]:
                invite = next((i for i in team.get("invites", [])
                               if secrets.compare_digest(i.get("secret_sha256", ""), digest)), None)
                if invite and team.get("id") == requested_team_id:
                    if invite.get("used_at") or invite.get("revoked_at") or _is_expired(invite.get("expires_at")):
                        _audit(data, actor, "invite.accept", team["id"], invite["id"], outcome="denied")
                        _save(data)
                        return _err(410, "Invite is expired, revoked, or already used", "invite_unavailable")
                    if _member(team, actor):
                        return _err(409, "Device is already a team member")
                    invite["used_at"] = time.time()
                    invite["accepted_by"] = actor
                    team["members"].append({"device_id": actor, "name": actor_name[:80],
                                            "role": invite["role"], "joined_at": time.time(), "status": "active"})
                    _audit(data, actor, "invite.accept", team["id"], invite["id"])
                    _save(data)
                    return 200, {"team": _summary(team, actor, False)}
            _audit(data, actor, "invite.accept", requested_team_id, outcome="denied")
            _save(data)
            return _err(404, "Invite not found", "invite_invalid")

        # Team creation/list and invite acceptance are the only ID-less routes.
        if parts[1] == "people" and len(parts) < 3:
            return _err(404, "Unknown team endpoint")
        if parts[1] == "people":
            team = _team(data, parts[2])
            if not team:
                return _err(404, "Team not found")
            if not master and not _member(team, actor):
                return _err(403, "Active team membership is required", "not_a_member")
            if len(parts) == 4 and parts[3] == "invites":
                if not _admin(team, actor, master):
                    return _err(403, "Team owner or admin role is required", "forbidden")
                if _hit_limit(data, f"invite_create:{team['id']}:{actor}", *RATE_LIMITS["invite_create"]):
                    _save(data)
                    return _err(429, "Too many invites created; try again later", "rate_limited")
                active_invites = [i for i in team.get("invites", []) if isinstance(i, dict) and not i.get("used_at") and not i.get("revoked_at") and not _is_expired(i.get("expires_at"))]
                if len(active_invites) >= 50:
                    return _err(429, "This team already has 50 active invites", "invite_limit")
                role = str(data_body.get("role") or "member")
                if role not in ("member", "observer"):
                    return _err(400, "Invites may grant only member or observer role")
                raw = secrets.token_urlsafe(32)
                invite = {"id": "inv_" + secrets.token_hex(10),
                          "secret_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                          "created_by": "master" if master else actor, "role": role,
                          "created_at": time.time(), "expires_at": time.time() + INVITE_TTL}
                team["invites"].append(invite)
                _audit(data, "master" if master else actor, "invite.create", team["id"], invite["id"])
                _save(data)
                return 201, {"invite": {"id": invite["id"], "secret": raw,
                                        "role": role, "expires_at": invite["expires_at"]}}
            if len(parts) == 6 and parts[3] == "invites" and parts[5] == "revoke":
                if not _admin(team, actor, master):
                    return _err(403, "Team owner or admin role is required", "forbidden")
                invite = next((i for i in team.get("invites", []) if i.get("id") == parts[4]), None)
                if not invite:
                    return _err(404, "Invite not found")
                if invite.get("used_at"):
                    return _err(409, "Invite has already been used")
                invite["revoked_at"] = time.time()
                _audit(data, "master" if master else actor, "invite.revoke", team["id"], invite["id"])
                _save(data)
                return 200, {"ok": True}
            if len(parts) == 4 and parts[3] == "rename":
                if not _admin(team, actor, master):
                    return _err(403, "Team owner or admin role is required", "forbidden")
                name = " ".join(str(data_body.get("name") or "").split())[:80]
                if not name:
                    return _err(400, "Team name is required")
                team["name"] = name
                _audit(data, "master" if master else actor, "team.rename", team["id"])
                _save(data)
                return 200, {"team": _summary(team, actor, master)}
            if len(parts) == 5 and parts[3:] == ["members", "role"]:
                if not _admin(team, actor, master):
                    return _err(403, "Team owner or admin role is required", "forbidden")
                target = str(data_body.get("device_id") or "")
                role = str(data_body.get("role") or "")
                member = next((m for m in team["members"] if m.get("device_id") == target and m.get("status", "active") == "active"), None)
                if not member:
                    return _err(404, "Active member not found")
                if target == team["owner_device_id"] or role not in ("admin", "member", "observer"):
                    return _err(400, "Owner role is immutable here; role must be admin, member, or observer")
                if role == "admin" and _role(team, actor, master) != "owner" and not master:
                    return _err(403, "Only the owner may assign an admin role")
                if _role(team, actor, master) == "admin" and member.get("role") == "admin":
                    return _err(403, "Admins cannot change another admin")
                member["role"] = role
                _audit(data, "master" if master else actor, "member.role", team["id"], target)
                _save(data)
                return 200, {"team": _summary(team, actor, master)}
            if len(parts) == 5 and parts[3:] == ["members", "remove"]:
                if not _admin(team, actor, master):
                    return _err(403, "Team owner or admin role is required", "forbidden")
                target = str(data_body.get("device_id") or "")
                member = next((m for m in team["members"] if m.get("device_id") == target and m.get("status", "active") == "active"), None)
                if not member:
                    return _err(404, "Active member not found")
                if target == team["owner_device_id"]:
                    return _err(400, "The owner cannot be removed")
                if _role(team, actor, master) == "admin" and member.get("role") == "admin":
                    return _err(403, "Admins cannot remove another admin")
                member["status"] = "removed"
                member["removed_at"] = time.time()
                for session in data["sessions"]:
                    if session.get("team_id") == team["id"]:
                        session.get("grants", {}).pop(target, None)
                        session.get("offers", {}).pop(target, None)
                _audit(data, "master" if master else actor, "member.remove", team["id"], target)
                _save(data)
                return 200, {"ok": True}
            if len(parts) == 5 and parts[3:] == ["owner", "transfer"]:
                if _role(team, actor, master) != "owner" and not master:
                    return _err(403, "Only the owner can offer ownership transfer", "forbidden")
                target = str(data_body.get("device_id") or "")
                target_member = _member(team, target)
                if not target_member or target == team.get("owner_device_id"):
                    return _err(404, "Active target member not found")
                if not _active_device(target):
                    return _err(409, "Target device is no longer paired", "device_inactive")
                team["pending_owner_transfer"] = {"device_id": target, "offered_by": actor,
                                                  "expires_at": time.time() + 24 * 60 * 60}
                _audit(data, "master" if master else actor, "owner.transfer_offer", team["id"], target)
                _save(data)
                return 200, {"ok": True, "target_device_id": target,
                             "expires_at": team["pending_owner_transfer"]["expires_at"]}
            if len(parts) == 5 and parts[3:] == ["owner", "accept"]:
                offer = team.get("pending_owner_transfer")
                if not isinstance(offer, dict) or offer.get("device_id") != actor or _is_expired(offer.get("expires_at")):
                    return _err(404, "No ownership transfer is pending for this device")
                if not _active_device(actor):
                    return _err(409, "Target device is no longer paired", "device_inactive")
                previous = str(team.get("owner_device_id") or "")
                old_owner = _member(team, previous)
                new_owner = _member(team, actor)
                if not old_owner or not new_owner:
                    return _err(409, "Ownership transfer membership state is invalid")
                old_owner["role"] = "admin"
                new_owner["role"] = "owner"
                team["owner_device_id"] = actor
                team.pop("pending_owner_transfer", None)
                _audit(data, actor, "owner.transfer_accept", team["id"], previous)
                _save(data)
                return 200, {"team": _summary(team, actor, False)}
            if len(parts) == 4 and parts[3] == "leave":
                member = _member(team, actor)
                if not member:
                    return _err(404, "Active membership not found")
                if actor == team["owner_device_id"]:
                    return _err(409, "Transfer ownership or delete the team before leaving")
                member["status"] = "removed"
                member["removed_at"] = time.time()
                for session in data["sessions"]:
                    if session.get("team_id") == team["id"]:
                        session.get("grants", {}).pop(actor, None)
                        session.get("offers", {}).pop(actor, None)
                _audit(data, actor, "member.leave", team["id"], actor)
                _save(data)
                return 200, {"ok": True}
            if len(parts) == 4 and parts[3] == "shares":
                if not _admin(team, actor, master):
                    return _err(403, "Team owner or admin role is required", "forbidden")
                name = " ".join(str(data_body.get("name") or "").split())[:80]
                if not name:
                    return _err(400, "Session name is required")
                session = {"id": "sess_" + secrets.token_hex(12), "team_id": team["id"],
                           "name": name, "owner_device_id": actor, "created_at": time.time(),
                           "offers": {}, "grants": {}}
                data["sessions"].append(session)
                _audit(data, "master" if master else actor, "session.create", team["id"], session["id"])
                _save(data)
                return 201, {"session": {k: session[k] for k in ("id", "team_id", "name", "created_at")}}
            return _err(404, "Unknown team endpoint")

        if parts[1] == "shares":
            if len(parts) < 3:
                return _err(404, "Unknown session endpoint")
            session = next((s for s in data["sessions"] if s.get("id") == parts[2]), None)
            if not session:
                return _err(404, "Session not found")
            team = _team(data, session["team_id"])
            if not team or (not master and not _member(team, actor)):
                return _err(403, "Active team membership is required", "not_a_member")
            if session.get("ended_at"):
                return _err(410, "Session has ended")
            if len(parts) == 5 and parts[3:] == ["scopes", "offer"]:
                if not _admin(team, actor, master):
                    return _err(403, "Team owner or admin role is required", "forbidden")
                target = str(data_body.get("device_id") or "")
                scopes = data_body.get("scopes")
                if not _member(team, target):
                    return _err(404, "Active target member not found")
                if not isinstance(scopes, list) or not scopes or any(s not in SCOPES for s in scopes):
                    return _err(400, "scopes must be a non-empty list of threads, workers, terminals")
                session.setdefault("offers", {})[target] = sorted(set(scopes))
                session.setdefault("grants", {}).pop(target, None)
                _audit(data, "master" if master else actor, "scope.offer", team["id"], target)
                _save(data)
                return 200, {"ok": True, "offered_scopes": session["offers"][target]}
            if len(parts) == 4 and parts[3] == "consent":
                scope = str(data_body.get("scope") or "")
                allow = data_body.get("allow")
                if not isinstance(allow, bool) or scope not in SCOPES:
                    return _err(400, "scope and boolean allow are required")
                if master or not _member(team, actor):
                    return _err(403, "Consent must be supplied by an active paired member")
                offered = session.get("offers", {}).get(actor, [])
                if allow and scope not in offered:
                    return _err(409, "This scope has not been offered", "scope_not_offered")
                grants = session.setdefault("grants", {}).setdefault(actor, {})
                grants[scope] = allow
                _audit(data, actor, "consent.grant" if allow else "consent.revoke", team["id"], scope)
                _save(data)
                return 200, {"ok": True, "scope": scope, "allow": allow}
            if len(parts) == 4 and parts[3] == "end":
                if not _admin(team, actor, master):
                    return _err(403, "Team owner or admin role is required", "forbidden")
                session["ended_at"] = time.time()
                session["grants"] = {}
                session["offers"] = {}
                _audit(data, "master" if master else actor, "session.end", team["id"], session["id"])
                _save(data)
                return 200, {"ok": True}
        return _err(404, "Unknown team endpoint")


def require_session_grant(session_id: str, actor: str, scope: str, *, action: bool = False) -> bool:
    """Shared-content handlers must call this before returning data.

    `action=True` always fails closed in v1: data consent is not permission to start work,
    execute terminal commands, or approve a worker. Add separate, narrowly scoped action
    consent only after a distinct threat-model review.
    """
    if action or scope not in SCOPES or not actor or actor == "master":
        return False
    with _locked():
        data = _load()
        if data.get("_corrupt"):
            return False
        session = next((s for s in data["sessions"] if s.get("id") == session_id and not s.get("ended_at")), None)
        team = _team(data, session.get("team_id", "")) if session else None
        return bool(team and _active_device(actor) and _member(team, actor)
                    and session.get("grants", {}).get(actor, {}).get(scope) is True)
