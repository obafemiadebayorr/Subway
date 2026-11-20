from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import re
import secrets
import time
import uuid
import win_lock
from typing import Any, Callable, Dict, List, Optional, Tuple

import server as _s

VALID_CONDITION_TYPES = (
    "time",
    "worker_done",
    "ledger_message",
    "file_change",
    "token_warning",
    "allowance_return",
    "chat_idle",
    "usage_rise",
    "github",
    "reminder_due",
    "calendar_event_starts",
)
VALID_PERMISSION_MODES = ("", "ask", "auto_safe", "auto_approve")
VALID_REPEATS = ("once", "daily", "weekdays", "weekly")
ACTIVE_STATES = ("queued", "waiting", "running")
TERMINAL_STATES = ("done", "dropped", "cancelled", "failed")


def _mail_dir() -> str:
    path = os.path.join(_s.SUBWAY_DIR, "mail")
    os.makedirs(path, exist_ok=True)
    os.makedirs(os.path.join(path, "events"), exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)
    return path


def _items_path() -> str:
    return os.path.join(_mail_dir(), "items.json")


def _event_log_path(mail_id: str) -> str:
    return os.path.join(_mail_dir(), "events", f"{mail_id}.jsonl")


def _load_mail_store() -> Dict[str, Any]:
    path = _items_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "items" in data and isinstance(data["items"], dict):
                return data
        except Exception as e:
            _s.log(f"could not read items.json: {e}")
    return {"version": 1, "items": {}}


def _save_mail_store(data: Dict[str, Any]) -> None:
    _mail_dir()
    _s._write_json_atomic(_items_path(), data)


def _append_mail_event(mail_id: str, event: Dict[str, Any]) -> None:
    _mail_dir()
    path = _event_log_path(mail_id)
    line = json.dumps(event) + "\n"
    payload = line.encode("utf-8")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            with contextlib.suppress(OSError):
                win_lock.lock(fd)
            os.write(fd, payload)
        finally:
            with contextlib.suppress(OSError):
                win_lock.unlock(fd)
            os.close(fd)
    except OSError as e:
        _s.log(f"failed to append mail event for {mail_id}: {e}")
    try:
        import sse_stream
        sse_stream.publish("mail", {"id": mail_id, "event": event.get("event")})
    except Exception:
        pass



def _to_epoch(ts: Any) -> float:
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str) and ts:
        try:
            d = _dt.datetime.fromisoformat(ts)
            if d.tzinfo is None:
                d = d.replace(tzinfo=_dt.timezone.utc)
            return d.timestamp()
        except Exception:
            pass
    return 0.0


def _capture_baseline(condition: Dict[str, Any]) -> Dict[str, Any]:
    ctype = condition.get("type")
    cond = dict(condition)
    if ctype == "file_change":
        paths = cond.get("paths") or []
        sigs = {}
        for p in paths:
            exists, size, mtime_ns = _s._file_activity_signature(p)
            sha = ""
            if exists:
                with contextlib.suppress(OSError):
                    sha = _s._file_sha256(p)
            sigs[p] = {"exists": exists, "size": size, "mtime_ns": mtime_ns, "sha256": sha}
        cond["baseline_sigs"] = sigs
    elif ctype == "ledger_message":
        if "since_ts" not in cond or cond["since_ts"] is None:
            cond["since_ts"] = time.time()
    elif ctype == "time":
        if "run_at" not in cond and "delay_seconds" in cond:
            with contextlib.suppress(Exception):
                cond["run_at"] = time.time() + float(cond["delay_seconds"])
        elif "run_at" in cond:
            cond["run_at"] = _to_epoch(cond["run_at"])
    return cond


def _normalise_deliver(deliver: Any) -> Tuple[bool, Any]:
    """Validate an optional target dict. Only chat delivery is supported server-side."""
    if deliver is None or deliver == "":
        return True, None
    if not isinstance(deliver, dict):
        return False, "deliver must be a dictionary"
    kind = str(deliver.get("kind") or "chat").strip().lower()
    if kind != "chat":
        return False, f"unknown deliver kind '{kind}'"
    chat_id = str(deliver.get("chat_id") or "").strip()
    if not chat_id:
        return False, "deliver kind 'chat' requires 'chat_id'"
    norm: Dict[str, Any] = {
        "kind": "chat",
        "chat_id": chat_id,
        "source": str(deliver.get("source") or "").strip().lower(),
        "model": str(deliver.get("model") or "").strip(),
        "effort": str(deliver.get("effort") or "").strip(),
        "attachments": list(deliver.get("attachments") or []),
    }
    return True, norm


_WINDOW_ALIASES = {"session": ("session", "5h", "5-hour"), "5h": ("5h", "5-hour", "session"),
                   "weekly": ("weekly", "7d", "week"), "7d": ("7d", "weekly", "week"), "daily": ("daily", "day", "24h")}


def _usage_window(provider: str, window: str, pool: str = "") -> Optional[Dict[str, Any]]:
    """The provider's quota window matching `window` by key or label (e.g. 'session', 'weekly'), optionally in one
    pool (key or name, e.g. 'gemini_models' / 'Gemini' for Antigravity's Gemini quota)."""
    want = str(window or "session").strip().lower()
    names = _WINDOW_ALIASES.get(want, (want,))
    pool_want = str(pool or "").strip().lower()
    try:
        providers = _s.discover_connected_providers(refresh=False) or []
    except Exception:
        return None
    record = next((p for p in providers if p.get("id") == provider), None)
    if not record:
        return None
    for pl in record.get("pools") or []:
        if pool_want and pool_want != str(pl.get("key") or "").lower() and pool_want not in str(pl.get("name") or "").lower():
            continue
        for w in pl.get("windows") or []:
            if w.get("disabled") or w.get("left_pct") is None:
                continue
            key = str(w.get("key") or "").lower()
            label = str(w.get("label") or "").lower()
            if any(n == key or n in label for n in names):
                return w
    return None


def _usage_used_pct(provider: str, window: str, pool: str = "") -> Optional[float]:
    w = _usage_window(provider, window, pool)
    if not w:
        return None
    try:
        return round(100.0 - float(w.get("left_pct")), 2)
    except Exception:
        return None


def _validate_condition(ctype: str, cond: Dict[str, Any]) -> Optional[str]:
    """Field rules shared by create and edit. Returns an error string or None."""
    if ctype == "time":
        if "run_at" not in cond and "delay_seconds" not in cond:
            return "time condition requires 'run_at' or 'delay_seconds'"
        repeat = str(cond.get("repeat") or "once").strip().lower()
        if repeat not in VALID_REPEATS:
            return f"invalid repeat '{repeat}'"
    elif ctype == "chat_idle":
        if not str(cond.get("chat_id") or "").strip():
            return "chat_idle condition requires 'chat_id'"
    elif ctype == "github":
        ev = str(cond.get("event") or "").strip()
        try:
            from hud_server import github_watch
            if ev not in github_watch.EVENTS:
                return f"github condition event must be one of {github_watch.EVENTS}"
        except Exception:
            if ev not in ("push","pr_opened","pr_merged","ci","issue","comment"):
                return "github condition event must be one of ('push','pr_opened','pr_merged','ci','issue','comment')"
    elif ctype in ("reminder_due", "calendar_event_starts"):
        import sys
        from hud_server import app_connectors
        if sys.platform != "darwin":
            return f"unsupported: {app_connectors._UNSUPPORTED_REASON}"
        if "interval_s" in cond and cond["interval_s"] is not None:
            try:
                if float(cond["interval_s"]) <= 0:
                    return f"{ctype} 'interval_s' must be positive"
            except Exception:
                return f"{ctype} 'interval_s' must be a number"
        if ctype == "calendar_event_starts" and "lead_minutes" in cond and cond["lead_minutes"] is not None:
            try:
                if float(cond["lead_minutes"]) < 0:
                    return "calendar_event_starts 'lead_minutes' must be >= 0"
            except Exception:
                return "calendar_event_starts 'lead_minutes' must be a number"
    return None


def _next_repeat_run_at(run_at: float, repeat: str) -> float:
    """Advance a run_at (seconds) like composer.js nextRepeat (local wall clock, ms epoch math)."""
    step = 7 if repeat == "weekly" else 1
    try:
        d = _dt.datetime.fromtimestamp(float(run_at))
    except (OverflowError, OSError, ValueError):
        return float(run_at) + step * 86400.0
    d = d + _dt.timedelta(days=step)
    if repeat == "weekdays":
        while d.weekday() >= 5:
            d = d + _dt.timedelta(days=1)
    with contextlib.suppress(OverflowError, OSError, ValueError):
        return d.timestamp()
    return float(run_at) + step * 86400.0


def create_mail_item(
    role: str,
    prompt: str,
    condition_type: str,
    condition: Dict[str, Any],
    provider: str = "auto",
    model: str = "",
    effort: str = "auto",
    track: str = "main",
    project_dir: str = "",
    isolate: bool = True,
    private: bool = False,
    mode: str = "standard",
    permission_mode: str = "",
    capabilities: Optional[List[str]] = None,
    max_tokens: int = 0,
    max_seconds: int = 0,
    budget_session_pct: Optional[float] = None,
    budget_weekly_pct: Optional[float] = None,
    max_budget_usd: Optional[float] = None,
    review: str = "",
    then: Any = None,
    created_by: Optional[Dict[str, Any]] = None,
    deliver: Optional[Dict[str, Any]] = None,
    position: Optional[float] = None,
    now: Optional[float] = None,
) -> Tuple[bool, Any]:
    ok_deliver, deliver_norm = _normalise_deliver(deliver)
    if not ok_deliver:
        return False, deliver_norm
    if deliver_norm and not str(role or "").strip():
        role = "Chat message"
    if not role or not str(role).strip():
        return False, "role is required"
    if not prompt or not str(prompt).strip():
        return False, "prompt is required"
    if condition_type not in VALID_CONDITION_TYPES:
        return False, f"invalid condition_type '{condition_type}'"
    if not isinstance(condition, dict):
        return False, "condition must be a dictionary"

    cond = dict(condition)
    cond["type"] = condition_type

    # Validate condition type required fields
    if condition_type == "time":
        if "run_at" not in cond and "delay_seconds" not in cond:
            return False, "time condition requires 'run_at' or 'delay_seconds'"
        if "run_at" in cond and isinstance(cond["run_at"], str):
            cond["run_at"] = _to_epoch(cond["run_at"])
        cond["repeat"] = str(cond.get("repeat") or "once").strip().lower()
    elif condition_type == "chat_idle":
        chat_id = str(cond.get("chat_id") or "").strip()
        if not chat_id:
            return False, "chat_idle condition requires 'chat_id'"
        cond["chat_id"] = chat_id
        cond["source"] = str(cond.get("source") or "").strip().lower()
    elif condition_type == "worker_done":
        if not cond.get("worker_id"):
            return False, "worker_done condition requires 'worker_id'"
        cond.setdefault("when", "completed")
        cond.setdefault("include", "none")
    elif condition_type == "ledger_message":
        if not cond.get("pattern"):
            return False, "ledger_message condition requires 'pattern'"
        cond.setdefault("track", track or "main")
    elif condition_type == "file_change":
        paths = cond.get("paths")
        if not paths or not isinstance(paths, list):
            return False, "file_change condition requires non-empty 'paths' list"
    elif condition_type == "token_warning":
        if not cond.get("scope"):
            return False, "token_warning condition requires 'scope'"
        cond.setdefault("threshold_pct", 80.0)
        cond.setdefault("action_on_warning", "wait_for_reset")
        cond.setdefault("drop_on_handoff", True)
    elif condition_type == "allowance_return":
        if not cond.get("provider"):
            return False, "allowance_return condition requires 'provider'"
        cond.setdefault("window", "any")
        cond.setdefault("min_left_pct", 20.0)
        cond.setdefault("drop_on_handoff", True)

    elif condition_type == "usage_rise":
        if not cond.get("provider"):
            return False, "usage_rise condition requires 'provider'"
        cond.setdefault("window", "session")
        try:
            cond["delta_pct"] = float(cond.get("delta_pct") or 1.0)
        except Exception:
            return False, "usage_rise 'delta_pct' must be a number"
        if cond.get("baseline_used_pct") is None:
            used = _usage_used_pct(cond["provider"], cond["window"], cond.get("pool", ""))
            if used is None:
                return False, f"usage_rise: no live {cond['window']} usage reported for '{cond['provider']}'"
            cond["baseline_used_pct"] = used
    elif condition_type == "github":
        from hud_server import github_watch
        ev = str(cond.get("event") or "").strip()
        if ev not in github_watch.EVENTS:
            return False, f"github condition event must be one of {github_watch.EVENTS}"
        if not str(cond.get("repo") or "").strip():
            try:
                cond["repo"] = github_watch.default_repo(project_dir or os.getcwd())
            except Exception:
                cond["repo"] = github_watch.default_repo(project_dir or os.getcwd())
        if not str(cond.get("repo") or "").strip():
            return False, "github condition requires 'repo' (owner/name)"
        try:
            cond["interval_s"] = max(30, int(float(cond.get("interval_s") or 60)))
        except Exception:
            cond["interval_s"] = 60
        if "baseline" not in cond or cond.get("baseline") is None:
            try:
                cond["baseline"] = github_watch.snapshot(cond)
            except github_watch.GhMissing as e:
                return False, str(e)
            except Exception as e:
                return False, f"github baseline failed: {e}"
    elif condition_type in ("reminder_due", "calendar_event_starts"):
        import sys
        from hud_server import app_connectors
        if sys.platform != "darwin":
            return False, f"unsupported: {app_connectors._UNSUPPORTED_REASON}"
        if cond.get("list_name") is not None:
            cond["list_name"] = str(cond.get("list_name") or "").strip()
        if cond.get("calendar_name") is not None:
            cond["calendar_name"] = str(cond.get("calendar_name") or "").strip()
        if cond.get("title_contains") is not None:
            cond["title_contains"] = str(cond.get("title_contains") or "").strip()
        try:
            cond["interval_s"] = max(15, int(float(cond.get("interval_s") or 60)))
        except Exception:
            cond["interval_s"] = 60
        if condition_type == "calendar_event_starts":
            try:
                lm = cond.get("lead_minutes")
                cond["lead_minutes"] = max(0.0, float(5.0 if lm is None else lm))
            except Exception:
                cond["lead_minutes"] = 5.0

    if permission_mode not in VALID_PERMISSION_MODES:
        return False, f"unknown permission_mode '{permission_mode}'"

    t_now = now if now is not None else time.time()
    mail_id = f"mail_{uuid.uuid4().hex[:8]}"

    cond = _capture_baseline(cond)

    caller_id = _s._self_id() if hasattr(_s, "_self_id") else ""
    default_created_by = {
        "station_id": caller_id or "unknown",
        "thread_id": (_s._thread_root_for_caller(caller_id) if hasattr(_s, "_thread_root_for_caller") else caller_id) or "main",
        "launcher": caller_id or "unknown",
        "surface": "mcp",
    }
    if created_by:
        default_created_by.update(created_by)

    item: Dict[str, Any] = {
        "id": mail_id,
        "version": 1,
        "state": "queued",
        "created_at": t_now,
        "updated_at": t_now,
        "next_check_at": None,
        "last_checked_at": None,
        "waiting_reason": "Queued in mailbox",
        "created_by": default_created_by,
        "task": {
            "role": str(role).strip(),
            "prompt": str(prompt).strip(),
            "provider": provider or "auto",
            "model": model or "",
            "effort": effort or "auto",
            "track": track or "main",
            "project_dir": project_dir or "",
            "isolate": bool(isolate),
            "private": bool(private),
            "mode": mode or "standard",
            "permission_mode": permission_mode or "",
            "capabilities": list(capabilities) if capabilities else [],
            "max_tokens": int(max_tokens or 0),
            "max_seconds": int(max_seconds or 0),
            "budget_session_pct": float(budget_session_pct) if budget_session_pct is not None else None,
            "budget_weekly_pct": float(budget_weekly_pct) if budget_weekly_pct is not None else None,
            "max_budget_usd": float(max_budget_usd) if max_budget_usd is not None else None,
            "review": review or "",
            "then": then if isinstance(then, list) else ([] if not then else [then]),
        },
        "condition": cond,
        "deliver": deliver_norm,
        "position": float(position) if position is not None else None,
        "repeat_series_id": mail_id,
        "claim_nonce": None,
        "spawned_worker_id": None,
        "terminal_reason": None,
        "completed_at": None,
    }

    with _s._state_lock("mail"):
        store = _load_mail_store()
        if item["position"] is None:
            existing_pos = [float(it.get("position") or 0.0) for it in store.get("items", {}).values()]
            item["position"] = (max(existing_pos) + 1.0) if existing_pos else 0.0
        store["items"][mail_id] = item
        _save_mail_store(store)
        _append_mail_event(mail_id, {"at": t_now, "event": "created", "state": "queued"})

    return True, item


def edit_mail_item(mail_id: str, updates: Dict[str, Any], now: Optional[float] = None) -> Tuple[bool, Any]:
    if not mail_id:
        return False, "mail_id is required"
    t_now = now if now is not None else time.time()
    with _s._state_lock("mail"):
        store = _load_mail_store()
        items = store.get("items", {})
        if mail_id not in items:
            return False, f"mail item '{mail_id}' not found"
        item = items[mail_id]
        if item["state"] in TERMINAL_STATES or item["state"] == "running":
            return False, f"cannot edit item in state '{item['state']}'"

        task = item.get("task", {})
        if "text" in updates and updates.get("prompt") is None:
            updates = dict(updates)
            updates["prompt"] = updates["text"]
        for field in ("role", "prompt", "provider", "model", "track", "project_dir", "effort", "isolate", "private", "permission_mode", "max_tokens", "max_seconds"):
            if field in updates and updates[field] is not None:
                if field == "permission_mode" and updates[field] not in VALID_PERMISSION_MODES:
                    return False, f"unknown permission_mode '{updates[field]}'"
                task[field] = updates[field]
        item["task"] = task

        if "position" in updates and updates["position"] is not None:
            with contextlib.suppress(TypeError, ValueError):
                item["position"] = float(updates["position"])

        if "deliver" in updates:
            ok_deliver, deliver_norm = _normalise_deliver(updates.get("deliver"))
            if not ok_deliver:
                return False, deliver_norm
            item["deliver"] = deliver_norm

        if "condition_type" in updates or "condition" in updates:
            ctype = updates.get("condition_type", item.get("condition", {}).get("type"))
            if ctype not in VALID_CONDITION_TYPES:
                return False, f"invalid condition_type '{ctype}'"
            cond = dict(updates.get("condition") or item.get("condition", {}))
            cond["type"] = ctype
            if ctype == "github":
                from hud_server import github_watch
                ev = str(cond.get("event") or "").strip()
                if ev not in github_watch.EVENTS:
                    return False, f"github condition event must be one of {github_watch.EVENTS}"
                if not str(cond.get("repo") or "").strip():
                    try:
                        cond["repo"] = github_watch.default_repo(item.get("task", {}).get("project_dir") or os.getcwd())
                    except Exception:
                        cond["repo"] = ""
                if not str(cond.get("repo") or "").strip():
                    return False, "github condition requires 'repo' (owner/name)"
                try:
                    cond["interval_s"] = max(30, int(float(cond.get("interval_s") or item.get("condition", {}).get("interval_s") or 60)))
                except Exception:
                    cond["interval_s"] = 60
                if "baseline" not in cond or cond.get("baseline") is None:
                    try:
                        cond["baseline"] = github_watch.snapshot(cond)
                    except github_watch.GhMissing as e:
                        return False, str(e)
                    except Exception as e:
                        return False, f"github baseline failed: {e}"
            err = _validate_condition(ctype, cond)
            if err:
                return False, err
            cond = _capture_baseline(cond)
            item["condition"] = cond

        item["updated_at"] = t_now
        _save_mail_store(store)
        _append_mail_event(mail_id, {"at": t_now, "event": "edited", "updates": updates})
        return True, item


def cancel_mail_item(mail_id: str, reason: str = "Cancelled by user/agent", now: Optional[float] = None) -> Tuple[bool, Any]:
    if not mail_id:
        return False, "missing mail_id"
    t_now = now if now is not None else time.time()
    with _s._state_lock("mail"):
        store = _load_mail_store()
        items = store.get("items", {})
        if mail_id not in items:
            return False, f"mail item '{mail_id}' not found"
        item = items[mail_id]
        if item["state"] in ("done", "dropped"):
            return False, f"cannot cancel item in terminal state '{item['state']}'"
        item["state"] = "cancelled"
        item["terminal_reason"] = reason or "Cancelled"
        item["waiting_reason"] = f"Cancelled: {item['terminal_reason']}"
        item["completed_at"] = t_now
        item["updated_at"] = t_now
        _save_mail_store(store)
        _append_mail_event(mail_id, {"at": t_now, "event": "cancelled", "reason": reason})
        return True, item


def list_mail_items(state: str = "all", track: str = "", limit: int = 100) -> Dict[str, Any]:
    with _s._state_lock("mail"):
        store = _load_mail_store()
    items_map = store.get("items", {})
    counts = {"queued": 0, "waiting": 0, "running": 0, "done": 0, "dropped": 0, "cancelled": 0, "failed": 0}
    for it in items_map.values():
        st = it.get("state")
        if st in counts:
            counts[st] += 1

    matched: List[Dict[str, Any]] = []
    for it in items_map.values():
        st = it.get("state")
        t = it.get("task", {}).get("track") or "main"
        if track and t != track:
            continue
        if state == "active":
            if st not in ACTIVE_STATES:
                continue
        elif state != "all" and state:
            if st != state:
                continue
        matched.append(it)

    state_order = {"running": 0, "waiting": 1, "queued": 2, "done": 3, "dropped": 4, "cancelled": 5, "failed": 6}
    matched.sort(key=lambda x: (state_order.get(x.get("state", ""), 9), -float(x.get("created_at", 0))))
    return {
        "items": matched[:limit],
        "counts": counts,
        "updated_at": time.time(),
    }


def _check_handoff_supersession(item: Dict[str, Any], now: float) -> Optional[Tuple[str, str]]:
    cond = item.get("condition", {})
    if cond.get("type") not in ("token_warning", "allowance_return"):
        return None
    if not cond.get("drop_on_handoff", True):
        return None

    origin_thread = cond.get("origin_thread_id") or item.get("created_by", {}).get("thread_id")
    origin_worker = cond.get("origin_worker_id")
    created_at = item.get("created_at", 0.0)
    provider = cond.get("provider", "")

    # Signal 1: worker reroute / thread edges
    if origin_worker:
        w = _s._load_worker(origin_worker)
        if w and w.get("rerouted_to"):
            new_id = w["rerouted_to"]
            return new_id, f"worker reroute from {origin_worker}"
        exit_rec = _s._load_json(f"workers/{origin_worker}.exit.json") if hasattr(_s, "_load_json") else {}
        if exit_rec.get("rerouted_to"):
            return exit_rec["rerouted_to"], f"worker exit reroute from {origin_worker}"

    if origin_thread:
        try:
            import threads
            tdata = threads.read_thread(_s.SUBWAY_DIR, origin_thread)
            for r in tdata.get("records", []):
                if r.get("at", 0) >= created_at - 1.0:
                    kind = r.get("kind")
                    peer = r.get("peer")
                    data = r.get("data") or {}
                    if kind in ("rerouted", "spawned") and peer:
                        w = _s._load_worker(peer)
                        p_cand = (w.get("provider") if w else "") or data.get("provider") or ""
                        if p_cand and p_cand != provider:
                            return peer, f"thread {kind} to {p_cand}"
                        if peer != origin_worker and peer.startswith("worker_"):
                            return peer, f"thread {kind}"
        except Exception:
            pass

    # Signal 2: chat transit watermark checkpoints
    cps = _s._load_json("checkpoints.json")
    for key, hist in cps.items():
        if origin_thread and origin_thread.lower() in key.lower():
            for rec in hist:
                ts_val = rec.get("ts")
                ts = _to_epoch(ts_val)
                if ts >= created_at - 1.0:
                    for k in rec.keys():
                        if k != "ts" and k.lower() != origin_thread.lower():
                            cand = k
                            cand_source = ""
                            try:
                                cand_source = _s.resolve(cand, strict=False).get("source") or ""
                            except Exception:
                                pass
                            if not cand_source:
                                clow = cand.lower()
                                if "agy" in clow or "anti" in clow:
                                    cand_source = "antigravity"
                                elif "opencode" in clow:
                                    cand_source = "opencode"
                                elif "claude" in clow:
                                    cand_source = "claude"
                                elif "copilot" in clow:
                                    cand_source = "copilot"
                            if cand_source and cand_source != provider:
                                return cand, f"chat transit ({cand_source})"

    # Signal 3: inbox notes
    inbox_dir = os.path.join(_s.SUBWAY_DIR, "inbox")
    if os.path.isdir(inbox_dir):
        for fn in os.listdir(inbox_dir):
            if fn.endswith(".jsonl"):
                target = fn[:-6]
                p_cand = "antigravity" if "agy" in target else "claude" if "claude" in target else "opencode" if "opencode" in target else ""
                if p_cand and p_cand != provider:
                    fp = os.path.join(inbox_dir, fn)
                    with contextlib.suppress(OSError):
                        with open(fp, "r", encoding="utf-8") as f:
                            for line in f:
                                try:
                                    nrec = json.loads(line)
                                    ts = _to_epoch(nrec.get("ts") or nrec.get("timestamp"))
                                    if ts >= created_at - 1.0 and (nrec.get("from") == origin_thread or (origin_worker and origin_worker in nrec.get("text", ""))):
                                        return target, "inbox handoff note"
                                except Exception:
                                    pass

    # Signal 4: track reroute entries
    track = item.get("task", {}).get("track") or "main"
    entries = _s.read_track_entries(track, limit=50, since_timestamp=created_at - 1.0)
    for entry in entries:
        msg = entry.get("message", "")
        if origin_worker and origin_worker in msg and ("rerouted" in msg.lower() or "handed off" in msg.lower()):
            st = entry.get("station_id") or "peer"
            return st, "track reroute broadcast"

    return None


def _evaluate_condition(item: Dict[str, Any], now: float) -> Tuple[bool, str, Dict[str, Any]]:
    cond = item.get("condition", {})
    ctype = cond.get("type")

    if ctype == "time":
        run_at = cond.get("run_at")
        if run_at is None and "delay_seconds" in cond:
            run_at = item.get("created_at", now) + float(cond["delay_seconds"])
            cond["run_at"] = run_at
        run_at_f = float(run_at or 0)
        if now >= run_at_f:
            return True, f"Scheduled time reached ({_s.fmt_ts(run_at_f)})", {}
        return False, f"Scheduled for {_s.fmt_ts(run_at_f)}", {"next_check_at": run_at_f}

    elif ctype == "worker_done":
        wid = cond.get("worker_id")
        when = cond.get("when", "completed")
        w = _s._load_worker(wid) if wid else None
        if not w:
            return False, f"Waiting for worker {wid} (not found)", {"next_check_at": now + 5.0}
        status = w.get("status")
        pending_statuses = {"pending", "pending_approval", "queued"}
        if when == "started":
            if status not in pending_statuses:
                return True, f"Worker {wid} started", {"include": cond.get("include", "none"), "upstream_worker": w}
            return False, f"Waiting for {wid} to start (currently {status})", {"next_check_at": now + 2.0}
        if status in _s.ACTIVE_WORKER_STATES or status == "launching":
            return False, f"Waiting for {wid} to finish (currently {status})", {"next_check_at": now + 2.0}

        verdict = w.get("verdict")
        matched = False
        if when == "always":
            matched = True
        elif when == "completed" and status == "completed":
            matched = True
        elif when == "failed" and status in ("failed", "stopped", "budget_stopped", "timed_out"):
            matched = True
        elif when == "approved" and verdict == "approve":
            matched = True
        elif when == "rejected" and (verdict == "reject" or status == "rejected"):
            matched = True

        if matched:
            return True, f"Worker {wid} finished with status '{status}'", {"include": cond.get("include", "none"), "upstream_worker": w}
        else:
            return False, f"DROPPED: Worker {wid} finished as {status} (wanted {when})", {"drop": True, "reason": f"Worker {wid} finished as {status} (wanted {when})"}

    elif ctype == "ledger_message":
        trk = cond.get("track") or "main"
        pat = cond.get("pattern", "")
        since = float(cond.get("since_ts") or item.get("created_at") or 0.0)
        entries = _s.read_track_entries(trk, limit=100, since_timestamp=since)
        for e in entries:
            e.get("id", "")
            msg = e.get("message", "")
            decs = " ".join(e.get("decisions") or [])
            combined = f"{msg} {decs}"
            if msg.startswith("[MAIL "):
                continue
            if cond.get("station_id") and e.get("station_id") != cond["station_id"]:
                continue
            if cond.get("files_touch"):
                e_files = set(e.get("files") or [])
                if not e_files.intersection(set(cond["files_touch"])):
                    continue
            if pat in combined or re.search(pat, combined, re.IGNORECASE):
                return True, f"Matched message on #{trk}: {msg[:60]}", {}
        return False, f"Waiting for message on #{trk} matching '{pat}'", {"next_check_at": now + 2.0}

    elif ctype == "file_change":
        paths = cond.get("paths") or []
        sigs = cond.get("baseline_sigs") or {}
        for p in paths:
            exists, size, mtime_ns = _s._file_activity_signature(p)
            base = sigs.get(p)
            if not base:
                continue
            if exists != base.get("exists") or size != base.get("size") or mtime_ns != base.get("mtime_ns"):
                curr_sha = _s._file_sha256(p) if exists else ""
                if curr_sha != base.get("sha256") or exists != base.get("exists"):
                    return True, f"File change detected on {p}", {}
        return False, f"Watching {len(paths)} path(s) for changes", {"next_check_at": now + 5.0}

    elif ctype == "token_warning":
        scope = cond.get("scope")
        thresh = float(cond.get("threshold_pct") or 80.0)
        action = cond.get("action_on_warning", "wait_for_reset")
        states = _s.budget_state() if hasattr(_s, "budget_state") else []
        tripped = False
        for bs in states:
            if bs.get("scope") == scope and (bs.get("pct", 0) >= thresh or bs.get("status") in ("warning", "reached")):
                tripped = True
                break
        if not tripped:
            quotas = _s.discover_connected_providers(refresh=False)
            for p in quotas:
                if p.get("id") == scope:
                    for pool in p.get("pools", []):
                        for win in pool.get("windows", []):
                            lp = win.get("left_pct")
                            if lp is not None and lp <= 20.0:
                                tripped = True
                                break
        if tripped and action == "run_now":
            return True, f"Token warning triggered on scope '{scope}'", {}
        elif action == "wait_for_reset":
            # Delegate to allowance_return logic
            cond_allow = dict(cond)
            cond_allow["provider"] = scope
            cond_allow["type"] = "allowance_return"
            fake_item = dict(item)
            fake_item["condition"] = cond_allow
            return _evaluate_condition(fake_item, now)
        return False, f"Waiting for token warning on '{scope}' (threshold {thresh}%)", {"next_check_at": now + 10.0}

    elif ctype == "allowance_return":
        prov = cond.get("provider")
        min_left = float(cond.get("min_left_pct") or 20.0)
        base_reset = cond.get("baseline_resets_at")
        win_key = cond.get("window", "any")
        max_wait = float(cond.get("max_wait_seconds") or (14 * 86400))
        if now - item.get("created_at", now) > max_wait:
            return False, f"DROPPED: allowance wait expired after {max_wait}s", {"drop": True, "reason": f"Allowance wait expired after {max_wait}s"}

        providers = _s.discover_connected_providers(refresh=False)
        record = next((p for p in providers if p["id"] == prov), None)
        if not record:
            if base_reset and now >= float(base_reset):
                return True, f"Provider {prov} reached scheduled reset at {_s.fmt_ts(base_reset)}", {}
            return False, f"Provider {prov} not detected; waiting for reset timestamp", {"next_check_at": now + 30.0}

        pool = _s._select_pool(record, cond.get("model", "")) if hasattr(_s, "_select_pool") else None
        if not pool and record.get("pools"):
            pool = record["pools"][0]

        if not pool or not pool.get("windows"):
            if base_reset and now >= float(base_reset):
                return True, f"Silent provider reset time reached ({_s.fmt_ts(base_reset)})", {}
            return False, "Provider does not report live quota; waiting for manual reset or timestamp", {"next_check_at": now + 30.0}

        windows = [w for w in pool.get("windows", []) if not w.get("disabled")]
        if win_key != "any":
            windows = [w for w in windows if w.get("key") == win_key]

        all_ok = True
        waiting_reason = ""
        resets_at_earliest = None
        for w in windows:
            lp = w.get("left_pct")
            r_at = w.get("resets_at")
            if r_at and (resets_at_earliest is None or r_at < resets_at_earliest):
                resets_at_earliest = r_at

            reset_cleared = (base_reset is None or now >= float(base_reset) or (r_at and r_at != base_reset))
            if lp is not None and float(lp) > min_left and reset_cleared:
                continue
            else:
                all_ok = False
                waiting_reason = f"Waiting for {prov} ({w.get('label') or w.get('key')}) reset at {_s.fmt_ts(r_at)} ({lp}% left)"
                break

        if all_ok and windows:
            return True, f"Allowance returned for {prov} (>{min_left}% left)", {}
        return False, waiting_reason or f"Waiting for {prov} allowance to return", {"next_check_at": resets_at_earliest or (now + 30.0)}

    elif ctype == "chat_idle":
        chat_id = str(cond.get("chat_id") or "").strip()
        source = str(cond.get("source") or "").strip().lower()
        if not chat_id:
            return False, "DROPPED: chat_idle condition has no chat_id", {"drop": True, "reason": "chat_idle condition has no chat_id"}
        busy_reason = ""
        try:
            from cli_tools import chats as _chats
            live = _chats.chat_live(chat_id, source=source) if source else _chats.chat_live(chat_id)
            working = bool((live.get("live") or {}).get("working"))
        except Exception:
            working = True  # errors are treated as busy, never as idle
        if working:
            busy_reason = f"Chat {chat_id} is working"
        else:
            try:
                import provider_lines
                headless = bool(provider_lines.claude_headless_running(chat_id))
            except Exception:
                headless = True
            if headless:
                busy_reason = f"Chat {chat_id} has a headless run in progress"
        if busy_reason:
            return False, busy_reason, {"next_check_at": now + 5.0}
        return True, f"Chat {chat_id} is idle", {}

    elif ctype == "usage_rise":
        prov = cond.get("provider")
        win = cond.get("window", "session")
        base = float(cond.get("baseline_used_pct") or 0.0)
        delta = float(cond.get("delta_pct") or 1.0)
        used = _usage_used_pct(prov, win, cond.get("pool", ""))
        if used is None:
            return False, f"Waiting for {prov} {win} usage to be reported", {}
        if used >= base + delta:
            return True, f"{prov} {win} usage rose to {used:g}% (from {base:g}%, +{delta:g}%)", {}
        return False, f"{prov} {win} usage {used:g}%, fires at {base + delta:g}%", {}

    elif ctype == "github":
        from hud_server import github_watch
        interval = max(30.0, float(cond.get("interval_s") or 60))
        try:
            fired, reason, _detail = github_watch.check(cond)
        except Exception as e:
            return False, f"GitHub check failed: {e}", {"next_check_at": now + interval}
        if fired:
            return True, reason, {}
        return False, reason, {"next_check_at": now + interval}

    elif ctype == "reminder_due":
        import sys
        from hud_server import app_connectors
        if sys.platform != "darwin":
            return False, f"unsupported: {app_connectors._UNSUPPORTED_REASON}", {"drop": True, "reason": "unsupported"}
        interval = max(15.0, float(cond.get("interval_s") or 60))
        try:
            fired, reason, _detail = app_connectors.check_reminder_due(cond, now=now)
        except Exception as e:
            return False, f"Reminders check failed: {e}", {"next_check_at": now + interval}
        if fired:
            return True, reason, {}
        return False, reason, {"next_check_at": now + interval}

    elif ctype == "calendar_event_starts":
        import sys
        from hud_server import app_connectors
        if sys.platform != "darwin":
            return False, f"unsupported: {app_connectors._UNSUPPORTED_REASON}", {"drop": True, "reason": "unsupported"}
        interval = max(15.0, float(cond.get("interval_s") or 60))
        try:
            fired, reason, _detail = app_connectors.check_calendar_event_starts(cond, now=now)
        except Exception as e:
            return False, f"Calendar check failed: {e}", {"next_check_at": now + interval}
        if fired:
            return True, reason, {}
        return False, reason, {"next_check_at": now + interval}

    return False, "Unknown condition type", {}


def _chat_queue_key(item: Dict[str, Any]) -> str:
    """The chat a mail item is queued against (chat_idle condition or chat delivery target)."""
    cond = item.get("condition") or {}
    if cond.get("type") == "chat_idle":
        return str(cond.get("chat_id") or "").strip()
    deliver = item.get("deliver") or {}
    if isinstance(deliver, dict) and deliver.get("kind") == "chat":
        return str(deliver.get("chat_id") or "").strip()
    return ""


ACK_TIMEOUT_SECONDS = 2 * 3600
ACK_MAX_ATTEMPTS = 3


def _ack_required(source: str) -> bool:
    """Every chat delivery waits for the chat's answer (read through chat_readers, or a mail_ack receipt)."""
    return True


def _target_provider(item: Dict[str, Any]) -> str:
    d = item.get("deliver") or {}
    if d.get("kind") == "chat":
        return str(d.get("provider") or d.get("source") or "").strip().lower()
    p = str((item.get("task") or {}).get("provider") or "").strip().lower()
    return "" if p in ("", "auto") else p


def _item_model(item: Dict[str, Any]) -> str:
    d = item.get("deliver") or {}
    return str(d.get("model") or (item.get("task") or {}).get("model") or "")


def _allowance_hold(provider: str, model: str = "") -> Optional[Dict[str, Any]]:
    """When the target provider reports a limit window with nothing left, what to wait for. None = free to send."""
    if not provider:
        return None
    try:
        providers = _s.discover_connected_providers(refresh=False) or []
    except Exception:
        return None
    rec = next((p for p in providers if p.get("id") == provider), None)
    if not rec:
        return None
    pool = None
    with contextlib.suppress(Exception):
        pool = _s._select_pool(rec, model) if hasattr(_s, "_select_pool") else None
    if not pool and rec.get("pools"):
        pool = rec["pools"][0]
    hit = []
    for w in (pool or {}).get("windows") or []:
        if w.get("disabled") or w.get("left_pct") is None:
            continue
        with contextlib.suppress(Exception):
            if float(w["left_pct"]) <= 0:
                hit.append(w)
    if not hit:
        return None
    resets = [float(w["resets_at"]) for w in hit if w.get("resets_at")]
    return {"provider": provider, "resets_at": max(resets) if resets else None,
            "label": ", ".join(str(w.get("label") or w.get("key") or "limit") for w in hit)}


def _hold_reason(hold: Dict[str, Any]) -> str:
    when = f" until {_s.fmt_ts(hold['resets_at'])}" if hold.get("resets_at") else " until it returns"
    return f"Condition met; holding: {hold['provider']} has no allowance left ({hold['label']}){when}"


def _check_replies(t_now: float) -> None:
    """Settle chat deliveries that are waiting for the chat to answer."""
    try:
        from hud_server import mail_ack
    except Exception:
        return
    with _s._state_lock("mail"):
        store = _load_mail_store()
        changed = False
        for mid, item in (store.get("items") or {}).items():
            a = item.get("awaiting_reply")
            if item.get("state") != "running" or not isinstance(a, dict):
                continue
            if t_now < float(a.get("next_check") or 0):
                continue
            verdict = mail_ack.reply_after(item)
            changed = True
            if verdict == "reply":
                item["state"] = "done"
                item["completed_at"] = t_now
                item["terminal_reason"] = None
                item["waiting_reason"] = f"Chat {a.get('chat_id')} answered"
                item.pop("awaiting_reply", None)
                item.pop("condition_met_at", None)
                item.pop("held_for_allowance", None)
                _append_mail_event(mid, {"at": t_now, "event": "chat_replied", "chat_id": a.get("chat_id")})
            elif verdict == "error":
                attempts = int(item.get("delivery_attempts") or 0) + 1
                item["delivery_attempts"] = attempts
                item.pop("awaiting_reply", None)
                item["claim_nonce"] = None
                if attempts >= ACK_MAX_ATTEMPTS:
                    item["state"] = "failed"
                    item["completed_at"] = t_now
                    item["terminal_reason"] = f"Chat could not answer after {attempts} deliveries (API errors)"
                    item["waiting_reason"] = item["terminal_reason"]
                else:
                    hold = _allowance_hold(_target_provider(item), _item_model(item))
                    item["state"] = "waiting"
                    item["condition_met_at"] = item.get("condition_met_at") or float(a.get("since") or t_now)
                    item["retry_after"] = (hold or {}).get("resets_at") or (t_now + 600)
                    item["next_check_at"] = item["retry_after"]
                    item["waiting_reason"] = f"Chat could not answer (API error, likely out of allowance); resending after {_s.fmt_ts(item['retry_after'])}"
                _append_mail_event(mid, {"at": t_now, "event": "chat_error", "attempt": attempts, "state": item["state"]})
            elif t_now >= float(a.get("deadline") or 0):
                item["state"] = "failed"
                item["completed_at"] = t_now
                how = "reply" if a.get("readable", True) else "mail_ack receipt"
                item["terminal_reason"] = f"No {how} from chat {a.get('chat_id')} within {int(ACK_TIMEOUT_SECONDS / 60)} min"
                item["waiting_reason"] = item["terminal_reason"]
                item.pop("awaiting_reply", None)
                _append_mail_event(mid, {"at": t_now, "event": "chat_no_reply"})
            else:
                gap = t_now - float(a.get("since") or t_now)
                a["next_check"] = t_now + min(120.0, max(15.0, gap / 2))
            item["updated_at"] = t_now
        if changed:
            _save_mail_store(store)


def _dispatch_chat_item(item: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    """Deliver a chat-targeted mail item through the same path /api/chat uses."""
    t_now = now if now is not None else time.time()
    mid = item["id"]
    deliver = item.get("deliver") or {}
    chat_id = str(deliver.get("chat_id") or "")
    text = str(item.get("task", {}).get("prompt") or "")
    source = str(deliver.get("source") or "")
    readable = True
    with contextlib.suppress(Exception):
        from hud_server import mail_ack
        readable = mail_ack.can_read(chat_id, source)
        if not readable:
            text += mail_ack.receipt_line(mid)
    model = str(deliver.get("model") or "")
    effort = str(deliver.get("effort") or "")
    result: Dict[str, Any] = {}
    error = ""
    try:
        from hud_server import chat_send
        pl_attachments = deliver.get("attachments") or None
        status, body = chat_send.send({
            "chat_id": chat_id,
            "text": text,
            "source": source,
            "model": model,
            "effort": effort,
            **({"provider": str(deliver.get("provider") or "")} if deliver.get("provider") else {}),
            **({"attachments": pl_attachments} if pl_attachments else {}),
        })
        if status != 200:
            result = {"delivered": False, "detail": str((body or {}).get("error") or f"HTTP {status}")}
        else:
            result = body or {}
    except Exception as e:
        error = f"{type(e).__name__}: {e}" if str(e).strip() else type(e).__name__
    delivered = bool(result.get("delivered") or result.get("ok")) and not error
    if not delivered and not error:
        error = str(result.get("detail") or result.get("method") or "not delivered")

    payload: Dict[str, Any] = {
        "at": t_now,
        "event": "chat_sent" if delivered else "chat_failed",
        "chat_id": chat_id,
        "source": source,
        "method": result.get("method"),
        "delivered": delivered,
        "detail": error or result.get("detail") or "",
    }
    if deliver.get("attachments"):
        payload["attachments"] = list(deliver.get("attachments") or [])

    with _s._state_lock("mail"):
        store = _load_mail_store()
        curr = (store.get("items") or {}).get(mid)
        if curr and curr.get("claim_nonce") == item.get("claim_nonce"):
            if delivered and _ack_required(source):
                # Done only once the chat answers (checked by _check_replies); until then the chat lane stays busy.
                curr["state"] = "running"
                curr["awaiting_reply"] = {"since": t_now, "chat_id": chat_id, "source": source,
                                          "deadline": t_now + ACK_TIMEOUT_SECONDS, "next_check": t_now + 15.0,
                                          "readable": readable}
                curr["terminal_reason"] = None
                curr["waiting_reason"] = f"Delivered to chat {chat_id}; waiting for its reply"
            elif delivered:
                curr["state"] = "done"
                curr["completed_at"] = t_now
                curr["terminal_reason"] = None
                curr["waiting_reason"] = f"Delivered to chat {chat_id}"
            else:
                curr["state"] = "failed"
                curr["completed_at"] = t_now
                curr["terminal_reason"] = f"Chat delivery failed: {error}"
                curr["waiting_reason"] = curr["terminal_reason"]
            curr["updated_at"] = t_now
            _save_mail_store(store)
            _append_mail_event(mid, payload)
            item["state"] = curr["state"]
            item["terminal_reason"] = curr.get("terminal_reason")
    with contextlib.suppress(Exception):
        _s.mark_hud_cache_stale()
    return result


def _rearm_repeat_item(mail_id: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Queue the next copy of a repeating time item once its run is marked done."""
    t_now = now if now is not None else time.time()
    with _s._state_lock("mail"):
        store = _load_mail_store()
        item = (store.get("items") or {}).get(mail_id)
        if not item or item.get("state") != "done":
            return None
        cond = item.get("condition") or {}
        if cond.get("type") != "time":
            return None
        repeat = str(cond.get("repeat") or "once").strip().lower()
        if repeat not in ("daily", "weekdays", "weekly"):
            return None
        run_at = cond.get("run_at")
        if run_at is None:
            run_at = item.get("created_at")
        with contextlib.suppress(TypeError, ValueError):
            run_at = float(run_at)
        if not isinstance(run_at, float):
            return None
        next_at = _next_repeat_run_at(run_at, repeat)
        base = dict(item)
        task = dict(base.get("task") or {})
        base_cond = dict(cond)

    new_cond = dict(base_cond)
    new_cond["run_at"] = next_at
    new_cond.pop("baseline_sigs", None)
    ok, res = create_mail_item(
        role=task.get("role") or "Chat message",
        prompt=task.get("prompt") or "",
        condition_type="time",
        condition=new_cond,
        provider=task.get("provider", "auto"),
        model=task.get("model", ""),
        effort=task.get("effort", "auto"),
        track=task.get("track", "main"),
        project_dir=task.get("project_dir", ""),
        isolate=task.get("isolate", True),
        private=task.get("private", False),
        mode=task.get("mode", "standard"),
        permission_mode=task.get("permission_mode", ""),
        capabilities=task.get("capabilities"),
        max_tokens=int(task.get("max_tokens") or 0),
        max_seconds=int(task.get("max_seconds") or 0),
        budget_session_pct=task.get("budget_session_pct"),
        budget_weekly_pct=task.get("budget_weekly_pct"),
        max_budget_usd=task.get("max_budget_usd"),
        review=task.get("review", ""),
        then=task.get("then") or None,
        created_by=base.get("created_by"),
        deliver=base.get("deliver"),
        now=t_now,
    )
    next_id = res.get("id") if ok and isinstance(res, dict) else None
    series_id = base.get("repeat_series_id") or mail_id
    with _s._state_lock("mail"):
        store = _load_mail_store()
        curr = (store.get("items") or {}).get(mail_id)
        if curr is not None:
            curr["updated_at"] = t_now
            _save_mail_store(store)
        _append_mail_event(mail_id, {"at": t_now, "event": "repeat_scheduled", "next_id": next_id,
                                     "next_run_at": next_at, "repeat": repeat})
        if next_id and next_id in (store.get("items") or {}):
            nxt = store["items"][next_id]
            nxt["repeat_series_id"] = series_id
            _save_mail_store(store)
    return store.get("items", {}).get(next_id) if next_id else None


def evaluate_mail_items(now: Optional[float] = None) -> List[Dict[str, Any]]:
    t_now = now if now is not None else time.time()
    claimed: List[Dict[str, Any]] = []
    _check_replies(t_now)

    with _s._state_lock("mail"):
        store = _load_mail_store()
        items = store.get("items", {})

        running_chats = {
            _chat_queue_key(it)
            for it in items.values()
            if it.get("state") == "running" and _chat_queue_key(it)
        }
        claimed_chats: set = set()
        ordered = sorted(
            items.items(),
            key=lambda kv: (float(kv[1].get("position") or 0.0), float(kv[1].get("created_at") or 0.0)),
        )
        for mid, item in ordered:
            if item.get("last_checked_at") and t_now < item["last_checked_at"]:
                item["last_checked_at"] = t_now

            st = item.get("state")
            if st not in ("queued", "waiting"):
                continue
            if float(item.get("retry_after") or 0) > t_now:
                item["next_check_at"] = item["retry_after"]
                continue

            # First tick arming
            if st == "queued":
                item["condition"] = _capture_baseline(item.get("condition", {}))
                item["state"] = "waiting"

            # Check drop on handoff
            handoff = _check_handoff_supersession(item, t_now)
            if handoff:
                peer, src = handoff
                item["state"] = "dropped"
                item["completed_at"] = t_now
                item["terminal_reason"] = f"Dropped: work handed off to {peer} via {src}"
                item["waiting_reason"] = item["terminal_reason"]
                _append_mail_event(mid, {"at": t_now, "event": "dropped", "reason": item["terminal_reason"], "handoff_peer": peer})
                _s._broadcast_track_internal(
                    f"[MAIL DROPPED] {item['id']} ({item['task']['role']} on {item['condition'].get('provider', 'auto')}): "
                    f"skipped allowance-reset run because work was handed off to `{peer}` ({src}).",
                    track=item["task"].get("track") or "main",
                    station_id="hud",
                )
                continue

            # Chat queue: one in-flight message per chat, lowest position first
            chat_key = _chat_queue_key(item)
            if chat_key and (chat_key in claimed_chats or chat_key in running_chats):
                item["waiting_reason"] = f"Waiting: an earlier message for chat {chat_key} is still in flight"
                item["next_check_at"] = t_now + 2.0
                item["last_checked_at"] = t_now
                continue

            # Evaluate condition (one already met while the target had no allowance stays met)
            if item.get("condition_met_at"):
                met, reason, extra = True, item.get("condition_met_reason") or "Condition met earlier", {}
            else:
                met, reason, extra = _evaluate_condition(item, t_now)
            if met and not extra.get("drop"):
                hold = _allowance_hold(_target_provider(item), _item_model(item))
                if hold:
                    first = not item.get("held_for_allowance")
                    item["condition_met_at"] = item.get("condition_met_at") or t_now
                    item["condition_met_reason"] = reason
                    item["held_for_allowance"] = hold
                    item["waiting_reason"] = _hold_reason(hold)
                    item["next_check_at"] = hold.get("resets_at")
                    item["last_checked_at"] = t_now
                    if first:
                        _append_mail_event(mid, {"at": t_now, "event": "held_for_allowance", **hold})
                    continue
                item.pop("held_for_allowance", None)
            if extra.get("drop"):
                item["state"] = "dropped"
                item["completed_at"] = t_now
                item["terminal_reason"] = extra.get("reason") or reason
                item["waiting_reason"] = item["terminal_reason"]
                _append_mail_event(mid, {"at": t_now, "event": "dropped", "reason": item["terminal_reason"]})
                _s._broadcast_track_internal(
                    f"[MAIL DROPPED] {item['id']} ({item['task']['role']}): {item['terminal_reason']}.",
                    track=item["task"].get("track") or "main",
                    station_id="hud",
                )
                continue

            if met:
                item["state"] = "running"
                item["claim_nonce"] = uuid.uuid4().hex
                item["last_checked_at"] = t_now
                item["waiting_reason"] = "Condition satisfied; dispatching"
                if chat_key:
                    claimed_chats.add(chat_key)
                _append_mail_event(mid, {"at": t_now, "event": "running", "claim_nonce": item["claim_nonce"], "reason": reason})
                claimed.append(dict(item))
            else:
                item["waiting_reason"] = reason
                item["next_check_at"] = extra.get("next_check_at")
                item["last_checked_at"] = t_now

        _save_mail_store(store)

    # Dispatch claimed items
    for item in claimed:
        mid = item["id"]
        task = item["task"]
        prompt = task["prompt"]

        if (item.get("deliver") or {}).get("kind") == "chat":
            _dispatch_chat_item(item)
            _rearm_repeat_item(mid)
            continue

        cond = item.get("condition", {})
        if cond.get("type") == "worker_done" and cond.get("include") in ("patch", "report", "both"):
            wid = cond["worker_id"]
            w = _s._load_worker(wid) or {}
            want = cond["include"]
            patch_text = ""
            if want in ("patch", "both"):
                p_path = w.get("patch_path") or os.path.join(_s.WORKERS_DIR, f"{wid}.patch")
                with contextlib.suppress(OSError):
                    with open(p_path, "r", encoding="utf-8") as pf:
                        patch_text = pf.read(40000)
            rep_text = str(w.get("response") or w.get("report") or "").strip()
            pieces = []
            if patch_text:
                pieces.append(patch_text)
            if want in ("report", "both") and rep_text:
                pieces.append(rep_text[:40000])
            if pieces:
                prompt = f"{prompt}\n\nPREVIOUS WORKER {wid} ({w.get('role', 'worker')}):\n" + "\n\n".join(pieces)

        kwargs = {
            "role": task["role"],
            "prompt": prompt,
            "provider": task.get("provider", "auto"),
            "model": task.get("model", ""),
            "track": task.get("track", "main"),
            "project_dir": task.get("project_dir", ""),
            "effort": task.get("effort", "auto"),
            "mode": task.get("mode", "standard"),
            "isolate": task.get("isolate", True),
            "private": task.get("private", False),
            "permission_mode": task.get("permission_mode", ""),
            "capabilities": task.get("capabilities"),
            "max_tokens": int(task.get("max_tokens") or 0),
            "max_seconds": int(task.get("max_seconds") or 0),
            "budget_session_pct": task.get("budget_session_pct"),
            "budget_weekly_pct": task.get("budget_weekly_pct"),
            "max_budget_usd": task.get("max_budget_usd"),
            "review": task.get("review", ""),
            "then": json.dumps(task["then"]) if isinstance(task.get("then"), list) else str(task.get("then") or ""),
        }

        launcher = item.get("created_by", {}).get("launcher") or "hud"
        orig_self_id = getattr(_s, "_self_id", lambda: launcher)
        try:
            _s._self_id = lambda: launcher
            out = _s.t_spin_worker(**kwargs)
        finally:
            _s._self_id = orig_self_id

        m = re.search(r"worker_[0-9a-zA-Z_]{6}", str(out))
        if m and not str(out).startswith("Error:"):
            wid = m.group(0)
            with _s._state_lock("workers"):
                w_meta = _s._load_worker(wid)
                if w_meta:
                    w_meta["mail_id"] = mid
                    w_meta["launched_by"] = launcher
                    _s._save_worker(w_meta)

            with _s._state_lock("mail"):
                st = _load_mail_store()
                if mid in st.get("items", {}):
                    curr = st["items"][mid]
                    if curr.get("claim_nonce") == item["claim_nonce"]:
                        curr["state"] = "done"
                        curr["spawned_worker_id"] = wid
                        curr["completed_at"] = time.time()
                        curr["waiting_reason"] = f"Spawned worker {wid}"
                        _save_mail_store(st)
                        _append_mail_event(mid, {"at": time.time(), "event": "done", "worker_id": wid})
            _rearm_repeat_item(mid)
        else:
            with _s._state_lock("mail"):
                st = _load_mail_store()
                if mid in st.get("items", {}):
                    curr = st["items"][mid]
                    if curr.get("claim_nonce") == item["claim_nonce"]:
                        curr["state"] = "waiting"
                        curr["claim_nonce"] = None
                        curr["waiting_reason"] = f"Dispatch failed: {out}"
                        _save_mail_store(st)
                        _append_mail_event(mid, {"at": time.time(), "event": "dispatch_error", "error": str(out)})

    return claimed


def recover_interrupted_mail(now: Optional[float] = None) -> List[Dict[str, Any]]:
    t_now = now if now is not None else time.time()
    recovered: List[Dict[str, Any]] = []
    with _s._state_lock("mail"):
        store = _load_mail_store()
        items = store.get("items", {})
        all_workers = _s._all_workers(include_archive=True) if hasattr(_s, "_all_workers") else []

        for mid, item in list(items.items()):
            if item.get("state") == "running" and isinstance(item.get("awaiting_reply"), dict):
                continue  # delivered and waiting for the chat's reply: resending would duplicate it
            if item.get("state") == "running":
                w = next((x for x in all_workers if x.get("mail_id") == mid), None)
                if w:
                    item["state"] = "done"
                    item["spawned_worker_id"] = w["id"]
                    item["completed_at"] = item.get("completed_at") or t_now
                    item["waiting_reason"] = f"Recovered completed dispatch: {w['id']}"
                    _append_mail_event(mid, {"at": t_now, "event": "recovered_done", "worker_id": w["id"]})
                else:
                    item["state"] = "waiting"
                    item["claim_nonce"] = None
                    item["waiting_reason"] = "Recovered after restart; re-queued for dispatch"
                    _append_mail_event(mid, {"at": t_now, "event": "recovered_waiting"})
                recovered.append(item)
        if recovered:
            _save_mail_store(store)
    return recovered


# MCP tool functions
def _resolve_to_chat(to_chat: str) -> Tuple[bool, Any]:
    target = str(to_chat or "").strip()
    if target.lower() in ("self", "@self"):
        self_info = _s.detect_self() if hasattr(_s, "detect_self") else None
        if isinstance(self_info, tuple) and len(self_info) >= 2:
            cid, src = str(self_info[0] or ""), str(self_info[1] or "")
        elif isinstance(self_info, dict):
            cid = str(self_info.get("chat_id") or self_info.get("id") or "")
            src = str(self_info.get("source") or "")
        elif isinstance(self_info, str) and self_info:
            cid, src = self_info, ""
        else:
            cid, src = "", ""
        if not cid:
            return False, "could not determine calling chat for 'self'"
        return True, (cid, src)
    try:
        resolved = _s.resolve(target, strict=True)
    except Exception as e:
        return False, f"could not resolve chat '{target}': {e}"
    if not resolved:
        return False, f"could not resolve chat '{target}'"
    if isinstance(resolved, tuple):
        cid = str(resolved[0] or "")
        src = str(resolved[1] or "") if len(resolved) > 1 else ""
    elif isinstance(resolved, dict):
        cid = str(resolved.get("chat_id") or resolved.get("id") or "")
        src = str(resolved.get("source") or "")
    else:
        cid, src = str(resolved), ""
    if not cid:
        return False, f"could not resolve chat '{target}'"
    return True, (cid, src)


def t_mail_post(
    role: str = "",
    prompt: str = "",
    condition_type: str = "",
    condition: Optional[Dict[str, Any]] = None,
    to_chat: str = "",
    **kwargs,
) -> str:
    cond = condition if condition is not None else {}
    if str(to_chat or "").strip():
        ok_chat, chat_res = _resolve_to_chat(to_chat)
        if not ok_chat:
            return f"Error: {chat_res}"
        chat_id, source = chat_res
        if not str(role or "").strip():
            role = "Chat message"
        kwargs["deliver"] = {
            "kind": "chat",
            "chat_id": chat_id,
            "source": source,
            "model": str(kwargs.get("model") or ""),
            "effort": str(kwargs.get("effort") or ""),
            "attachments": [],
        }
    ok, res = create_mail_item(role=role, prompt=prompt, condition_type=condition_type, condition=cond, **kwargs)
    if not ok:
        return f"Error: {res}"
    return f"[MAIL POSTED] Item `{res['id']}` ({res['task']['role']} on {res['task']['provider']}) queued with condition '{condition_type}'."


def t_mail_list(state: str = "all", track: str = "", limit: int = 50) -> str:
    res = list_mail_items(state=state, track=track, limit=limit)
    items = res["items"]
    if not items:
        return f"No mail items found (state='{state}', track='{track or 'all'}')."
    lines = [f"Mailbox items ({len(items)} shown, total {sum(res['counts'].values())}):"]
    for it in items:
        lines.append(f"- `{it['id']}` [{it['state'].upper()}] {it['task']['role']} ({it['task']['provider']}) — {it.get('waiting_reason', '')}")
    return "\n".join(lines)


def t_mail_ack(mail_id: str) -> str:
    """Receipt from the chat a mail item was delivered to (used by agents Subway cannot read)."""
    t_now = time.time()
    with _s._state_lock("mail"):
        store = _load_mail_store()
        item = (store.get("items") or {}).get(mail_id)
        if not item:
            return f"Error: no mail item {mail_id}"
        if item.get("state") != "running" or not isinstance(item.get("awaiting_reply"), dict):
            return f"[MAIL ACK] {mail_id} is {item.get('state')}; nothing to acknowledge"
        item["acked_at"] = t_now
        item["awaiting_reply"]["next_check"] = 0
        item["updated_at"] = t_now
        _save_mail_store(store)
    _append_mail_event(mail_id, {"at": t_now, "event": "acked"})
    return f"[MAIL ACK] {mail_id} acknowledged"


def t_mail_cancel(mail_id: str, reason: str = "Cancelled by user/agent") -> str:
    ok, res = cancel_mail_item(mail_id, reason=reason)
    if not ok:
        return f"Error: {res}"
    return f"[MAIL CANCELLED] Item `{mail_id}` cancelled. Reason: {reason}"


def t_mail_edit(mail_id: str, **kwargs) -> str:
    ok, res = edit_mail_item(mail_id, kwargs)
    if not ok:
        return f"Error: {res}"
    return f"[MAIL UPDATED] Item `{mail_id}` updated successfully."


# HTTP handlers
def http_get_mail(state: str = "all", track: str = "", limit: int = 100) -> Dict[str, Any]:
    return list_mail_items(state=state, track=track, limit=limit)


def http_post_mail(payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    if not isinstance(payload, dict):
        return 400, {"error": "payload must be a JSON object"}
    mail_id = payload.get("mail_id") or payload.get("id")
    if mail_id:
        ok, res = edit_mail_item(str(mail_id), payload)
        if not ok:
            status = 404 if "not found" in str(res).lower() else 400
            return status, {"error": str(res)}
        return 200, {"ok": True, "item": res}
    else:
        created_by = {"surface": "hud", "launcher": "hud", "station_id": "hud"}
        ok, res = create_mail_item(
            role=payload.get("role", ""),
            prompt=payload.get("prompt", ""),
            condition_type=payload.get("condition_type", ""),
            condition=payload.get("condition") or {},
            provider=payload.get("provider", "auto"),
            model=payload.get("model", ""),
            track=payload.get("track", "main"),
            isolate=payload.get("isolate", True),
            permission_mode=payload.get("permission_mode", ""),
            max_tokens=payload.get("max_tokens", 0),
            max_seconds=payload.get("max_seconds", 0),
            created_by=created_by,
            deliver=payload.get("deliver"),
            position=payload.get("position"),
        )
        if not ok:
            return 400, {"error": str(res)}
        return 200, {"ok": True, "item": res}


def http_edit_mail(payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    if not isinstance(payload, dict):
        return 400, {"error": "payload must be a JSON object"}
    mail_id = payload.get("mail_id") or payload.get("id")
    if not mail_id:
        return 400, {"error": "missing mail_id"}
    ok, res = edit_mail_item(str(mail_id), payload)
    if not ok:
        status = 404 if "not found" in str(res).lower() else 400
        return status, {"error": str(res)}
    return 200, {"ok": True, "item": res}


def http_cancel_mail(payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    if not isinstance(payload, dict):
        return 400, {"error": "payload must be a JSON object"}
    mail_id = payload.get("mail_id") or payload.get("id")
    if not mail_id:
        return 400, {"error": "missing mail_id"}
    reason = payload.get("reason", "Cancelled from HUD")
    ok, res = cancel_mail_item(str(mail_id), reason=reason)
    if not ok:
        err = str(res)
        if "not found" in err.lower():
            return 404, {"error": err}
        if "terminal state" in err.lower():
            return 409, {"error": err}
        return 400, {"error": err}
    return 200, {"ok": True, "item": res}


# Composer/HUD handler wrappers (return dict payloads)
def handle_composer_mail(payload: Dict[str, Any]) -> Dict[str, Any]:
    status, res = http_post_mail(payload)
    if status != 200:
        return {"error": res.get("error", "Error"), "status": status}
    return res


def handle_composer_edit(payload: Dict[str, Any]) -> Dict[str, Any]:
    status, res = http_edit_mail(payload)
    if status != 200:
        return {"error": res.get("error", "Error"), "status": status}
    return res


def handle_composer_cancel(payload: Dict[str, Any]) -> Dict[str, Any]:
    status, res = http_cancel_mail(payload)
    if status != 200:
        return {"error": res.get("error", "Error"), "status": status}
    return res


handle_edit_mail = handle_composer_edit
handle_cancel_mail = handle_composer_cancel
handle_post_mail = handle_composer_mail

# Tool aliases expected by server.py
t_post_mail = t_mail_post
t_list_mail = t_mail_list
t_edit_mail = t_mail_edit
t_cancel_mail = t_mail_cancel
