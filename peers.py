"""Linked stations (roadmap 66): who they are, how to reach them, and what each may do here.

A link is made once with a one-time QR code, and works both ways. Each side keeps, in peers.json (0600):
the other station's tunnel identity, the token this station uses to call it, the pairing device record the
other station uses to call us (scope "peer"), and the grants the user gave that link. Every call from a peer
is checked against its grants on THIS station; nothing the caller sends widens them.
"""
import base64
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import pairing
import subway_home

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "subway-peer.mjs")


def _setting(key: str, default: Any = None) -> Any:
    try:
        from hud_server import settings_api as _settings_api
        v = _settings_api.value(key)
        return default if v is None else v
    except Exception:
        return default


GRANT_CHOICES = {
    "chats": ("none", "titles", "decisions", "full"),
    "mail": ("off", "on"),
    "mcp": ("off", "on"),
    "spawn": ("off", "pending", "direct"),
    "approve": ("off", "on"),
    "land": ("off", "patch", "edits", "branch"),
    "claims": ("off", "read", "claim"),
    "events": ("off", "own", "all"),
    "files": ("off", "read", "send"),
    "apps": ("off", "browser", "desktop"),
    "collab": ("off", "on"),
    "swarm": ("off", "on"),
    "skills": ("off", "read"),
    "screen": ("off", "snapshot", "live"),
    "control": ("off", "on"),
}
PRESETS = {
    "personal": {"chats": "full", "mail": "on", "spawn": "direct", "approve": "on", "land": "edits",
                 "claims": "claim", "events": "all", "files": "send", "apps": "off", "collab": "on", "swarm": "on", "skills": "off", "screen": "off", "mcp": "off", "control": "off"},
    "collaborator": {"chats": "decisions", "mail": "on", "spawn": "pending", "approve": "off", "land": "patch",
                     "claims": "claim", "events": "own", "files": "read", "apps": "off", "collab": "on", "swarm": "off", "skills": "off", "screen": "off", "mcp": "off", "control": "off"},
    "observer": {"chats": "titles", "mail": "off", "spawn": "off", "approve": "off", "land": "off",
                 "claims": "read", "events": "own", "files": "off", "apps": "off", "collab": "off", "swarm": "off", "skills": "off", "screen": "off", "mcp": "off", "control": "off"},
}
# Grants that only the designated controller keeps when one is set.
CONTROL_GRANTS = ("spawn", "approve", "land")

# Trains (item 38): one engine per person, its other stations ride as coaches.
TRAIN_ROLES = ("engine", "coach")
RELATIONS = ("own", "partner")

# route -> (grant, values that allow it). Anything not listed is denied to peers.
_GET_ROUTES = {
    "/api/chats": ("chats", ("titles", "decisions", "full")),
    "/api/threads": ("chats", ("titles", "decisions", "full")),
    "/api/thread": ("chats", ("decisions", "full")),
    "/api/mail": ("mail", ("on",)),
    "/api/events": ("events", ("own", "all")),
    "/api/locks": ("claims", ("read", "claim")),
    "/api/workers": ("events", ("own", "all")),
    "/api/worker": ("events", ("own", "all")),
    "/api/audit": ("events", ("all",)),
    "/api/peer/chats": ("chats", ("titles", "decisions", "full")),
    "/api/peer/read_chat": ("chats", ("decisions", "full")),
    "/api/peer/search_chat": ("chats", ("decisions", "full")),
    "/api/peer/worker": ("events", ("own", "all")),
    "/api/peer/claims": ("claims", ("read", "claim")),
    "/api/peer/station": ("events", ("own", "all")),
    "/api/peer/coordination": ("claims", ("read", "claim")),
    "/api/peer/models": ("spawn", ("pending", "direct")),
    "/api/peer/allowance": ("spawn", ("pending", "direct")),
    "/api/peer/skills": ("skills", ("read",)),
    "/api/peer/skill": ("skills", ("read",)),
    "/api/peer/screen": ("screen", ("snapshot", "live")),
    "/api/peer/screen/session": ("screen", ("live",)),
    "/api/peer/patch": ("events", ("own", "all")),
    "/api/peer/files": ("files", ("read", "send")),
    "/api/peer/file": ("files", ("read", "send")),
    "/api/peer/media": ("events", ("own", "all")),
    "/api/peer/swarm/manifest": ("swarm", ("on",)),
    "/api/peer/swarm/bundle": ("swarm", ("on",)),
}
_POST_ROUTES = {
    "/api/mail": ("mail", ("on",)),
    "/api/peer/mail": ("mail", ("on",)),
    "/api/peer/hub_call": ("mcp", ("on",)),
    "/api/peer/screen/request": ("screen", ("live",)),
    "/api/peer/screen/signal": ("screen", ("live",)),
    "/api/spawn": ("spawn", ("pending", "direct")),
    "/api/peer/spawn": ("spawn", ("pending", "direct")),
    "/api/peer/approve_worker": ("approve", ("on",)),
    "/api/approve_worker": ("approve", ("on",)),
    "/api/reject_worker": ("approve", ("on",)),
    "/api/answer_permission": ("approve", ("on",)),
    "/api/claim": ("claims", ("claim",)),
    "/api/peer/collab/sync": ("collab", ("on",)),
    "/api/peer/collab/couple": ("collab", ("on",)),
    "/api/peer/collab/handover": ("collab", ("on",)),
    "/api/peer/collab/plan": ("collab", ("on",)),
    "/api/release": ("claims", ("claim",)),
    "/api/peer/file": ("files", ("send",)),
    "/api/peer/swarm/submit": ("swarm", ("on",)),
}
ALWAYS = {("GET", "/api/status"), ("GET", "/api/peer/hello"), ("POST", "/api/peer/unlink")}


def _data() -> Dict[str, Any]:
    d = pairing._load("peers.json")
    if not isinstance(d.get("peers"), dict):
        d["peers"] = {}
    return d


def _save(d: Dict[str, Any]) -> None:
    pairing._save("peers.json", d)


def normalise_grants(grants: Any, base: str = "observer") -> Dict[str, str]:
    out = dict(PRESETS[base])
    if isinstance(grants, str) and grants in PRESETS:
        return dict(PRESETS[grants])
    if isinstance(grants, dict):
        for k, v in grants.items():
            if k in GRANT_CHOICES and v in GRANT_CHOICES[k]:
                out[k] = v
    return out


def public(peer: Dict[str, Any]) -> Dict[str, Any]:
    """A peer without its token, for lists and the HUD."""
    return {k: v for k, v in peer.items() if k != "token"}


def add(name: str, station: Dict[str, Any], token: str, device_id: str, grants: Any = None,
        relation: str = "partner") -> Dict[str, Any]:
    if grants is None:
        grants = _setting("link_default_preset", "personal")
    d = _data()
    new_station = {k: str(station.get(k) or "")[:300] for k in ("sid", "pk", "relay", "name")}
    new_pk = str(new_station.get("pk") or "")
    if _b64_len(new_pk) == 32:  # only a real station key identifies a station
        for p in d["peers"].values():
            if str((p.get("station") or {}).get("pk") or "") == new_pk:
                alts = p.setdefault("alt_device_ids", [])
                if device_id and device_id != p.get("device_id") and device_id not in alts:
                    alts.append(device_id)
                if not p.get("token") and token:
                    p["token"] = str(token or "")
                p["station"] = new_station
                _save(d)
                return public(p)
    peer_id = "peer_" + secrets.token_hex(5)
    rel = str(relation or "partner").strip().lower()
    d["peers"][peer_id] = {
        "peer_id": peer_id,
        "name": str(name or "Station")[:60],
        "station": new_station,
        "token": str(token or ""),
        "device_id": str(device_id or ""),
        "grants": normalise_grants(grants, "observer"),
        "relation": rel if rel in RELATIONS else "partner",
        "created_at": time.time(),
    }
    _save(d)
    allow = _setting("link_default_allowance", None)
    if allow:
        set_allowance(peer_id, int(allow))
    return public(d["peers"][peer_id])


def dedupe_peers() -> int:
    """Merge link records that name the same station (same real 32-byte station pk). Returns how many were merged away."""
    d = _data()
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for p in d["peers"].values():
        pk = str((p.get("station") or {}).get("pk") or "")
        if _b64_len(pk) == 32:
            groups.setdefault(pk, []).append(p)
    merged = 0
    for recs in groups.values():
        if len(recs) < 2:
            continue
        recs.sort(key=lambda r: (0 if r.get("alias") else 1, float(r.get("created_at") or 0)))
        keep = recs[0]
        for o in recs[1:]:
            alts = keep.setdefault("alt_device_ids", [])
            for dv in [o.get("device_id")] + list(o.get("alt_device_ids") or []):
                if dv and dv != keep.get("device_id") and dv not in alts:
                    alts.append(dv)
            ids = keep.setdefault("merged_ids", [])
            for pid in [o["peer_id"]] + list(o.get("merged_ids") or []):
                if pid not in ids:
                    ids.append(pid)
            if not keep.get("alias") and o.get("alias"):
                keep["alias"] = o["alias"]
            if not keep.get("token") and o.get("token"):
                keep["token"] = o["token"]
            if d.get("controller") == o["peer_id"]:
                d["controller"] = keep["peer_id"]
            del d["peers"][o["peer_id"]]
            merged += 1
    if merged:
        _save(d)
    return merged


def list_peers() -> List[Dict[str, Any]]:
    d = _data()
    return [dict(public(p), controller=(d.get("controller") == p["peer_id"]),
                 warnings=warnings(p.get("grants") or {}, slug(p), d.get("controller"), d.get("controller") == p["peer_id"]))
            for p in d["peers"].values()]


def get(peer_id: str) -> Optional[Dict[str, Any]]:
    peers_map = _data()["peers"]
    p = peers_map.get(peer_id)
    if p:
        return p
    for rec in peers_map.values():
        if peer_id in (rec.get("merged_ids") or []):
            return rec
    return None


def by_device(device_id: str) -> Optional[Dict[str, Any]]:
    for p in _data()["peers"].values():
        if device_id and p.get("device_id") == device_id:
            return p
        if device_id and device_id in (p.get("alt_device_ids") or []):
            return p
    return None


def set_grants(peer_id: str, grants: Any) -> Dict[str, Any]:
    d = _data()
    p = d["peers"].get(peer_id)
    if not p:
        return {"error": "unknown peer"}
    p["grants"] = normalise_grants(grants, "observer") if not isinstance(grants, dict) else \
        normalise_grants({**p.get("grants", {}), **grants}, "observer")
    _save(d)
    return public(p)


def train_role() -> str:
    """'engine' (this station couples) or 'coach' (it only reaches the world through its engine)."""
    role = str(_data().get("train_role") or "engine").strip().lower()
    return role if role in TRAIN_ROLES else "engine"


def set_train_role(role: Any) -> Dict[str, Any]:
    role = str(role or "").strip().lower()
    if role not in TRAIN_ROLES:
        return {"error": "train_role must be one of " + ", ".join(TRAIN_ROLES)}
    d = _data()
    d["train_role"] = role
    _save(d)
    return {"ok": True, "train_role": role}


def set_relation(peer_id: str, relation: Any, engine: bool = False) -> Dict[str, Any]:
    """Mark a linked station as one of my own (train coach) or a partner's (other train)."""
    rel = str(relation or "").strip().lower()
    if rel not in RELATIONS:
        return {"error": "relation must be one of " + ", ".join(RELATIONS)}
    d = _data()
    p = d["peers"].get(str(peer_id or ""))
    if not p:
        return {"error": "unknown peer"}
    p["relation"] = rel
    if rel == "own" and train_role() == "coach":
        # A coach has exactly one engine: the own station it rides under.
        for other in d["peers"].values():
            other.pop("is_engine", None)
        p["is_engine"] = bool(engine) if engine else True
    else:
        p.pop("is_engine", None)
    _save(d)
    return {"ok": True, "peer": public(p)}


def my_train() -> Dict[str, Any]:
    """This station's train: role, the engine it rides under, its coaches, and partner links."""
    d = _data()
    role = train_role()
    own = [p for p in d["peers"].values() if str(p.get("relation") or "partner") == "own"]
    partners = [p for p in d["peers"].values() if str(p.get("relation") or "partner") != "own"]
    engine: Optional[str] = None
    if role == "coach":
        engine = next((p["peer_id"] for p in own if p.get("is_engine")), own[0]["peer_id"] if own else None)
    shared = {str(s.get("peer_id") or "") for s in (d.get("shared_coaches") or []) if isinstance(s, dict)}
    stations = []
    for p in d["peers"].values():
        stations.append({"peer_id": p["peer_id"], "station": slug(p), "name": p.get("name") or "",
                         "relation": str(p.get("relation") or "partner"),
                         "train_role": str(p.get("train_role") or "engine"),
                         "shared": p["peer_id"] in shared})
    return {
        "role": role,
        "engine": engine,
        "coaches": [p["peer_id"] for p in own] if role == "engine" else [],
        "partners": [p["peer_id"] for p in partners],
        "shared": [p["peer_id"] for p in own if p["peer_id"] in shared],
        "stations": stations,
    }


def shared_coaches() -> List[Dict[str, Any]]:
    """The owner's coaches that ride along when this engine couples, with their session grants."""
    d = _data()
    out: List[Dict[str, Any]] = []
    for item in d.get("shared_coaches") or []:
        if not isinstance(item, dict):
            continue
        p = d["peers"].get(str(item.get("peer_id") or ""))
        if not p or str(p.get("relation") or "partner") != "own":
            continue
        out.append({"peer_id": p["peer_id"], "station": slug(p),
                    "grants": normalise_grants(item.get("grants"), "observer")})
    return out


def set_shared_coaches(items: Any) -> Dict[str, Any]:
    """Choose which of my coaches ride along when I couple, and with which grants."""
    if not isinstance(items, list):
        return {"error": "shared_coaches must be a list of {peer_id, grants}"}
    d = _data()
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        pid = str((item or {}).get("peer_id") or "") if isinstance(item, dict) else ""
        p = d["peers"].get(pid)
        if not p:
            return {"error": "unknown peer " + (pid or "?")}
        if str(p.get("relation") or "partner") != "own":
            return {"error": "only your own stations can ride along as coaches"}
        if pid in seen:
            continue
        seen.add(pid)
        grants = normalise_grants((item or {}).get("grants"), "observer")
        out.append({"peer_id": pid, "grants": grants})
    d["shared_coaches"] = out
    _save(d)
    return {"ok": True, "shared_coaches": shared_coaches()}


def shared_coach_members() -> List[Dict[str, Any]]:
    """Coach member cards an engine hands to the host when it couples (identity only, no tokens)."""
    d = _data()
    out: List[Dict[str, Any]] = []
    for item in shared_coaches():
        p = d["peers"].get(item["peer_id"]) or {}
        station = p.get("station") or {}
        out.append({
            "device_id": str(p.get("device_id") or ""),
            "name": str(station.get("name") or p.get("name") or item["station"])[:60],
            "person": str(p.get("person") or p.get("name") or item["station"])[:60],
            "device_kind": "station",
            "role": "member",
            "train_role": "coach",
            "station_card": {"station": item["station"], "name": str(station.get("name") or item["station"])[:60]},
            "grants": item["grants"],
        })
    return out


def set_controller(peer_id: Optional[str]) -> Dict[str, Any]:
    """Name one linked station as the only remote controller, or None for no controller."""
    d = _data()
    if peer_id and peer_id not in d["peers"]:
        return {"error": "unknown peer"}
    d["controller"] = peer_id or None
    _save(d)
    return {"ok": True, "controller": d["controller"]}


def remove(peer_id: str) -> Dict[str, Any]:
    d = _data()
    p = d["peers"].pop(peer_id, None)
    if not p:
        return {"error": "unknown peer"}
    if d.get("controller") == peer_id:
        d["controller"] = None
    d["shared_coaches"] = [s for s in (d.get("shared_coaches") or [])
                           if isinstance(s, dict) and str(s.get("peer_id") or "") != peer_id]
    for k in [k for k in _COACH_CLAIMS if k[0] == peer_id]:
        _COACH_CLAIMS.pop(k, None)
    archived = 0
    for stub in (d.get("remote_workers") or {}).values():
        if isinstance(stub, dict) and stub.get("peer_id") == peer_id and not stub.get("archived"):
            stub["archived"] = True
            stub["archive_reason"] = "station unlinked"
            archived += 1
    _save(d)
    if p.get("device_id"):
        pairing.revoke(p["device_id"])
    released = 0
    try:
        from cli_tools import locks as _locks
        released = _locks.release_claims_for_peer(peer_id)
    except Exception:
        released = 0
    return {"ok": True, "peer_id": peer_id, "released_claims": released, "archived_workers": archived}


def effective_grants(peer: Dict[str, Any]) -> Dict[str, str]:
    g = normalise_grants(peer.get("grants"), "observer")
    ctl = _data().get("controller")
    if ctl and ctl != peer.get("peer_id"):
        for k in CONTROL_GRANTS:
            g[k] = "off"
    return g


def allows(device_id: str, path: str, method: str) -> Tuple[bool, str]:
    """Whether the linked station behind this device token may use this route."""
    peer = by_device(device_id)
    if not peer:
        return False, "this link was removed"
    route = str(path or "").split("?")[0]
    m = method.upper()
    if (m if m != "HEAD" else "GET", route) in ALWAYS:
        return True, ""
    table = _GET_ROUTES if m in ("GET", "HEAD") else _POST_ROUTES if m == "POST" else {}
    rule = table.get(route)
    if not rule:
        return False, "linked stations cannot use this route"
    grant, ok_values = rule
    if effective_grants(peer).get(grant) in ok_values:
        return True, ""
    return False, f"this link is not granted {grant}"


def my_station() -> dict | None:
    """This station's tunnel identity from <subway_dir>/tunnel/station.json, or None."""
    p = os.path.join(subway_home.subway_dir(), "tunnel", "station.json")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict):
        return None
    sid = str(info.get("sid") or "")
    pk = str(info.get("x25519_pub") or info.get("pk") or "")
    relay = str(info.get("url") or info.get("relay") or "")
    if not sid or not pk or not relay:
        return None
    return {"sid": sid, "pk": pk, "relay": relay, "name": socket.gethostname()}


_SID_RE = re.compile(r"^[a-z2-7]{26}$")


def _b64_len(s: str) -> int:
    if not isinstance(s, str):
        return -1
    if not re.match(r"^[A-Za-z0-9+/]+={0,2}$", s) or len(s) % 4 != 0:
        return -1
    pad = 2 if s.endswith("==") else 1 if s.endswith("=") else 0
    return (len(s) // 4) * 3 - pad


def parse_link(link: str) -> dict | None:
    """Python port of parseTunnelLink in mobile/src/pairing/tunnelLink.ts (same checks)."""
    if not isinstance(link, str) or not link.startswith("subway://pair?"):
        return None
    q = urllib.parse.parse_qs(link[len("subway://pair?"):], keep_blank_values=True)
    def one(key: str, default: str = "") -> str:
        v = q.get(key)
        return str(v[0]) if v else default
    sid = one("sid")
    pk = one("pk")
    relay = one("relay")
    code = q.get("code")
    code_val = str(code[0]) if code else None
    if not _SID_RE.match(sid) or _b64_len(pk) != 32 or not relay.startswith("wss://"):
        return None
    out = {"sid": sid, "pk": pk, "relay": relay}
    if code_val is not None:
        out["code"] = code_val
    return out


def _node_call(station: dict, token: str, method: str, path: str, body=None, timeout: int = 30) -> tuple[int, dict]:
    """One request over the tunnel, retried once when the relay socket drops before an answer."""
    status, data = _node_call_once(station, token, method, path, body, timeout)
    if status == 0 and "socket" in str((data or {}).get("error", "")):
        time.sleep(1)
        status, data = _node_call_once(station, token, method, path, body, timeout)
    return status, data


def _node_call_once(station: dict, token: str, method: str, path: str, body=None, timeout: int = 30) -> tuple[int, dict]:
    """One request over the tunnel to another station. (0, {"error":...}) when it can't be reached."""
    node = shutil.which("node")
    if not node:
        return 0, {"error": "node not found"}
    env = dict(os.environ)
    cmd = [node, SCRIPT, "call",
           "--sid", str(station.get("sid") or ""),
           "--pk", str(station.get("pk") or ""),
           "--relay", str(station.get("relay") or ""),
           "--method", str(method or "GET").upper(),
           "--path", str(path or "/"),
           "--timeout-ms", str(max(1000, timeout * 1000 - 500))]
    body_path = None
    if body is not None:
        fd, body_path = tempfile.mkstemp(prefix=".subway-peer-", suffix=".json")
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                if isinstance(body, str):
                    fh.write(body)
                else:
                    json.dump(body, fh)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            try:
                os.unlink(body_path)
            except Exception:
                pass
            return 0, {"error": "failed to write request body"}
        cmd.extend(["--body-file", body_path])
    if token:
        env["SUBWAY_PEER_TOKEN"] = str(token)
        cmd.extend(["--token-env", "SUBWAY_PEER_TOKEN"])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
        out_text = (proc.stdout or "").strip()
        if not out_text:
            # fall back to stderr for diagnostics
            err = (proc.stderr or "").strip()
            return 0, {"error": err or "no response"}
        try:
            out = json.loads(out_text.splitlines()[-1])
        except Exception:
            return 0, {"error": out_text[:300]}
        if isinstance(out, dict) and "error" in out and "status" not in out:
            return 0, {"error": str(out.get("error") or "error")}
        status = int(out.get("status") or 0) if isinstance(out, dict) else 0
        body_text = str(out.get("body") or "") if isinstance(out, dict) else ""
        try:
            data = json.loads(body_text) if body_text != "" else {}
        except Exception:
            data = {"text": body_text}
        if not isinstance(data, dict):
            data = {"data": data}
        return status, data
    except subprocess.TimeoutExpired:
        return 0, {"error": "timeout"}
    except Exception as e:
        return 0, {"error": str(e)}
    finally:
        if body_path:
            try:
                os.unlink(body_path)
            except Exception:
                pass


def call(peer_id: str, method: str, path: str, body=None, timeout: int = 30) -> dict:
    """{"status": n, "data": {...}} or {"error": "offline: ..."}; updates last_seen on success."""
    p = get(peer_id)
    if not p:
        return {"error": "unknown peer"}
    station = p.get("station") or {}
    status, res = _node_call(station, str(p.get("token") or ""), method, path, body, timeout=timeout)
    if status == 0 and isinstance(res, dict) and "error" in res:
        return {"error": "offline: " + str(res.get("error") or "unreachable")}
    # update last_seen on success (any HTTP response received)
    try:
        d = _data()
        peer = d["peers"].get(peer_id)
        if peer:
            peer["last_seen"] = time.time()
            _save(d)
    except Exception:
        pass
    return {"status": status, "data": res}


def link_add(link: str, grants="personal") -> dict:
    l = parse_link(link)
    if not l:
        return {"error": "not a Subway pairing link"}
    if not l.get("code"):
        return {"error": "this link has no pairing code"}
    mine = my_station()
    if not mine:
        return {"error": "set up Subway Tunnel on this station first"}
    m = pairing.mint_device("Linked station", "station", pairing.SCOPE_PEER)
    st = {"sid": l["sid"], "pk": l["pk"], "relay": l["relay"]}
    status, res = _node_call(st, "", "POST", "/api/pair/request",
                             {"kind": "station", "qr_code": l["code"], "station": mine, "peer_token": m["token"]})
    if status != 200 or not isinstance(res, dict) or not res.get("request_id"):
        pairing.revoke(m["device_id"])
        err = res.get("error") if isinstance(res, dict) else None
        return {"error": err or f"link failed ({status})"}
    req_id = str(res.get("request_id"))
    approved_token = None
    final_status = None
    deadline = time.time() + 30
    while time.time() < deadline:
        s_status, s_res = _node_call(st, "", "GET", "/api/pair/status?request_id=" + urllib.parse.quote(req_id, safe=""))
        if s_status == 200 and isinstance(s_res, dict):
            final_status = str(s_res.get("status") or "")
            if final_status == "approved" and s_res.get("token"):
                approved_token = str(s_res.get("token"))
                break
            if final_status in ("denied", "expired", "unknown"):
                pairing.revoke(m["device_id"])
                return {"error": final_status}
        time.sleep(1)
    if not approved_token:
        pairing.revoke(m["device_id"])
        return {"error": final_status or "link failed (timeout)"}
    h_status, hello = _node_call(st, approved_token, "GET", "/api/peer/hello")
    name = "Station"
    if h_status == 200 and isinstance(hello, dict) and hello.get("station"):
        name = str(hello.get("station") or "Station")
    st_named = {**st, "name": name}
    peer = add(name, st_named, approved_token, m["device_id"], grants)
    try:
        pairing.rename(m["device_id"], name)
    except Exception:
        pass
    return {"ok": True, "peer": peer}


def unlink(peer_id: str) -> dict:
    """Tell the other side (best effort), then drop our half and revoke its token."""
    call(peer_id, "POST", "/api/peer/unlink", {})
    return remove(peer_id)


def _grants_summary(peer: dict) -> str:
    g = peer.get("grants") or {}
    if isinstance(g, str):
        return g
    # compact summary
    return ",".join(f"{k}={v}" for k, v in sorted(g.items()) if v not in ("off", "none")) or "none"


def link_cli(argv: list[str]) -> int:
    import sys
    sub = argv[0] if argv else ""
    rest = argv[1:]
    if sub == "roots":
        if not rest:
            print("usage: subway link roots <station> [folder ...]  (no folders = this station's projects)")
            return 1
        p = by_slug(rest[0])
        if not p:
            print(f"no linked station called {rest[0]}")
            return 1
        res = set_roots(p["peer_id"], rest[1:])
        print("Shares: " + (", ".join(res.get("roots") or []) or "this station's project folders"))
        return 0
    if sub == "files":
        print(station_files(rest[0], rest[1] if len(rest) > 1 else "") if rest else "usage: subway link files <station> [path]")
        return 0
    if sub == "fetch":
        print(fetch_file(rest[0], rest[1], rest[2] if len(rest) > 2 else "") if len(rest) >= 2 else "usage: subway link fetch <station> <path> [to]")
        return 0
    if sub == "send":
        args = [a for a in rest if a != "--overwrite"]
        print(send_file(args[0], args[1], args[2], "--overwrite" in rest) if len(args) >= 3 else "usage: subway link send <station> <local> <remote> [--overwrite]")
        return 0
    if sub == "land":
        if not rest:
            print("usage: subway link land worker_x@station [repo_dir]")
            return 1
        out = land_remote(rest[0], rest[1:] + [os.getcwd()])
        print(out)
        return 1 if out.startswith("Error") else 0
    if sub == "models":
        if not rest:
            print("usage: subway link models <station> [provider]")
            return 1
        r = remote_models(rest[0], fresh=True)
        if r.get("error"):
            print(r["error"])
            return 1
        print(f"Models on {r['station']}:\n" + (models_text(rest[0], rest[1] if len(rest) > 1 else "") or "  none"))
        return 0
    if sub == "rename":
        if len(rest) < 2:
            print("usage: subway link rename <peer_id> <alias>")
            return 1
        res = set_alias(rest[0], rest[1])
        if "error" in res:
            print(str(res["error"]))
            return 1
        print(f"Renamed to {slug(res)}")
        return 0
    if sub == "add":
        link = ""
        preset = "personal"
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == "--preset" and i + 1 < len(rest):
                preset = rest[i + 1]
                i += 2
            elif a.startswith("--preset="):
                preset = a.split("=", 1)[1]
                i += 1
            elif not a.startswith("-") and not link:
                link = a
                i += 1
            else:
                i += 1
        res = link_add(link, preset)
        if "error" in res:
            print(str(res["error"]))
            return 1
        p = res.get("peer") or {}
        print(f"Linked {p.get('name','Station')} ({p.get('peer_id','')})")
        return 0
    if sub == "list":
        d = _data()
        ctl = d.get("controller")
        for p in list_peers():
            mark = " [controller]" if p.get("peer_id") == ctl or p.get("controller") else ""
            print(f"{p.get('peer_id','')}  {p.get('name','')}  {_grants_summary(p)}{mark}")
            for w in p.get("warnings") or []:
                print(f"  ! {w}")
        return 0
    if sub == "remove":
        pid = rest[0] if rest else ""
        res = unlink(pid)
        if "error" in res:
            print(str(res["error"]))
            return 1
        print(f"Removed {pid}")
        return 0
    if sub == "call":
        pid = rest[0] if rest else ""
        cpath = rest[1] if len(rest) > 1 else "/api/status"
        res = call(pid, "GET", cpath)
        if "error" in res:
            print(str(res["error"]))
            return 1
        data = res.get("data")
        text = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
        print(f"{res.get('status')} {text[:300]}")
        return 0
    print("usage: subway link add|list|remove|call ...")
    return 1

# ---- P3: one roster (chats on linked stations, named @alias@station) ----
import re as _re

_ROSTER_CACHE: Dict[str, Tuple[float, Any]] = {}
_ROSTER_TTL = 10.0


def slug(peer: Dict[str, Any]) -> str:
    """Short station name used in @alias@station."""
    raw = str(peer.get("alias") or peer.get("name") or peer.get("peer_id") or "station").lower()
    if raw.endswith(".local"):
        raw = raw[: -len(".local")]
    return _re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:24] or "station"


def by_slug(name: str) -> Optional[Dict[str, Any]]:
    n = str(name or "").strip().lower().lstrip("@")
    for p in _data()["peers"].values():
        if n and (slug(p) == n or p.get("peer_id", "").lower() == n):
            return p
    return None


def set_alias(peer_id: str, alias: str) -> Dict[str, Any]:
    d = _data()
    p = d["peers"].get(peer_id)
    if not p:
        return {"error": "unknown peer"}
    clean = slug({"alias": alias})
    for other in d["peers"].values():
        if other.get("peer_id") != peer_id and slug(other) == clean:
            return {"error": f"another linked station is already called {clean}"}
    p["alias"] = clean
    _save(d)
    return public(p)


def split_remote(chat_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """'@claude-main@studio' -> ('@claude-main', peer) when 'studio' is a linked station; else None."""
    s = str(chat_id or "").strip()
    head, sep, station = s.rpartition("@")
    if not sep or not head or head == "@":
        return None
    peer = by_slug(station)
    return (head, peer) if peer else None


def remote_get(peer: Dict[str, Any], path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """GET a peer route with query params, cached 10 s. Returns call()'s dict."""
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    full = path + ("?" + q if q else "")
    key = peer["peer_id"] + " " + full
    hit = _ROSTER_CACHE.get(key)
    if hit and time.time() - hit[0] < _ROSTER_TTL:
        return hit[1]
    res = call(peer["peer_id"], "GET", full)
    _ROSTER_CACHE[key] = (time.time(), res)
    return res


def remote_chats(query: str = "", limit: int = 15) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for p in _data()["peers"].values():
        st = slug(p)
        res = remote_get(p, "/api/peer/chats", {"query": query, "limit": int(limit)})
        chats = (res.get("data") or {}).get("chats") if res.get("status") == 200 else None
        if not isinstance(chats, list):
            rows.append({"station": st, "peer_id": p["peer_id"], "offline": True,
                         "denied": res.get("status") == 403})
            continue
        for c in chats[: int(limit)]:
            rows.append({"station": st, "peer_id": p["peer_id"], "id": str(c.get("id") or ""),
                         "alias": str(c.get("alias") or ""), "title": str(c.get("title") or ""),
                         "source": str(c.get("source") or ""), "updated": c.get("updated"), "offline": False})
    return rows

def send_mail(chat_ref: str, text: str, sender: str, mode: str = "") -> str:
    """Send a note to '@alias@station'. Returns a line for the caller."""
    r = split_remote(chat_ref)
    if not r:
        return f"Error: {chat_ref} is not a chat on a linked station."
    head, peer = r
    st = slug(peer)
    res = call(peer["peer_id"], "POST", "/api/peer/mail", {"to": head, "text": str(text)[:8000], "from": str(sender or "")[:120], "mode": mode or ""})
    if res.get("status") == 200:
        return f"Sent to {head} on {st}: " + str((res.get("data") or {}).get("result") or "ok")
    if res.get("status") == 403:
        return f"{st} has not granted mail to this station."
    if res.get("status"):
        return f"{st} refused the note: " + str((res.get("data") or {}).get("error") or res.get("status"))
    return f"{st} is offline; the note was not sent."


# ---- P5: workers on linked stations ----
_WORKER_RE = _re.compile(r"worker_[0-9a-f]{6,}")


def split_worker(ref: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """'worker_ab12cd@studio' -> ('worker_ab12cd', peer) when studio is a linked station."""
    head, sep, station = str(ref or "").strip().rpartition("@")
    if not sep or not _WORKER_RE.fullmatch(head):
        return None
    peer = by_slug(station)
    return (head, peer) if peer else None


def _remember_remote(worker_id: str, peer: Dict[str, Any], role: str, launched_by: str) -> None:
    d = _data()
    rw = d.setdefault("remote_workers", {})
    rw[worker_id] = {"worker_id": worker_id, "peer_id": peer["peer_id"], "station": slug(peer),
                     "role": str(role)[:80], "launched_by": str(launched_by or "")[:120], "created_at": time.time()}
    if len(rw) > 500:
        for k in sorted(rw, key=lambda k: rw[k].get("created_at", 0))[:-500]:
            rw.pop(k, None)
    _save(d)


REMOTE_FINAL = {"completed", "failed", "stopped", "rejected", "budget_stopped", "timeout", "error", "cancelled"}
REMOTE_ARCHIVE_AFTER = 7 * 86400


def _stub_view(r: Dict[str, Any], now: float) -> Dict[str, Any]:
    v = dict(r)
    v["label"] = "on @" + str(r.get("station") or "?")
    if not v.get("archived") and r.get("status") in REMOTE_FINAL:
        if now - float(r.get("checked_at") or r.get("created_at") or 0) > REMOTE_ARCHIVE_AFTER:
            v["archived"] = True
            v["archive_reason"] = "finished over 7 days ago"
    v["archived"] = bool(v.get("archived"))
    v.update({k: r.get(k) for k in ("swarm", "state", "files", "requested_by", "paid_by", "tokens") if k in r})
    return v


def queue_remote_workers() -> List[Dict[str, Any]]:
    """Remote workers for the HUD queue: hide finished ones older than 10 minutes (kept in history)."""
    now = time.time()
    return [v for v in remote_workers(archived=False)
            if not (v.get("status") in REMOTE_FINAL
                    and now - float(v.get("checked_at") or v.get("created_at") or 0) > 600)]


def remote_workers(archived: bool = False) -> List[Dict[str, Any]]:
    """Stubs for workers launched on linked stations. Default: live ones; archived=True: the archive only."""
    now = time.time()
    views = [_stub_view(r, now) for r in _data().get("remote_workers", {}).values()]
    return sorted([v for v in views if v["archived"] == bool(archived)], key=lambda r: -float(r.get("created_at", 0)))


SPAWN_FIELDS = ("role", "prompt", "provider", "model", "effort", "track", "max_tokens", "max_seconds",
                "max_tool_calls", "timeout_seconds", "code", "project", "isolate", "capabilities")


def spawn_remote(station: str, fields: Dict[str, Any], launched_by: str = "") -> str:
    peer = by_slug(station)
    if not peer:
        return f"Error: no linked station called {station}."
    st = slug(peer)
    body = {k: fields[k] for k in SPAWN_FIELDS if fields.get(k) not in (None, "", [], 0)}
    body["origin_chat"] = str(launched_by or "")[:120]
    try:
        from hud_server import collab_api as _c
        body["collab_device"] = _c._device_id()
    except Exception:
        pass
    want = str(body.get("model") or "").strip()
    if want:
        known = remote_models(slug(peer))
        if not known.get("error"):
            prov = str(body.get("provider") or "").strip()
            match = [m for m in known["models"] if str(m.get("model")) == want and (not prov or prov in ("auto", str(m.get("provider"))))]
            if not match:
                return (f"Error: {slug(peer)} has no model {want}" + (f" for {prov}" if prov and prov != "auto" else "")
                        + ".\n" + models_text(slug(peer), "" if prov in ("", "auto") else prov))
            if all(m.get("exhausted") for m in match):
                return f"Error: {want} on {slug(peer)} is out of quota right now.\n" + models_text(slug(peer))
    res = call(peer["peer_id"], "POST", "/api/peer/spawn", body)
    if res.get("status") == 403:
        return f"{st} has not granted starting workers to this station."
    if res.get("status") != 200:
        err = (res.get("data") or {}).get("error") if res.get("status") else res.get("error")
        hint = models_text(st, str(body.get("provider") or "")) if body.get("model") or body.get("provider") else ""
        return f"Error: {st} did not start the worker: {err or res.get('status')}" + (f"\nModels on {st}:\n{hint}" if hint else "")
    data = res.get("data") or {}
    wid = str(data.get("worker_id") or "")
    if not wid:
        return f"Error: {st} answered without a worker id: {str(data.get('result') or '')[:200]}"
    _remember_remote(wid, peer, body.get("role", ""), launched_by)
    if data.get("status") == "pending_approval":
        return (f"[QUEUED ON {st.upper()}] Worker `{wid}@{st}` waits for your user's approval on {st} "
                f"(its HUD or phone), or here with approve_worker('{wid}@{st}', <code shown on {st}>).")
    return f"[LAUNCHED ON {st.upper()}] Worker `{wid}@{st}` is running on {st}. Check it with worker_status('{wid}@{st}')."


def remote_worker_status(ref: str) -> str:
    r = split_worker(ref)
    if not r:
        return f"Error: {ref} is not a worker on a linked station."
    wid, peer = r
    st = slug(peer)
    res = call(peer["peer_id"], "GET", "/api/peer/worker?id=" + urllib.parse.quote(wid))
    if res.get("status") == 200:
        w = res.get("data") or {}
        lines = [f"Worker `{wid}@{st}` on {st}: {w.get('status')}",
                 f"role: {w.get('role') or ''} · {w.get('provider') or ''} {w.get('model') or ''} · tokens: {w.get('spent') or 0}"]
        if w.get("report"):
            lines.append("report:\n" + str(w["report"]))
        return "\n".join(lines)
    if res.get("status") == 403:
        return f"{st} does not share this worker with this station."
    if res.get("status") == 404:
        return f"{st} has no worker {wid}."
    return stub_status_text(ref) or f"{st} is offline."


def remote_approve(ref: str, approval_code: str = "", by_user: bool = False) -> str:
    """by_user=True only from this station's HUD (the user's own token); agents must pass the code shown on that station."""
    r = split_worker(ref)
    if not r:
        return f"Error: {ref} is not a worker on a linked station."
    wid, peer = r
    st = slug(peer)
    if by_user:
        res = call(peer["peer_id"], "POST", "/api/approve_worker", {"id": wid})
    else:
        res = call(peer["peer_id"], "POST", "/api/peer/approve_worker", {"id": wid, "approval_code": str(approval_code or "")})
    if res.get("status") == 200:
        return f"Approved `{wid}` on {st}: " + str((res.get("data") or {}).get("result") or "launching")
    if res.get("status") == 403:
        return f"{st} has not granted approvals to this station."
    if res.get("status"):
        return f"{st} refused: " + str((res.get("data") or {}).get("error") or res.get("status"))
    return f"{st} is offline."


def remote_reject(ref: str, reason: str = "") -> str:
    r = split_worker(ref)
    if not r:
        return f"Error: {ref} is not a worker on a linked station."
    wid, peer = r
    st = slug(peer)
    res = call(peer["peer_id"], "POST", "/api/reject_worker", {"id": wid, "reason": str(reason or "")})
    if res.get("status") == 200:
        return f"Rejected `{wid}` on {st}."
    if res.get("status") == 403:
        return f"{st} has not granted approvals to this station."
    return f"{st} is offline." if not res.get("status") else f"{st} refused: {res.get('status')}"


def resolve_project(project: str, known_dirs: List[str], default: str) -> Optional[str]:
    """Pick a project folder on THIS station for a remote worker: an existing absolute path, else a known
    project whose folder name matches, else the default. None if nothing fits."""
    p = str(project or "").strip()
    if p and os.path.isabs(p) and os.path.isdir(p):
        return p
    if p:
        name = os.path.basename(p.rstrip("/")).lower()
        for k in known_dirs:
            if k and os.path.isdir(k) and os.path.basename(k.rstrip("/")).lower() == name:
                return k
        return None
    return default if default and os.path.isdir(default) else None

def stub_status_text(ref: str) -> str:
    """worker_status for a remote worker when its station can't be reached: the last known state."""
    r = split_worker(ref)
    stub = _data().get("remote_workers", {}).get(r[0]) if r else None
    if not stub:
        return ""
    lines = [f"Worker `{stub['worker_id']}@{stub['station']}` (last known, station offline): {stub.get('status') or 'unknown'}"]
    if stub.get("report"):
        lines.append("report:\n" + str(stub["report"]))
    return "\n".join(lines)

GRANT_LABELS = {
    "chats": "Read chats", "mail": "Send mail", "spawn": "Start workers", "approve": "Approve workers and permissions",
    "land": "Change files", "claims": "File claims", "events": "See workers", "files": "Files", "apps": "Apps", "swarm": "Run its agents on my checkout", "skills": "Borrow my skills", "screen": "See my screen", "mcp": "Use my MCP tools",
    "control": "Control my screen",
}
CHOICE_LABELS = {
    "none": "No", "titles": "Titles only", "decisions": "Decisions", "full": "Everything",
    "off": "No", "on": "Yes", "pending": "After approval", "direct": "Without approval",
    "patch": "As a patch", "edits": "Direct edits", "branch": "On a branch",
    "read": "See them", "claim": "See and claim", "own": "Its own", "all": "All",
    "snapshot": "Still image on request", "live": "Live view I approve",
}


def grant_schema() -> List[Dict[str, Any]]:
    """Every grant with its label and labelled choices, for the HUD permissions panel."""
    out = []
    for key, choices in GRANT_CHOICES.items():
        if key not in GRANT_LABELS:
            continue
        out.append({"key": key, "label": GRANT_LABELS[key],
                    "choices": [{"value": c, "label": CHOICE_LABELS.get(c, c)} for c in choices]})
    return out


def warnings(grants: Dict[str, str], name: str = "This station", controller: Optional[str] = None,
             is_controller: bool = False) -> List[str]:
    """Plain warnings for risky grant combinations, shown next to the link's settings (HUD and CLI)."""
    g = normalise_grants(grants, "observer")
    out: List[str] = []
    if controller and not is_controller:
        out.append(f"Another station is the only controller, so {name} cannot start, approve or land work here.")
        return out
    if g["spawn"] == "direct":
        out.append(f"Agents on {name} can launch workers here without your approval "
                   "(unless Settings → Workers asks before launching workers from linked stations).")
    if g["spawn"] == "pending" and g["approve"] == "off":
        out.append(f"Workers from {name} wait for approval, but {name} cannot approve them: approve them on this station.")
    if g["approve"] == "on":
        out.append(f"{name} can approve workers and permission requests on this station.")
    if g["land"] in ("edits", "branch"):
        out.append(f"Workers from {name} can change files in this station's projects directly." if g["land"] == "edits"
                   else f"Workers from {name} can push branches in this station's projects.")
    if g["chats"] == "full":
        out.append(f"{name} can read every chat on this station in full, including tool output.")
    if g.get("files") == "send":
        out.append(f"{name} can read and write files in the folders this link shares.")
    if g.get("apps") == "browser":
        out.append(f"Workers from {name} can drive a browser on this station, with the sessions you are signed in to.")
    if g.get("apps") == "desktop":
        out.append(f"Workers from {name} can see and control apps on this station.")
    return out

# ---- P6a: shared claims ----
import subprocess as _subprocess

_REPO_CACHE: Dict[str, Tuple[float, Optional[Tuple[str, str]]]] = {}
_OFFLINE_UNTIL: Dict[str, float] = {}


def repo_identity(path: str) -> Optional[Tuple[str, str]]:
    """(root commit, path relative to the repo top) for a file inside a git repo, else None. Cached 5 min per folder."""
    p = os.path.abspath(str(path or ""))
    folder = p if os.path.isdir(p) else os.path.dirname(p)
    hit = _REPO_CACHE.get(folder)
    if hit and time.time() - hit[0] < 300 and hit[1] is not None:
        top, root = hit[1]
    else:
        try:
            top = _subprocess.run(["git", "-C", folder, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5).stdout.strip()
            root = _subprocess.run(["git", "-C", folder, "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True, timeout=5).stdout.split()
            root = sorted(root)[0] if root else ""
        except (OSError, _subprocess.SubprocessError):
            top, root = "", ""
        _REPO_CACHE[folder] = (time.time(), (top, root) if top and root else None)
        if not (top and root):
            return None
    rel = os.path.relpath(os.path.realpath(p), os.path.realpath(top))
    if rel.startswith(".."):
        return None
    return root, rel.replace(os.sep, "/")


def remote_claim_conflict(path: str, holder: str) -> str:
    """'' when no linked station holds this file; else a [COLLISION WARNING] line naming the station."""
    peers_now = list(_data()["peers"].values())
    if not peers_now:
        return ""
    ident = repo_identity(path)
    if not ident:
        return ""
    root, rel = ident
    q = "/api/peer/claims?" + urllib.parse.urlencode({"root": root, "rel": rel})
    for p in peers_now:
        if _OFFLINE_UNTIL.get(p["peer_id"], 0) > time.time():
            continue
        res = call(p["peer_id"], "GET", q, timeout=6)
        if res.get("status") != 200:
            if not res.get("status"):
                _OFFLINE_UNTIL[p["peer_id"]] = time.time() + 60
            continue
        for c in (res.get("data") or {}).get("claims") or []:
            if str(c.get("holder") or "") != str(holder or ""):
                purp = f" for '{c.get('purpose')}'" if c.get("purpose") else ""
                st = slug(p)
                return (f"[COLLISION WARNING] '{rel}' is leased on linked station @{st} to {c.get('holder')}{purp}. "
                        f"Lease expires in {int(c.get('expires_in') or 0)}s. "
                        f"Do NOT overwrite. Ask or coordinate before editing: send_note(to='@{st}', text='Requesting handoff/edit for {rel} ...') "
                        f"or broadcast_track(track='main', message='Handoff request for {rel} to @{st}').")
    return ""


def local_claims_for(root: str, rel: str, locks: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Active claims on THIS station for a repo-relative path (answers a linked station)."""
    now = time.time()
    out = []
    for key, v in (locks or {}).items():
        if "::" in key or float(v.get("expires_at") or 0) <= now:
            continue
        ident = repo_identity(key)
        if ident and ident == (root, rel):
            out.append({"rel": rel, "holder": v.get("holder"), "purpose": v.get("purpose") or "",
                        "expires_in": int(float(v["expires_at"]) - now)})
    return out


_COACH_CLAIMS_TTL = 5.0
_COACH_CLAIMS: Dict[Tuple[str, str, str], Tuple[float, List[Dict[str, Any]]]] = {}


def coach_claims_for(root: str, rel: str, fresh: bool = False) -> List[Dict[str, Any]]:
    """Claims held by this engine's own coaches, fetched with a short timeout and cached 5 s.

    A coach never sees another coach's claims directly; it asks its engine, which answers for the
    whole train. Entries are tagged with the coach's station so the caller can name it.
    """
    now = time.time()
    out: List[Dict[str, Any]] = []
    mine = [p for p in _data()["peers"].values() if str(p.get("relation") or "partner") == "own"]
    shared = {str(s.get("peer_id") or "") for s in (_data().get("shared_coaches") or []) if isinstance(s, dict)}
    for p in mine:
        if shared and p["peer_id"] not in shared:
            continue
        key = (p["peer_id"], root, rel)
        hit = _COACH_CLAIMS.get(key)
        if hit and not fresh and now - hit[0] < _COACH_CLAIMS_TTL:
            out.extend(hit[1])
            continue
        q = "/api/peer/claims?" + urllib.parse.urlencode({"root": root, "rel": rel})
        res = call(p["peer_id"], "GET", q, timeout=3)
        found: List[Dict[str, Any]] = []
        if res.get("status") == 200:
            st = slug(p)
            for c in (res.get("data") or {}).get("claims") or []:
                if isinstance(c, dict) and str(c.get("station") or st) == st:
                    found.append(dict(c, station=st))
        _COACH_CLAIMS[key] = (now, found)
        out.extend(found)
    return out


def claims_with_train(root: str, rel: str, locks: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Everything this station can answer for a path: its own claims plus its coaches' claims."""
    return local_claims_for(root, rel, locks) + coach_claims_for(root, rel)


# ---- models on linked stations ----
_MODELS_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def remote_models(station: str, fresh: bool = False) -> Dict[str, Any]:
    """{"station", "models": [{provider, model, exhausted, vision}]} or {"station", "error"}. Cached 5 min."""
    peer = by_slug(station)
    if not peer:
        return {"station": station, "error": f"no linked station called {station}"}
    st = slug(peer)
    hit = _MODELS_CACHE.get(peer["peer_id"])
    if hit and not fresh and time.time() - hit[0] < 300:
        return {"station": st, "models": hit[1]}
    res = call(peer["peer_id"], "GET", "/api/peer/models", timeout=15)
    if res.get("status") == 403:
        return {"station": st, "error": f"{st} has not granted starting workers to this station"}
    if res.get("status") != 200:
        return {"station": st, "error": f"{st} is offline" if not res.get("status") else f"{st} answered {res.get('status')}"}
    models = [m for m in ((res.get("data") or {}).get("models") or []) if isinstance(m, dict) and m.get("provider")]
    _MODELS_CACHE[peer["peer_id"]] = (time.time(), models)
    return {"station": st, "models": models}


def models_text(station: str, provider: str = "") -> str:
    """One line per provider: 'opencode: a, b (exhausted: c)'. '' when unknown."""
    r = remote_models(station)
    if r.get("error"):
        return ""
    by: Dict[str, List[str]] = {}
    for m in r["models"]:
        p = str(m.get("provider"))
        if provider and p != provider:
            continue
        name = str(m.get("model") or "")
        by.setdefault(p, []).append(name + (" (exhausted)" if m.get("exhausted") else ""))
    return "\n".join(f"  {p}: {', '.join(v[:25])}" + (f" (+{len(v) - 25} more)" if len(v) > 25 else "") for p, v in sorted(by.items()))


def share_models(catalog: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """What a station tells a linked station about its models (no rankings, paths or benchmarks)."""
    return [{"provider": m.get("provider"), "model": m.get("model"), "exhausted": bool(m.get("exhausted")),
             "vision": bool(m.get("vision"))} for m in catalog or [] if m.get("provider") and m.get("model")]


# ---- P6b: patch landing ----
def _git_out(repo: str, *args: str, inp: Optional[str] = None) -> Tuple[int, str]:
    r = _subprocess.run(["git", "-C", repo, *args], input=inp, capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def fetch_patch(ref: str) -> Dict[str, Any]:
    """{"patch", "root", "role"} or {"error"} for worker_x@station."""
    r = split_worker(ref)
    if not r:
        return {"error": f"{ref} is not a worker on a linked station"}
    wid, peer = r
    st = slug(peer)
    res = call(peer["peer_id"], "GET", "/api/peer/patch?id=" + urllib.parse.quote(wid), timeout=30)
    if res.get("status") == 200 and (res.get("data") or {}).get("patch"):
        return res["data"]
    if res.get("status") == 404:
        return {"error": f"{st} has no patch for {wid} (it changed nothing, is still running, or landed there directly)"}
    if res.get("status") == 403:
        return {"error": f"{st} does not share this worker with this station"}
    return {"error": f"{st} is offline" if not res.get("status") else f"{st} answered {res.get('status')}"}


def land_remote(ref: str, candidates: List[str]) -> str:
    """Apply a linked station's worker patch to the local repo with the same root commit; commit only its files."""
    got = fetch_patch(ref)
    if got.get("error"):
        return "Error: " + got["error"]
    wid, peer = split_worker(ref)
    st = slug(peer)
    patch, root = str(got["patch"]), str(got.get("root") or "")
    repo = ""
    for c in candidates:
        if not c or not os.path.isdir(c):
            continue
        ident = repo_identity(c)
        if ident and (not root or ident[0] == root):
            code, top = _git_out(c, "rev-parse", "--show-toplevel")
            repo = top.strip() if code == 0 else ""
            if repo:
                break
    if not repo:
        return f"Error: no repository here has the same root commit as {st}'s ({root[:10] or 'unknown'})."
    code, files_out = _git_out(repo, "apply", "--numstat", "-", inp=patch)
    files = [ln.split("\t", 2)[2] for ln in files_out.splitlines() if ln.count("\t") >= 2]
    if code != 0 or not files:
        return f"Error: the patch from {st} could not be read: {files_out.strip()[:200]}"
    code, dirty = _git_out(repo, "status", "--porcelain", "--", *files)
    if dirty.strip():
        return f"Error: these files have uncommitted changes here, land after committing them: {', '.join(files[:8])}"
    # Claims are re-checked at land time (roadmap 38, Mode C): a file claimed here since the patch was sent is refused.
    try:
        from cli_tools.locks import load_locks as _load_locks
        claimed = swarm_conflicts(repo, _load_locks() or {}, files)
    except Exception:
        claimed = []
    if claimed:
        return f"Error: these files are claimed here, land after the claim is released: {', '.join(claimed[:8])}"
    code, out = _git_out(repo, "apply", "--check", "--whitespace=nowarn", "-", inp=patch)
    if code != 0:
        keep = os.path.join(subway_home.subway_dir(), "workers", f"{wid}@{st}.patch")
        os.makedirs(os.path.dirname(keep), exist_ok=True)
        with open(keep, "w", encoding="utf-8") as fh:
            fh.write(patch)
        return f"Error: the patch from {st} clashes with this tree, kept at {keep}: {out.strip()[:200]}"
    _git_out(repo, "apply", "--whitespace=nowarn", "-", inp=patch)
    _git_out(repo, "add", "--", *files)
    role = str(got.get("role") or "worker")
    code, out = _git_out(repo, "commit", "-m", f"Land {wid}@{st}: {role}", "--", *files)
    if code != 0:
        return f"Error: applied but not committed: {out.strip()[:200]}"
    _code, head = _git_out(repo, "rev-parse", "--short", "HEAD")
    d = _data()
    stub = d.setdefault("remote_workers", {}).get(wid)
    if stub:
        stub["landed"] = head.strip()
        if stub.get("swarm"):
            stub["state"] = "landed"
        _save(d)
    return f"Landed {wid}@{st} in {repo} as {head.strip()} ({len(files)} files)."

# ---- P7: files across stations ----
import base64 as _b64
import hashlib as _hashlib

CHUNK = 256 * 1024
MAX_FILE = 200 * 1024 * 1024


def set_roots(peer_id: str, roots: List[str]) -> Dict[str, Any]:
    d = _data()
    p = d["peers"].get(peer_id)
    if not p:
        return {"error": "no such link"}
    clean = []
    for r in roots or []:
        r = os.path.realpath(os.path.expanduser(str(r).strip()))
        if r and os.path.isdir(r) and r not in clean:
            clean.append(r)
    p["roots"] = clean
    _save(d)
    return p


def roots_for(peer: Dict[str, Any], default_roots: List[str]) -> List[str]:
    src = peer.get("roots") or default_roots or []
    return [os.path.realpath(r) for r in src if r and os.path.isdir(r)]


def safe_path(path: str, roots: List[str], must_exist: bool = True) -> Optional[str]:
    """Absolute real path inside one of roots, else None. Relative paths resolve against the first root."""
    raw = str(path or "").strip()
    if not roots or not raw:
        return None
    roots = [os.path.realpath(r) for r in roots]
    p = raw if os.path.isabs(raw) else os.path.join(roots[0], raw)
    real = os.path.realpath(p)
    if must_exist and not os.path.exists(real):
        return None
    if not must_exist and not os.path.isdir(os.path.dirname(real)):
        return None
    for r in roots:
        if real == r or real.startswith(r.rstrip(os.sep) + os.sep):
            return real
    return None


def list_dir(real: str, limit: int = 500) -> List[Dict[str, Any]]:
    out = []
    try:
        names = sorted(os.listdir(real))
    except OSError:
        return out
    for n in names[:limit]:
        fp = os.path.join(real, n)
        try:
            st = os.stat(fp)
        except OSError:
            continue
        out.append({"name": n, "dir": os.path.isdir(fp), "size": st.st_size, "mtime": int(st.st_mtime)})
    return out


def read_chunk(real: str, offset: int) -> Dict[str, Any]:
    size = os.path.getsize(real)
    with open(real, "rb") as fh:
        fh.seek(max(0, offset))
        data = fh.read(CHUNK)
    out = {"size": size, "offset": offset, "data": _b64.b64encode(data).decode(), "eof": offset + len(data) >= size}
    if out["eof"]:
        h = _hashlib.sha256()
        with open(real, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        out["sha256"] = h.hexdigest()
    return out


def write_chunk(real: str, data_b64: str, offset: int, final: bool, overwrite: bool) -> Dict[str, Any]:
    if offset == 0 and os.path.exists(real) and not overwrite:
        return {"error": "file exists (send with overwrite to replace it)"}
    part = real + ".subway-part"
    data = _b64.b64decode(data_b64 or "")
    if offset == 0:
        mode = "wb"
    elif not os.path.exists(part) or os.path.getsize(part) != offset:
        return {"error": "chunk out of order"}
    else:
        mode = "ab"
    if offset + len(data) > MAX_FILE:
        return {"error": "file too large"}
    with open(part, mode) as fh:
        fh.write(data)
    if final:
        os.replace(part, real)
        return {"ok": True, "path": real, "size": os.path.getsize(real)}
    return {"ok": True, "received": offset + len(data)}


def station_files(station: str, path: str = "") -> str:
    peer = by_slug(station)
    if not peer:
        return f"Error: no linked station called {station}."
    st = slug(peer)
    res = call(peer["peer_id"], "GET", "/api/peer/files?" + urllib.parse.urlencode({"path": path or ""}))
    if res.get("status") == 403:
        return f"{st} has not granted file access to this station, or that folder is outside what it shares."
    if res.get("status") != 200:
        return f"Error: {st} " + ("is offline." if not res.get("status") else f"answered {res.get('status')}: {(res.get('data') or {}).get('error', '')}")
    data = res.get("data") or {}
    if data.get("roots") is not None:
        return (f"Folders {st} shares:\n" + "\n".join(f"  {r}" for r in data["roots"])) if data["roots"] else f"{st} shares no folders."
    rows = [f"  {e['name']}{'/' if e.get('dir') else ''}  {'' if e.get('dir') else e.get('size')}" for e in data.get("entries") or []]
    return f"{st}:{data.get('path')}\n" + ("\n".join(rows) or "  (empty)")


def fetch_file(station: str, path: str, to: str = "") -> str:
    peer = by_slug(station)
    if not peer:
        return f"Error: no linked station called {station}."
    st = slug(peer)
    dest = os.path.abspath(os.path.expanduser(to or os.path.basename(path)))
    if os.path.isdir(dest):
        dest = os.path.join(dest, os.path.basename(path))
    if os.path.exists(dest):
        return f"Error: {dest} exists here; choose another name."
    part, offset, h, d = dest + ".subway-part", 0, _hashlib.sha256(), {}
    with open(part, "wb") as fh:
        while True:
            res = call(peer["peer_id"], "GET", "/api/peer/file?" + urllib.parse.urlencode({"path": path, "offset": offset}), timeout=60)
            if res.get("status") != 200:
                fh.close()
                os.remove(part)
                err = (res.get("data") or {}).get("error") if res.get("status") else "offline"
                return f"Error: {st} did not send {path}: {err or res.get('status')}"
            d = res.get("data") or {}
            blob = _b64.b64decode(d.get("data") or "")
            fh.write(blob)
            h.update(blob)
            offset += len(blob)
            if d.get("eof") or not blob:
                break
            if offset > MAX_FILE:
                fh.close()
                os.remove(part)
                return "Error: file too large."
    if d.get("sha256") and d["sha256"] != h.hexdigest():
        os.remove(part)
        return f"Error: {path} arrived damaged (checksum mismatch); nothing kept."
    os.replace(part, dest)
    return f"Fetched {st}:{path} to {dest} ({offset} bytes)."


def send_file(station: str, path: str, to: str, overwrite: bool = False) -> str:
    peer = by_slug(station)
    if not peer:
        return f"Error: no linked station called {station}."
    st = slug(peer)
    src = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(src):
        return f"Error: {src} is not a file here."
    size = os.path.getsize(src)
    if size > MAX_FILE:
        return "Error: file too large (200 MB max)."
    offset = 0
    with open(src, "rb") as fh:
        while True:
            blob = fh.read(CHUNK)
            final = offset + len(blob) >= size
            res = call(peer["peer_id"], "POST", "/api/peer/file", {"path": to, "offset": offset, "final": final,
                       "overwrite": bool(overwrite), "data": _b64.b64encode(blob).decode()}, timeout=60)
            if res.get("status") != 200:
                err = (res.get("data") or {}).get("error") if res.get("status") else "offline"
                return f"Error: {st} refused {to}: {err or res.get('status')}"
            offset += len(blob)
            if final:
                return f"Sent {src} to {st}:{(res.get('data') or {}).get('path') or to} ({size} bytes)."


# ---- P8a: apps and media ----
APP_CAPS = {"browser": ("browser", "desktop"), "desktop": ("desktop",)}
PEER_REFUSED_CAPS = ("terminal:full",)
MEDIA_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def check_caps(peer: Dict[str, Any], caps: List[str]) -> Tuple[List[str], str]:
    """(allowed caps, '' ) or ([], reason) for capabilities a linked station asked for."""
    g = effective_grants(peer)
    out = []
    for c in caps or []:
        c = str(c)
        if c in PEER_REFUSED_CAPS:
            return [], f"linked stations cannot ask for {c}"
        if c in APP_CAPS and g.get("apps", "off") not in APP_CAPS[c]:
            return [], f"this station has not granted {c} use to {slug(peer)}"
        if c not in out:
            out.append(c)
    return out, ""


def media_dir(name: str) -> str:
    import subway_home
    return os.path.join(subway_home.subway_dir(), "hub_media", name)


def local_media(worker_id: str, limit: int = 6, max_bytes: int = 3_000_000) -> List[Dict[str, Any]]:
    """Images a local worker saved, base64, newest first (answers the station that started it)."""
    d = media_dir(worker_id)
    if not os.path.isdir(d):
        return []
    files = sorted((f for f in os.listdir(d) if f.lower().endswith(MEDIA_EXT)),
                   key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)[:limit]
    out = []
    for f in files:
        p = os.path.join(d, f)
        if os.path.getsize(p) <= max_bytes:
            with open(p, "rb") as fh:
                out.append({"name": f, "data": _b64.b64encode(fh.read()).decode()})
    return out


def pull_media(stub: Dict[str, Any]) -> List[str]:
    """Copy a remote worker's images here (hub_media/worker_x@station/). Returns saved file names."""
    peer = get(stub.get("peer_id", ""))
    if not peer:
        return []
    res = call(peer["peer_id"], "GET", "/api/peer/media?id=" + urllib.parse.quote(stub["worker_id"]), timeout=60)
    if res.get("status") != 200:
        return []
    d = media_dir(f"{stub['worker_id']}@{stub['station']}")
    os.makedirs(d, exist_ok=True)
    saved = []
    for m in (res.get("data") or {}).get("media") or []:
        name = os.path.basename(str(m.get("name") or ""))
        if not name.lower().endswith(MEDIA_EXT):
            continue
        with open(os.path.join(d, name), "wb") as fh:
            fh.write(_b64.b64decode(m.get("data") or ""))
        saved.append(name)
    return saved


# ---- two-station coordination view (item 59) ----


def _active_workers_local(limit: int = 20) -> List[Dict[str, Any]]:
    """Best-effort snapshot of this station's active workers for coordination UIs."""
    out = []
    try:
        import server as _s
        root = getattr(_s, "WORKERS_DIR", None) or os.path.expanduser("~/.subway-context/workers")
        if not os.path.isdir(root):
            return out
        for name in sorted(os.listdir(root), reverse=True):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(root, name), "r", encoding="utf-8") as fh:
                    w = json.load(fh)
            except (OSError, ValueError):
                continue
            status = str(w.get("status") or "")
            if status not in ("pending", "running", "starting", "waiting_approval", "blocked"):
                continue
            out.append({
                "id": w.get("id") or name[:-5],
                "status": status,
                "role": w.get("role") or "",
                "model": w.get("model") or "",
                "task": str(w.get("task") or w.get("prompt") or "")[:160],
                "claim": w.get("file_claim") or w.get("claimed_file") or "",
                "station": "local",
            })
            if len(out) >= limit:
                break
    except Exception:
        return out
    return out


def _recent_decisions_local(track: str = "main", limit: int = 10) -> List[Dict[str, Any]]:
    """Recent track decisions/events for coordination handoffs."""
    try:
        import server as _s
        raw = _s.t_read_track(track=track, limit=limit)
        # t_read_track returns text; expose a compact structured tail for UIs.
        if isinstance(raw, dict):
            ev = raw.get("events") or []
            return ev[:limit] if isinstance(ev, list) else []
        return [{"text": str(raw)[:4000]}] if raw else []
    except Exception:
        return []


def person_name(peer: Optional[Dict[str, Any]] = None) -> str:
    """Who a station belongs to: its peer record, else SUBWAY_PERSON, else the login user."""
    if isinstance(peer, dict):
        who = str(peer.get("person") or "").strip()
        if who:
            return who
    who = str(os.environ.get("SUBWAY_PERSON") or "").strip()
    if who:
        return who
    try:
        import getpass
        return (getpass.getuser() or "").strip() or "You"
    except Exception:
        return "You"


def local_coordination_state(station: str = "local", track: str = "main") -> Dict[str, Any]:
    """Live coordination snapshot for this station: workers, claims, recent decisions."""
    now = time.time()
    claims = []
    try:
        import server as _s
        locks = ((_s.load_locks() or {}).get("locks") or {})
        for key, v in (locks or {}).items():
            if "::" in key or float(v.get("expires_at") or 0) <= now:
                continue
            ident = repo_identity(key)
            rel = ident[1] if ident else key
            claims.append({
                "path": key, "rel": rel, "holder": v.get("holder"),
                "purpose": v.get("purpose") or "",
                "expires_in": int(float(v.get("expires_at", 0)) - now),
            })
    except Exception:
        claims = []
    return {
        "station": station,
        "person": person_name(),
        "workers": _active_workers_local(),
        "claims": claims,
        "recent_decisions": _recent_decisions_local(track=track),
        "track": track,
        "ts": time.time(),
    }


def coordination_view(track: str = "main", peer_slug: str = "") -> Dict[str, Any]:
    """Shared coordination view across this station and one linked station (live workers, claims, recent decisions)."""
    local = local_coordination_state(station="local", track=track)
    peers_now = list(_data()["peers"].values())
    target = by_slug(peer_slug) if peer_slug else (next((p for p in peers_now if p.get("relation") == "own"), peers_now[0]) if peers_now else None)
    remote: Dict[str, Any] = {"station": peer_slug or "", "offline": True, "workers": [], "claims": [], "recent_decisions": []}
    if target:
        st = slug(target)
        remote["station"] = st
        res = call(target["peer_id"], "GET", "/api/peer/coordination?" + urllib.parse.urlencode({"track": track}), timeout=8)
        if res.get("status") == 200 and isinstance(res.get("data"), dict):
            d = res["data"]
            remote = {"station": d.get("station") or st, "offline": False, "workers": d.get("workers") or [],
                      "claims": d.get("claims") or [], "recent_decisions": d.get("recent_decisions") or [],
                      "track": d.get("track") or track}
    return {"track": track, "local": local, "remote": remote, "ts": time.time()}


def coordination_view_all(track: str = "main") -> Dict[str, Any]:
    """Coordination for every linked station, grouped by person (one group per train)."""
    me = person_name()
    local = local_coordination_state(station="local", track=track)
    local["relation"] = "own"
    groups: List[Dict[str, Any]] = [{"person": me, "engine": "local", "offline": False,
                                     "stations": [local], "ts": local.get("ts")}]
    for p in list(_data()["peers"].values()):
        st = slug(p)
        q = "/api/peer/coordination?" + urllib.parse.urlencode({"track": track})
        res = call(p["peer_id"], "GET", q, timeout=8)
        ok = res.get("status") == 200 and isinstance(res.get("data"), dict)
        d = res["data"] if ok else {}
        entry = {
            "station": d.get("station") or st,
            "person": str(d.get("person") or person_name(p)),
            "relation": str(p.get("relation") or "partner"),
            "train_role": str(d.get("train_role") or p.get("train_role") or "engine"),
            "via": str(d.get("via") or ""),
            "offline": not ok,
            "workers": d.get("workers") or [],
            "claims": d.get("claims") or [],
            "recent_decisions": d.get("recent_decisions") or [],
        }
        if entry["relation"] == "own":
            groups[0]["stations"].append(entry)
            continue
        key = entry["person"] or ("train-" + st)
        group = next((g for g in groups[1:] if g["person"] == key), None)
        if group is None:
            group = {"person": key, "engine": st, "offline": True, "stations": [], "ts": time.time()}
            groups.append(group)
        group["stations"].append(entry)
        if not entry["offline"]:
            group["offline"] = False
    return {"track": track, "person": me, "groups": groups, "ts": time.time()}


def record_handoff(to_station: str, subject: str = "handoff", text: str = "", files: Optional[List[str]] = None,
                   track: str = "main") -> Dict[str, Any]:
    """Record an explicit handoff request on the shared track so both stations see it (item 59)."""
    from cli_tools.tracks import t_broadcast_track
    to_station = str(to_station or "").lstrip("@").strip()
    subject = str(subject or "handoff").strip() or "handoff"
    track = str(track or "main").strip() or "main"
    files = [str(f) for f in (files or []) if str(f).strip()]
    if not to_station:
        return {"ok": False, "error": "to is required"}
    msg = f"[HANDOFF] to=@{to_station} subject={subject} :: {text or 'Handoff: ' + subject}"
    res = t_broadcast_track(message=msg, track=track, files=files or None, decisions=[f"handoff:{subject}"])
    if str(res).startswith("Error"):
        return {"ok": False, "error": str(res)}
    return {"ok": True, "track": track, "to": to_station, "subject": subject, "files": files}


ALLOWANCE_FINAL = {"completed", "failed", "stopped", "rejected", "budget_stopped", "timeout", "error", "cancelled"}


def _ts(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(str(v)).timestamp()
    except Exception:
        return 0.0


def set_allowance(peer_id: str, tokens_per_day: Any) -> Dict[str, Any]:
    """Owner side: cap the tokens a linked station may spend here per day on workers it launches (0 = no cap)."""
    try:
        n = max(0, int(tokens_per_day or 0))
    except (TypeError, ValueError):
        return {"error": "tokens_per_day must be a whole number"}
    d = _data()
    p = d["peers"].get(str(peer_id or ""))
    if not p:
        return {"error": "unknown peer"}
    p["allowance"] = {"tokens_per_day": n}
    _save(d)
    return {"ok": True, "peer_id": p["peer_id"], **allowance_status(p["peer_id"])}


def _peer_tokens_today(peer_id: str) -> int:
    """Tokens used today by workers this linked station launched here; running workers count their max_tokens."""
    try:
        from workers.outcomes import _all_workers
        from workers.activity import _worker_tokens
    except Exception:
        return 0
    lt = time.localtime()
    start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    total = 0
    for m in _all_workers():
        if not isinstance(m, dict) or (m.get("origin") or {}).get("peer_id") != peer_id:
            continue
        if _ts(m.get("created_at")) < start:
            continue
        used = _worker_tokens(m)
        if str(m.get("status") or "") not in ALLOWANCE_FINAL:
            try:
                cap = int(m.get("max_tokens") or (m.get("budget") or {}).get("max_tokens") or 0)
            except (TypeError, ValueError):
                cap = 0
            used = max(used, cap)
        total += used
    return total


def allowance_status(peer_id: str) -> Dict[str, Any]:
    p = get(peer_id) or {}
    cap = int((p.get("allowance") or {}).get("tokens_per_day") or 0)
    used = _peer_tokens_today(peer_id) if cap else 0
    return {"tokens_per_day": cap, "used_today": used, "left": max(0, cap - used) if cap else None}


HISTORY_DAYS = 14


def _day_key(ts: float) -> str:
    lt = time.localtime(ts)
    return "%04d-%02d-%02d" % (lt.tm_year, lt.tm_mon, lt.tm_mday)


def _peer_tokens_by_day(peer_id: str, days: int = HISTORY_DAYS) -> Dict[str, int]:
    """Tokens per local calendar day, last `days` days, archived workers included."""
    try:
        from workers.outcomes import _all_workers
        from workers.activity import _worker_tokens
    except Exception:
        return {}
    out: Dict[str, int] = {}
    now = time.time()
    for m in _all_workers(include_archive=True):
        if not isinstance(m, dict) or (m.get("origin") or {}).get("peer_id") != peer_id:
            continue
        created = _ts(m.get("created_at"))
        if now - created > days * 86400:
            continue
        used = _worker_tokens(m)
        if str(m.get("status") or "") not in ALLOWANCE_FINAL:
            try:
                cap = int(m.get("max_tokens") or (m.get("budget") or {}).get("max_tokens") or 0)
            except (TypeError, ValueError):
                cap = 0
            used = max(used, cap)
        key = _day_key(created)
        out[key] = out.get(key, 0) + int(used)
    return out


def allowance_history_snapshot(peer_id: str, days: Any = HISTORY_DAYS) -> List[Dict[str, Any]]:
    """Last `days` days of tokens used by workers this linked station launched here, oldest first.
    Today's total is persisted into peers.json so pruned workers don't erase history."""
    try:
        days = max(1, min(90, int(days or HISTORY_DAYS)))
    except (TypeError, ValueError):
        days = HISTORY_DAYS
    d = _data()
    p = d["peers"].get(peer_id) or {}
    stored = p.get("allowance_history") if isinstance(p.get("allowance_history"), dict) else {}
    today_used = _peer_tokens_today(peer_id)
    if today_used or stored:
        today = _day_key(time.time())
        stored[today] = max(int(today_used), int(stored.get(today, 0) or 0))
        kept = sorted(stored)[-90:]
        p["allowance_history"] = {k: int(stored[k]) for k in kept}
        _save(d)
    by_day = _peer_tokens_by_day(peer_id, days)
    cap = int((p.get("allowance") or {}).get("tokens_per_day") or 0)
    lt = time.localtime()
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    out = []
    for i in range(days - 1, -1, -1):
        key = _day_key(midnight - i * 86400)
        used = by_day.get(key, 0) or int(stored.get(key, 0) or 0)
        out.append({"date": key, "used": int(used), "cap": cap if cap else None})
    return out


def allowance_limit(peer_id: str, requested: int) -> Tuple[int, str]:
    """(max_tokens to launch with, refusal reason or ''). No cap: the request passes unchanged."""
    st = allowance_status(peer_id)
    if not st["tokens_per_day"]:
        return int(requested or 0), ""
    left = int(st["left"] or 0)
    if left <= 0:
        return 0, f"daily token allowance used up ({st['used_today']} of {st['tokens_per_day']})"
    req = int(requested or 0)
    return (min(req, left) if req > 0 else left), ""


def remote_allowance(station: str) -> Dict[str, Any]:
    """Partner side: what the linked station `station` lets this station spend there today."""
    peer = by_slug(station) or get(station)
    if not peer:
        return {"station": station, "error": f"no linked station called {station}"}
    res = call(peer["peer_id"], "GET", "/api/peer/allowance", timeout=10)
    if res.get("status") != 200:
        return {"station": slug(peer), "error": res.get("error") or f"answered {res.get('status')}"}
    return {"station": slug(peer), **(res.get("data") or {})}


# ---- Mode C: swarm (the sender's own agents work on this station's checkout) ----
SWARM_MAX_BUNDLE = 200 * 1024 * 1024
SWARM_BUNDLE_KEEP_SECONDS = 86400


def swarm_dir() -> str:
    return os.path.join(subway_home.subway_dir(), "swarm")


def swarm_repo(project: str) -> str:
    """Git top-level of a project folder, or '' when it is not a repo."""
    if not project or not os.path.isdir(project):
        return ""
    code, top = _git_out(project, "rev-parse", "--show-toplevel")
    return top.strip() if code == 0 else ""


def swarm_claims(repo: str, locks: Any) -> List[Dict[str, Any]]:
    """Live claims on files inside `repo`, as repo-relative rows."""
    rows = locks.get("locks") if isinstance(locks, dict) else None
    if not isinstance(rows, dict) or not repo:
        return []
    top = os.path.normcase(os.path.realpath(repo))
    now = time.time()
    out: List[Dict[str, Any]] = []
    for key, row in rows.items():
        if not isinstance(row, dict):
            continue
        try:
            exp = float(row.get("expires_at") or 0)
            real = os.path.normcase(os.path.realpath(str(key)))
        except (TypeError, ValueError, OSError):
            continue
        if exp < now or not real.startswith(top + os.sep):
            continue
        out.append({"rel": os.path.relpath(real, top).replace(os.sep, "/"),
                    "holder": str(row.get("holder") or "")[:80],
                    "purpose": str(row.get("purpose") or "")[:120], "expires_in": int(exp - now)})
    return out


def swarm_conflicts(repo: str, locks: Any, files: List[str], holder: str = "") -> List[str]:
    """Files in `files` that someone other than `holder` has claimed on this station."""
    held = {c["rel"] for c in swarm_claims(repo, locks) if c.get("holder") != holder}
    return sorted(f for f in files if f in held)


def swarm_bundle(repo: str) -> Tuple[str, str]:
    """(bundle path, commit) for this checkout including uncommitted work; ('', '') on failure."""
    head = ""
    try:
        from workers.spawn import _worktree_base
        head = str(_worktree_base(repo) or "").strip()
    except Exception:
        head = ""
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        # _worktree_base (tracked + untracked edits) unavailable: stash snapshot (tracked edits), then HEAD.
        code, out = _git_out(repo, "stash", "create")
        head = out.strip() if code == 0 else ""
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        code, out = _git_out(repo, "rev-parse", "HEAD")
        head = out.strip() if code == 0 else ""
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        return "", ""
    d = swarm_dir()
    os.makedirs(d, exist_ok=True)
    now = time.time()
    for name in os.listdir(d):  # drop stale bundles
        p = os.path.join(d, name)
        if name.endswith(".bundle") and now - os.path.getmtime(p) > SWARM_BUNDLE_KEEP_SECONDS:
            try:
                os.remove(p)
            except OSError:
                pass
    path = os.path.join(d, head + ".bundle")
    if not os.path.isfile(path):
        ref = "refs/subway/swarm/" + head[:12]
        _git_out(repo, "update-ref", ref, head)
        code, _out = _git_out(repo, "bundle", "create", path + ".part", ref)
        if code != 0 or not os.path.isfile(path + ".part"):
            try:
                os.remove(path + ".part")
            except OSError:
                pass
            return "", ""
        os.replace(path + ".part", path)
    return path, head


def swarm_bundle_file(head: str) -> str:
    """Path of an already-built bundle for this commit, else ''. Never builds one."""
    if not re.fullmatch(r"[0-9a-f]{40}", str(head or "")):
        return ""
    p = os.path.join(swarm_dir(), head + ".bundle")
    return p if os.path.isfile(p) else ""


def swarm_manifest(peer: Dict[str, Any], repo: str, locks: Any, requested: int = 0) -> Tuple[int, Dict[str, Any]]:
    """Receiver: what a linked sender needs to start a swarm worker here. (status, body)."""
    _cap, why = allowance_limit(peer["peer_id"], int(requested or 0))
    if why:
        return 403, {"error": why}
    path, head = swarm_bundle(repo)
    if not path:
        return 500, {"error": "could not package this checkout"}
    size = os.path.getsize(path)
    if size > SWARM_MAX_BUNDLE:
        return 413, {"error": "this checkout is too large to send"}
    ident = repo_identity(repo)
    return 200, {"root": ident[0] if ident else "", "head": head, "size": size,
                 "claims": swarm_claims(repo, locks)}


def swarm_submit(peer: Dict[str, Any], body: Any, repo: str, locks: Any) -> Tuple[int, Dict[str, Any]]:
    """Receiver: a sender's swarm worker finished. Refuse claimed files, else record an awaiting-land item."""
    body = body if isinstance(body, dict) else {}
    wid = str(body.get("worker_id") or "")
    if not _WORKER_RE.fullmatch(wid):
        return 400, {"error": "worker_id is required"}
    raw = body.get("files") if isinstance(body.get("files"), list) else []
    files = [f for f in raw if isinstance(f, str) and f][:500]
    if not files or any(os.path.isabs(f) or ".." in f.split("/") for f in files):
        return 400, {"error": "files must be repo-relative paths"}
    clash = swarm_conflicts(repo, locks, files, "swarm:" + slug(peer))
    if clash:
        return 409, {"error": "claimed on this station: " + ", ".join(clash[:8]), "files": clash[:20]}
    try:
        tokens = max(0, int(body.get("tokens") or 0))
    except (TypeError, ValueError):
        tokens = 0
    _remember_remote(wid, peer, str(body.get("role") or "swarm worker"), "swarm")
    d = _data()
    stub = d.setdefault("remote_workers", {}).get(wid)
    if stub is not None:
        stub.update({"swarm": True, "mode": "C", "state": "awaiting_land", "files": files[:50], "tokens": tokens,
                     "requested_by": str(body.get("requested_by") or "")[:120], "paid_by": slug(peer),
                     "head": str(body.get("head") or "")[:40], "submitted_at": time.time()})
        _save(d)
        try:
            import usage_ledger
            usage_ledger.record(wid, stub["requested_by"], stub["paid_by"], str(body.get("provider") or "swarm"),
                                str(body.get("model") or ""), tokens, slug(peer), mode="C")
        except Exception:
            pass
    return 200, {"ok": True, "state": "awaiting_land"}


def swarm_tokens_today(peer_id: str) -> int:
    """Tokens a linked sender reported for swarm workers submitted here today."""
    lt = time.localtime()
    start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    total = 0
    for s in (_data().get("remote_workers") or {}).values():
        if isinstance(s, dict) and s.get("swarm") and s.get("peer_id") == peer_id and _ts(s.get("submitted_at")) >= start:
            try:
                total += max(0, int(s.get("tokens") or 0))
            except (TypeError, ValueError):
                pass
    return total


def swarm_marker(path: str) -> Dict[str, Any]:
    """The marker written for a swarm checkout dir (<subway>/swarm/<id> -> <id>.json), or {}."""
    try:
        real = os.path.realpath(str(path or ""))
        if os.path.realpath(os.path.dirname(real)) != os.path.realpath(swarm_dir()):
            return {}
        with open(real + ".json", "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def swarm_checkout(station: str, project: str, dest: str, max_tokens: int = 0, requested_by: str = "") -> Dict[str, Any]:
    """Sender: pull the receiver's checkout over the link into `dest` (a new folder) and write its marker.
    Returns {"error"} or {"station", "peer_id", "root", "head", "claims", "dest"}."""
    peer = by_slug(station)
    if not peer:
        return {"error": f"no linked station called {station}."}
    st = slug(peer)
    if os.path.exists(dest):
        return {"error": f"{dest} already exists."}
    q = urllib.parse.urlencode({"project": project, "max_tokens": int(max_tokens or 0)})
    res = call(peer["peer_id"], "GET", "/api/peer/swarm/manifest?" + q, timeout=180)
    if res.get("status") == 403:
        return {"error": f"{st} has not granted swarm work to this station ({(res.get('data') or {}).get('error') or 'swarm grant off'})."}
    if res.get("status") != 200:
        err = (res.get("data") or {}).get("error") if res.get("status") else "offline"
        return {"error": f"{st} did not send its checkout: {err or res.get('status')}", "offline": not res.get("status")}
    man = res.get("data") or {}
    head = str(man.get("head") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        return {"error": f"{st} sent an unreadable checkout description."}
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part, offset, h, d = dest + ".bundle.part", 0, _hashlib.sha256(), {}
    try:
        with open(part, "wb") as fh:
            while True:
                r = call(peer["peer_id"], "GET", "/api/peer/swarm/bundle?" + urllib.parse.urlencode(
                    {"project": project, "head": head, "offset": offset}), timeout=60)
                if r.get("status") != 200:
                    return {"error": f"{st} stopped sending its checkout at {offset} bytes."}
                d = r.get("data") or {}
                blob = _b64.b64decode(d.get("data") or "")
                fh.write(blob)
                h.update(blob)
                offset += len(blob)
                if d.get("eof") or not blob:
                    break
                if offset > SWARM_MAX_BUNDLE:
                    return {"error": "checkout too large."}
        if d.get("sha256") and d["sha256"] != h.hexdigest():
            return {"error": "the checkout arrived damaged (checksum mismatch); nothing kept."}
        ref = "refs/subway/swarm/" + head[:12]
        os.makedirs(dest)
        for args in (("init", "-q"), ("fetch", "-q", part, ref + ":refs/heads/swarm-base"),
                     ("checkout", "-q", "-B", "main", "swarm-base")):
            code, out = _git_out(dest, *args)
            if code != 0:
                shutil.rmtree(dest, ignore_errors=True)
                return {"error": "could not unpack the checkout: " + out.strip()[:200]}
    finally:
        try:
            os.remove(part)
        except OSError:
            pass
    info = {"station": st, "peer_id": peer["peer_id"], "root": str(man.get("root") or ""), "head": head,
            "claims": man.get("claims") if isinstance(man.get("claims"), list) else [],
            "project": project, "requested_by": str(requested_by or "")[:120], "created_at": time.time()}
    with open(dest + ".json", "w", encoding="utf-8") as fh:
        json.dump(info, fh)
    return dict(info, dest=dest)


def swarm_submit_remote(meta: Dict[str, Any], marker: Dict[str, Any], diff: str) -> str:
    """Sender: tell the receiver which files the finished swarm worker changed. Returns a status note."""
    peer = get(str(marker.get("peer_id") or ""))
    if not peer:
        return "The receiving station's link is gone; the patch stays here."
    st = slug(peer)
    files = sorted(set(re.findall(r"^diff --git a/(.+) b/\1$", diff, re.M)))
    if not files:
        return "No files changed."
    try:
        from workers.activity import _worker_tokens
        tokens = int(_worker_tokens(meta) or 0)
    except Exception:
        tokens = 0
    body = {"project": marker.get("project") or "", "worker_id": meta.get("id"), "role": meta.get("role") or "",
            "files": files, "tokens": tokens, "requested_by": marker.get("requested_by") or "", "head": marker.get("head") or ""}
    res = call(peer["peer_id"], "POST", "/api/peer/swarm/submit", body, timeout=30)
    if res.get("status") == 200:
        return f"Patch sent to {st} for approval ({len(files)} files)."
    if res.get("status") == 409:
        return f"{st} refused the patch: {(res.get('data') or {}).get('error') or 'files are claimed there'}. Patch kept here."
    if res.get("status") == 403:
        return f"{st} no longer grants swarm work to this station. Patch kept here."
    return f"{st} is unreachable; patch kept here ({res.get('error') or res.get('status')})."
