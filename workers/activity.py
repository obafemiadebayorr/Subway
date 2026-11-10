"""Per-day worker activity ledger.

The /api/worker_activity endpoint originally bucketed live worker records, so
per-day spun/finished/token counts dropped whenever a worker was archived or
deleted. The ledger records each event once, keyed by worker id, so history
survives archival.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os

import server as _s

_LEDGER_FILE = "activity_ledger.json"


def _ledger_path() -> str:
    return os.path.join(_s.SUBWAY_DIR, _LEDGER_FILE)


def _empty_day() -> dict:
    return {"spun": 0, "finished": 0, "tokens": 0,
            "ids": {"spun": [], "finished": []}}


def _created_ts(meta: dict):
    """Same day bucketing as the original endpoint: ISO string or s/ms epoch."""
    ts = meta.get("created_at")
    if isinstance(ts, str):
        try:
            ts = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = None
    if isinstance(ts, (int, float)):
        ts = ts / 1000 if ts > 1e11 else ts
    else:
        ts = None
    return ts


def _day_of(meta: dict):
    ts = _created_ts(meta)
    if not ts:
        return None
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _worker_tokens(meta: dict) -> int:
    usage = meta.get("usage") or {}
    return int(meta.get("tokens") or (usage.get("input", 0) + usage.get("output", 0)) or 0)


def _load() -> dict:
    try:
        with open(_ledger_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _save(data: dict) -> None:
    _s._ensure_subway_dir()
    _s._write_json_atomic(_ledger_path(), data)


def _record(meta: dict, kind: str, tokens: int = 0) -> None:
    """Record one event for a worker, counted once per worker id per kind."""
    if not isinstance(meta, dict) or not meta.get("id"):
        return
    day = _day_of(meta)
    if not day:
        return
    with _s._state_lock("activity_ledger"):
        data = _load()
        entry = data.setdefault(day, _empty_day())
        ids = entry.setdefault("ids", {"spun": [], "finished": []})
        if kind == "spun":
            if meta["id"] in ids["spun"]:
                return
            ids["spun"].append(meta["id"])
            entry["spun"] = entry.get("spun", 0) + 1
        else:  # finished
            if meta["id"] in ids["finished"]:
                return  # already terminal once; unarchive/restart must not double count
            ids["finished"].append(meta["id"])
            entry["finished"] = entry.get("finished", 0) + 1
            entry["tokens"] = entry.get("tokens", 0) + int(tokens or 0)
            ids.setdefault("tok", {})[meta["id"]] = int(tokens or 0)
        _save(data)


def record_spun(meta: dict) -> None:
    with contextlib.suppress(Exception):
        _record(meta, "spun")


def record_finished(meta: dict) -> None:
    if not isinstance(meta, dict):
        return
    if (meta.get("status") or "") in _s.ACTIVE_WORKER_STATES:
        return
    with contextlib.suppress(Exception):
        _record(meta, "finished", tokens=_worker_tokens(meta))


def backfill() -> dict:
    """One-time backfill from live AND archived records when the ledger is missing."""
    data = {}
    for w in _s._all_workers(include_archive=True):
        day = _day_of(w)
        if not day:
            continue
        entry = data.setdefault(day, _empty_day())
        ids = entry["ids"]
        if w["id"] not in ids["spun"]:
            ids["spun"].append(w["id"])
            entry["spun"] += 1
        if (w.get("status") or "") not in _s.ACTIVE_WORKER_STATES and w["id"] not in ids["finished"]:
            ids["finished"].append(w["id"])
            entry["finished"] += 1
            entry["tokens"] += _worker_tokens(w)
            ids.setdefault("tok", {})[w["id"]] = _worker_tokens(w)
    _save(data)
    return data


def get_worker_activity(days: int) -> dict:
    """Endpoint payload: per-day activity over the last `days` days."""
    days = max(1, int(days or 14))
    now = _dt.datetime.now().timestamp()
    with _s._state_lock("activity_ledger"):
        data = _load()
        if not (data.get("_meta") or {}).get("backfilled"):
            backfilled = backfill()
            # Merge per worker id: a worker present on both sides counts once,
            # and live-recorded entries win over backfilled ones.
            for day, live in list(data.items()):
                if day == "_meta" or not isinstance(live, dict):
                    continue
                merged = backfilled.setdefault(day, _empty_day())
                l_ids = live.get("ids") or {}
                b_ids = merged.get("ids") or {}
                spun = set(b_ids.get("spun") or []) | set(l_ids.get("spun") or [])
                fin = set(b_ids.get("finished") or []) | set(l_ids.get("finished") or [])
                merged["ids"] = {
                    "spun": sorted(spun),
                    "finished": sorted(fin),
                    "tok": dict(b_ids.get("tok") or {}),
                }
                merged["spun"] = len(spun)
                merged["finished"] = len(fin)
                # Live day total already covers its own finished ids; add only
                # backfilled tokens for ids the live ledger does not have.
                live_fin = set(l_ids.get("finished") or [])
                extra = sum(int((b_ids.get("tok") or {}).get(w, 0))
                            for w in fin - live_fin)
                merged["tokens"] = int(live.get("tokens") or 0) + extra
            backfilled["_meta"] = {"backfilled": True}
            _save(backfilled)
            data = backfilled
    activity = []
    for i in range(days):
        day = _dt.datetime.fromtimestamp(now - (days - 1 - i) * 86400).strftime("%Y-%m-%d")
        b = data.get(day) or {}
        activity.append({
            "day": day,
            "spun": int(b.get("spun") or 0),
            "finished": int(b.get("finished") or 0),
            "tokens": int(b.get("tokens") or 0),
        })
    return {"days": days, "activity": activity}


_record_activity_spun = record_spun
_record_activity_finished = record_finished
