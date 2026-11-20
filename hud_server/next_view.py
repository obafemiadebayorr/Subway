"""Slimmed status view for the hud_next frontend (GET /api/status?view=next).

Ported from the old dev server reference (removed) so the live HUD can serve the
prototype without the dev proxy. Only safe, UI-needed fields are exposed.
"""
from __future__ import annotations

from typing import Any, Dict

# Workers page: safe worker fields only (never prompt, secret, env or paths).
WK_KEYS = (
    "id", "role", "status", "provider", "model", "effort", "track", "created_at",
    "started_at", "completed_at", "exit_code", "isolate", "branch_name", "merged", "branch_discarded", "branch_gone",
    "launched_by", "requested_by", "paid_by", "mode", "swarm_id", "reason",
    "tool_calls", "priority", "archived", "thread_root",
)

# Settings the prototype pages show, read from the status snapshot (never budgets, quotas or secrets).
READ = {
    "permission_mode", "permission_modes", "max_local_workers", "local_memory_guard",
    "local_concurrency_limits", "private_routing", "hub_max_local", "hub_max_remote",
    "hub_concurrency_limits", "compaction", "compact_at_tokens", "archive", "principal",
    "caffeinate", "chat_branch_auto", "git_branch_mode", "land_mode", "worktree_cleanup",
    "worker_log_days", "hud_autostart", "hud_open_in", "hud_wallpaper", "transplant_turns", "transplant_full", "hud_open_on_use",
    "notification_targets", "notification_webhook", "reply_style", "table_rows",
    "projector_enabled", "projector_max_lines", "nudge_builder_calls",
    "nudge_other_calls", "token_controls", "wake", "terminal_mode",
    "transplant_turns", "transplant_full",
    "protected_commands", "protected_exceptions", "doctor_mode", "doctor_budget_mode",
    "doctor_budget_tokens", "doctor_model", "models", "benchmarks", "budgets",
    "launch_approval_remote", "link_default_preset", "link_default_allowance",
}


def _wk(w: Dict[str, Any]) -> Dict[str, Any]:
    o = {k: w.get(k) for k in WK_KEYS if w.get(k) not in (None, "")}
    u = w.get("usage") or {}
    if isinstance(u, dict):
        o["tokens"] = int(u.get("input") or 0) + int(u.get("output") or 0) or int(u.get("total") or 0)
    if isinstance(w.get("review"), (str, bool)):
        o["review"] = w["review"]
    for src in (w.get("budget"), w.get("spin_args")):
        if isinstance(src, dict) and str(src.get("max_tokens") or "").isdigit() and int(src["max_tokens"]) > 0:
            o["cap"] = int(src["max_tokens"])
            break
    sa = w.get("spin_args") if isinstance(w.get("spin_args"), dict) else {}
    bm = (w.get("billing_mode") or sa.get("billing_mode") or "")
    if str(bm).upper() in ("A", "B", "C"):
        o["billing_mode"] = str(bm).upper()
    r = w.get("report")
    if isinstance(r, str) and r.strip():
        o["report"] = r[-1500:]
    bs = w.get("borrowed_skills") or sa.get("skills") or []
    if isinstance(bs, list) and bs:
        o["skills"] = [str(x.get("name") if isinstance(x, dict) else x)[:80] for x in bs][:8]
    nd = sa.get("needs") or w.get("needs") or []
    if isinstance(nd, list) and nd:
        o["mcp"] = [str(x)[:60] for x in nd][:8]
    return o


def slim(data: Dict[str, Any], models: bool = False) -> Dict[str, Any]:
    """Return the reduced status payload for hud_next."""
    st = data.get("settings") or {}
    out = {
        "capacity": st.get("capacity") or {},
        "settings": {k: st[k] for k in READ if k in st},
        "workers": [_wk(w) for w in (data.get("workers") or [])],
        "history": [_wk(w) for w in (data.get("history") or [])],
        "total_history": data.get("total_history") or 0,
        # Usage page: limits per tool and the Copilot inline estimate (counts only, no keys).
        "quotas": data.get("quotas") or {},
        "copilot_inline_estimate": data.get("copilot_inline_estimate") or {},
        "timestamp": data.get("timestamp"),
        # Workers page: leases, sign-offs, permissions and decision tracks.
        "locks": data.get("locks") or {},
        "signoffs": data.get("signoffs") or {},
        "tracks": data.get("tracks") or {},
        "permissions": data.get("permissions") or [],
    }
    # Models tab: ranking track and models out of allowance.
    if models:
        out["models_track"] = data.get("models_track")
        out["exhausted_models"] = data.get("exhausted_models")
    return out
