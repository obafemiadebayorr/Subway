from __future__ import annotations

import json

import server as _s
def t_estimate_complexity(prompt: str, role: str = "") -> str:
    """Evaluate task complexity and return recommended effort, tier, and provider."""
    res = _s.estimate_complexity(prompt, role)
    route = _s.route_task(prompt)
    return (f"# Task Complexity Assessment\n"
            f"- **Role**: {role or '(unspecified)'}\n"
            f"- **Complexity Tier**: {res['tier'].upper()}\n"
            f"- **Recommended Effort**: {res['effort'].upper()}\n"
            f"- **Mode**: {res['mode'].upper()}\n"
            f"- **Route, best first**: {' > '.join(route['order'])}\n"
            f"- **Rationale**: {route['reason']}")


def t_answer_permission(request_id: str, decision: str) -> str:
    """Pass on the user's answer. Only the window that launched the worker can do this."""
    raw_id = str(request_id or "").strip()
    if "@" in raw_id:
        if raw_id.startswith("@"):
            rest = raw_id[1:]
            if "/" not in rest:
                return f"Error: invalid remote permission id `{raw_id}`. Expected `@station/perm_id` or `perm_id@station`."
            station_name, rid = rest.split("/", 1)
        else:
            rid, station_name = raw_id.rsplit("@", 1)
        station_name = station_name.strip().lstrip("@")
        rid = rid.strip()
        if not rid or not station_name:
            return f"Error: invalid remote permission id `{raw_id}`."
        dec = str(decision or "").strip()
        if dec not in ("allow", "allow_always", "deny"):
            return "Error: decision must be allow, allow_always, or deny."
        import peers as _peers
        peer = _peers.by_slug(station_name)
        if not peer:
            return f"Error: no linked station called {station_name}."
        res = _peers.call(peer["peer_id"], "POST", "/api/answer_permission", {"id": rid, "decision": dec})
        status = res.get("status")
        if status == 200:
            return f"Answered remote permission `{rid}` on `@{_peers.slug(peer)}` with `{dec}`."
        data = res.get("data") if isinstance(res.get("data"), dict) else {}
        err = data.get("error") or res.get("error") or f"HTTP {status}"
        return f"Error ({status}): {err}" if status else f"Error: {err}"

    me = _s._self_id()
    try:
        with open(_s._perm_file(request_id), "r", encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return f"Error: permission request `{request_id}` not found."
    if rec.get("launched_by") != me:
        return f"[REFUSED] `{request_id}` belongs to the window that launched `{rec.get('worker_id')}`."
    if rec.get("tool") == "run_terminal":
        # The chat that asks to run a terminal command is also its own "launcher", so letting the
        # tool answer would let an agent approve its own command (npm publish included).
        return (f"[REFUSED] `{request_id}` is a terminal command. Your user approves it in the HUD "
                "or in the prompt their chat shows; agents can't answer it.")
    if rec.get("kind") == "fastmail_offline":
        # Resuming an offline chat spends the user's budget; only the user approves in the HUD.
        return (f"[REFUSED] `{request_id}` is an offline fastmail resume. Your user approves it in the HUD; "
                "agents can't answer it.")
    return _s._answer_permission(request_id, decision, _s._short_id(me))


def t_permission_requests(worker_id: str = "") -> str:
    """List permission requests from workers that are waiting on the user."""
    pending = _s._pending_permissions(worker_id=worker_id)
    return _s.permission_text(pending) if pending else "No permission requests are waiting."
