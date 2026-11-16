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
)
VALID_PERMISSION_MODES = ("", "ask", "auto_safe", "auto_approve")
ACTIVE_STATES = ("queued", "waiting", "running")
TERMINAL_STATES = ("done", "dropped", "cancelled")


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
    now: Optional[float] = None,
) -> Tuple[bool, Any]:
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
        "claim_nonce": None,
        "spawned_worker_id": None,
        "terminal_reason": None,
        "completed_at": None,
    }

    with _s._state_lock("mail"):
        store = _load_mail_store()
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
        for field in ("role", "prompt", "provider", "model", "track", "project_dir", "effort", "isolate", "private", "permission_mode", "max_tokens", "max_seconds"):
            if field in updates and updates[field] is not None:
                if field == "permission_mode" and updates[field] not in VALID_PERMISSION_MODES:
                    return False, f"unknown permission_mode '{updates[field]}'"
                task[field] = updates[field]
        item["task"] = task

        if "condition_type" in updates or "condition" in updates:
            ctype = updates.get("condition_type", item.get("condition", {}).get("type"))
            if ctype not in VALID_CONDITION_TYPES:
                return False, f"invalid condition_type '{ctype}'"
            cond = dict(updates.get("condition") or item.get("condition", {}))
            cond["type"] = ctype
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
    counts = {"queued": 0, "waiting": 0, "running": 0, "done": 0, "dropped": 0, "cancelled": 0}
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

    state_order = {"running": 0, "waiting": 1, "queued": 2, "done": 3, "dropped": 4, "cancelled": 5}
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

    return False, "Unknown condition type", {}


def evaluate_mail_items(now: Optional[float] = None) -> List[Dict[str, Any]]:
    t_now = now if now is not None else time.time()
    claimed: List[Dict[str, Any]] = []

    with _s._state_lock("mail"):
        store = _load_mail_store()
        items = store.get("items", {})

        for mid, item in list(items.items()):
            if item.get("last_checked_at") and t_now < item["last_checked_at"]:
                item["last_checked_at"] = t_now

            st = item.get("state")
            if st not in ("queued", "waiting"):
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

            # Evaluate condition
            met, reason, extra = _evaluate_condition(item, t_now)
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
                item["waiting_reason"] = "Condition satisfied; dispatching worker"
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
def t_mail_post(role: str, prompt: str, condition_type: str, condition: Dict[str, Any], **kwargs) -> str:
    ok, res = create_mail_item(role=role, prompt=prompt, condition_type=condition_type, condition=condition, **kwargs)
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
