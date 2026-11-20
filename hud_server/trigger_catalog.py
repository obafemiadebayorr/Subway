"""Trigger catalog for the composer's "When it runs" picker (GET /api/mail/triggers).

Field keys mirror postmail's validators (create_mail_item / _validate_condition). Options come from live
sources (quota providers and pools, GitHub sign-in state, git remote). No network calls: it must be fast.
"""
import os
from typing import Any, Dict, List


def _opt(v, l=None):
    return {"value": v, "label": l if l is not None else str(v)}


def _providers() -> List[Dict[str, Any]]:
    try:
        import server
        return [p for p in (server.load_quotas(refresh=False).get("providers") or []) if isinstance(p, dict) and p.get("id")]
    except Exception:
        return []


def _github_state():
    try:
        import oauth_tokens
        rec = oauth_tokens.load("github")
        if rec and rec.get("status") != "disconnected":
            return True, ""
    except Exception:
        pass
    return False, "Connect GitHub in the HUD's MCP list"


def _app_state(app_id: str):
    try:
        from hud_server import app_connectors
        return app_connectors.app_state(app_id)
    except Exception:
        return False, "App connector unavailable"


GITHUB_LABELS = {"push": "New push", "pr_opened": "PR opened", "pr_merged": "PR merged", "ci": "CI finished",
                 "issue": "Issue opened", "comment": "New comment", "release": "New release", "tag": "New tag",
                 "branch": "New branch", "review": "PR review"}
REPEAT_LABELS = {"once": "Once", "daily": "Every day", "weekdays": "Weekdays", "weekly": "Every week"}


def trigger_catalog(project_dir: str = "") -> List[Dict[str, Any]]:
    import postmail
    from hud_server import github_watch
    provs = _providers()
    prov_opts = [_opt(p["id"], p.get("name") or p.get("label") or p["id"]) for p in provs]
    pool_fields = []
    for p in provs:
        pools = [pl for pl in (p.get("pools") or []) if isinstance(pl, dict) and pl.get("key")]
        if len(pools) > 1:
            pool_fields.append({"key": "pool", "label": "Pool", "kind": "select", "required": False, "default": None,
                                "options": [_opt(pl["key"], pl.get("name") or pl["key"]) for pl in pools],
                                "show_if": {"provider": [p["id"]]}})
    first = prov_opts[0]["value"] if prov_opts else None
    gh_ok, gh_reason = _github_state()
    rem_ok, rem_reason = _app_state("reminders")
    cal_ok, cal_reason = _app_state("calendar")
    try:
        repo = github_watch.default_repo(project_dir or os.getcwd())
    except Exception:
        repo = ""
    cat = [
        {"type": "time", "label": "At a time", "group": "Time", "icon": "calendar", "fields": [
            {"key": "run_at", "label": "When", "kind": "datetime", "required": True, "default": None},
            {"key": "repeat", "label": "Repeat", "kind": "select", "required": False, "default": "once",
             "options": [_opt(r, REPEAT_LABELS.get(r, r)) for r in postmail.VALID_REPEATS]}]},
        {"type": "worker_done", "label": "Worker event", "group": "Agents", "icon": "robot", "fields": [
            {"key": "worker_id", "label": "Worker", "kind": "worker", "required": True, "default": None},
            {"key": "when", "label": "When", "kind": "select", "required": False, "default": "completed",
             "options": [_opt("completed", "Finishes"), _opt("failed", "Fails"), _opt("started", "Starts")]}]},
        {"type": "chat_idle", "label": "Chat goes idle", "group": "Agents", "icon": "chat-circle", "fields": [
            {"key": "chat_id", "label": "Chat", "kind": "chat", "required": True, "default": None}]},
        {"type": "ledger_message", "label": "Track message", "group": "Agents", "icon": "megaphone", "fields": [
            {"key": "track", "label": "Track", "kind": "text", "required": False, "default": "main"},
            {"key": "pattern", "label": "Message contains", "kind": "text", "required": True, "default": None, "hint": "Text to match in the broadcast"}]},
        {"type": "file_change", "label": "File changes", "group": "Files", "icon": "file", "fields": [
            {"key": "paths", "label": "Files", "kind": "text_list", "required": True, "default": None, "hint": "One path per line"}]},
        {"type": "usage_rise", "label": "Usage rises", "group": "Usage", "icon": "trend-up", "fields": [
            {"key": "provider", "label": "Provider", "kind": "select", "required": True, "default": first, "options": prov_opts},
            {"key": "window", "label": "Window", "kind": "select", "required": False, "default": "session",
             "options": [_opt("session", "Session (5-hour)"), _opt("weekly", "Weekly"), _opt("daily", "Daily")]},
            *pool_fields,
            {"key": "delta_pct", "label": "Rise by %", "kind": "number", "required": False, "default": 1}]},
        {"type": "token_warning", "label": "Near a limit", "group": "Usage", "icon": "lightning", "fields": [
            {"key": "scope", "label": "Provider", "kind": "select", "required": True, "default": first, "options": prov_opts},
            {"key": "threshold_pct", "label": "At % used", "kind": "number", "required": False, "default": 80},
            {"key": "action_on_warning", "label": "Then", "kind": "select", "required": False, "default": "wait_for_reset",
             "options": ACTION_OPTIONS}]},
        {"type": "allowance_return", "label": "Allowance returns", "group": "Usage", "icon": "gauge", "fields": [
            {"key": "provider", "label": "Provider", "kind": "select", "required": True, "default": first, "options": prov_opts},
            {"key": "window", "label": "Window", "kind": "select", "required": False, "default": "any",
             "options": [_opt("any", "Any"), _opt("session", "Session"), _opt("weekly", "Weekly"), _opt("daily", "Daily")]},
            {"key": "min_left_pct", "label": "At least % left", "kind": "number", "required": False, "default": 20}]},
        {"type": "github", "label": "GitHub", "group": "GitHub", "icon": "github-logo", "available": gh_ok, "reason": gh_reason, "fields": [
            {"key": "event", "label": "Event", "kind": "select", "required": True, "default": "push",
             "options": [_opt(e, GITHUB_LABELS.get(e, e)) for e in github_watch.EVENTS]},
            {"key": "repo", "label": "Repo", "kind": "text", "required": True, "default": repo or None, "hint": "owner/name"},
            {"key": "branch", "label": "Branch", "kind": "text", "required": False, "default": None, "show_if": {"event": ["push"]}},
            {"key": "number", "label": "Issue or PR #", "kind": "number", "required": True, "default": None, "show_if": {"event": ["comment", "review"]}},
            {"key": "number", "label": "PR # (newest open if empty)", "kind": "number", "required": False, "default": None, "show_if": {"event": ["ci"]}},
            {"key": "conclusion", "label": "Result", "kind": "select", "required": False, "default": "any", "show_if": {"event": ["ci"]},
             "options": [_opt("any", "Any"), _opt("success", "Passed"), _opt("failure", "Failed")]},
            {"key": "state", "label": "Review", "kind": "select", "required": False, "default": "any", "show_if": {"event": ["review"]},
             "options": [_opt("any", "Any"), _opt("approved", "Approved"), _opt("changes_requested", "Changes requested"), _opt("commented", "Commented")]},
            {"key": "interval_s", "label": "Check every (s)", "kind": "number", "required": False, "default": 60}]},
        {"type": "reminder_due", "label": "Reminder due", "group": "Apps", "icon": "bell", "available": rem_ok, "reason": rem_reason, "fields": [
            {"key": "list_name", "label": "List", "kind": "text", "required": False, "default": None, "hint": "Optional Reminders list"},
            {"key": "title_contains", "label": "Title contains", "kind": "text", "required": False, "default": None, "hint": "Optional text match"},
            {"key": "interval_s", "label": "Check every (s)", "kind": "number", "required": False, "default": 60}]},
        {"type": "calendar_event_starts", "label": "Calendar event starts", "group": "Apps", "icon": "calendar", "available": cal_ok, "reason": cal_reason, "fields": [
            {"key": "calendar_name", "label": "Calendar", "kind": "text", "required": False, "default": None, "hint": "Optional Calendar name"},
            {"key": "title_contains", "label": "Title contains", "kind": "text", "required": False, "default": None, "hint": "Optional text match"},
            {"key": "lead_minutes", "label": "Minutes before", "kind": "number", "required": False, "default": 5},
            {"key": "interval_s", "label": "Check every (s)", "kind": "number", "required": False, "default": 60}]},
    ]
    for t in cat:
        t.setdefault("available", True)
        t.setdefault("reason", "")
    known = set(postmail.VALID_CONDITION_TYPES)
    return [t for t in cat if t["type"] in known]


# FILL IN: every value postmail's token_warning evaluator (~L744, `action = cond.get("action_on_warning", ...)`) handles.
ACTION_OPTIONS = [
    _opt("wait_for_reset", "Wait for the reset"),
    _opt("run_now", "Run now"),
]
