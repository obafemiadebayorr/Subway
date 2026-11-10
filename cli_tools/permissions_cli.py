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
    return _s._answer_permission(request_id, decision, _s._short_id(me))


def t_permission_requests(worker_id: str = "") -> str:
    """List permission requests from workers that are waiting on the user."""
    pending = _s._pending_permissions(worker_id=worker_id)
    return _s.permission_text(pending) if pending else "No permission requests are waiting."
