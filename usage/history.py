"""Hourly token history, so the spend chart can page back past 24 hours.

Each provider already counts a 24-hour series for its card. This module buckets
the same raw events over a wider window on request; nothing is computed until
the HUD asks for more hours, so a page load still only costs one day of data.
"""
from __future__ import annotations

import sys
import time
from typing import Any, Dict, List

try:
    import server as _s
except Exception:
    _s = sys.modules.get("server")

MAX_HOURS = 168  # A week: the longest window the sources keep transcripts for.
STEP_HOURS = 24

NAMES = {"claude": "Claude Code", "antigravity": "Antigravity", "opencode": "OpenCode",
         "copilot": "GitHub Copilot", "pi": "DeepAI Code"}


def _norm(hours: Any) -> int:
    try:
        value = int(hours)
    except (TypeError, ValueError):
        value = STEP_HOURS
    value = max(STEP_HOURS, min(MAX_HOURS, value))
    return value - value % STEP_HOURS if value % STEP_HOURS else value


def _bucket(events: List[tuple], hours: int, now: float) -> List[int]:
    out = [0] * hours
    for ts, used in events:
        if not isinstance(ts, (int, float)) or ts > 1e11:
            if isinstance(ts, (int, float)):
                ts = ts / 1000.0
            else:
                continue
        idx = int((now - ts) // 3600)
        if 0 <= idx < hours:
            out[hours - 1 - idx] += int(used or 0)
    return out


def _pi_hourly(hours: int, now: float) -> List[int]:
    try:
        from .pi import _load_pi_state
        state = _load_pi_state()
    except Exception:
        return [0] * hours
    events = []
    for ev in state.get("events") or []:
        if isinstance(ev, dict):
            events.append((ev.get("ts"), int(ev.get("inp") or 0) + int(ev.get("out") or 0)))
    return _bucket(events, hours, now)


def _ledger_hourly(parse, warm, hours: int, now: float) -> List[int]:
    """Bucket a transcript ledger (Claude, Antigravity) over any window."""
    try:
        warm()
        state = _s._ledger_cache.get(parse) or {}
        files = state.get("files") or {}
    except Exception:
        return [0] * hours
    events = []
    for _, parsed in list(files.values()):
        for ts, inp, out, _cache in parsed:
            events.append((ts, inp + out))
    return _bucket(events, hours, now)


def _claude_hourly(hours: int, now: float) -> List[int]:
    return _ledger_hourly(_s._parse_claude_usage, _s._fetch_claude_tokens, hours, now)


def _agy_hourly(hours: int, now: float) -> List[int]:
    return _ledger_hourly(_s._parse_agy_usage, _s._fetch_agy_tokens, hours, now)


def _opencode_hourly(hours: int, now: float) -> List[int]:
    try:
        con = _s._opencode_db()
    except Exception:
        con = None
    if con is None:
        return [0] * hours
    normalized = "case when time_created > 100000000000 then time_created / 1000.0 else time_created end"
    rows = []
    try:
        for created, used in con.execute(
                "select time_created, coalesce(json_extract(data,'$.tokens.input'),0)+"
                "coalesce(json_extract(data,'$.tokens.output'),0)"
                f" from session_message where type='assistant' and {normalized}>=?",
                (now - hours * 3600,)):
            rows.append((_s._opencode_timestamp(created), used))
    except Exception:
        return [0] * hours
    return _bucket(rows, hours, now)


def token_history(hours: Any = STEP_HOURS) -> Dict[str, Any]:
    """Hourly new tokens per provider, oldest first, ending now."""
    want = _norm(hours)
    now = time.time()
    series = []
    for key, fn in (("claude", _claude_hourly), ("antigravity", _agy_hourly),
                    ("opencode", _opencode_hourly), ("pi", _pi_hourly)):
        try:
            values = fn(want, now)
        except Exception:
            values = [0] * want
        if any(values):
            series.append({"id": key, "name": NAMES.get(key, key), "values": values})
    return {"hours": want, "max_hours": MAX_HOURS, "generated_at": now, "series": series}
