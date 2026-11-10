"""GET /api/stats: worker-record analytics for the HUD Stats page.

Sources (never includes secret/cmd/prompt): ~/.subway-context/workers/worker_*.json
- usage.input, usage.output, usage.cache_read
- meta.stats.{tool_calls,edits,thinking_tokens,files_changed,wall_seconds} when present
- timestamps: started_at, completed_at, created_at
- provider, model, status
"""
from __future__ import annotations

import glob
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

WORKERS_DIR = os.path.expanduser("~/.subway-context/workers")
CLAUDE_PROJECTS_DIR: Optional[str] = None
RANGES = {"1h": 3600, "5h": 5 * 3600, "24h": 24 * 3600, "7d": 7 * 86400, "30d": 30 * 86400, "90d": 90 * 86400}
METRICS = {"tokens", "time", "speed", "work", "tool_calls", "thinking", "cache", "failures"}
GROUPS = {"provider", "model", "hour"}
FAIL_STATUSES = {"failed", "budget_stopped", "timed_out"}
FINISHED = {"completed", "failed", "budget_stopped", "timed_out", "stopped", "rejected"}

_TTL = 30.0
_cache: Dict[str, Any] = {"key": None, "at": 0.0, "value": None}

_SESSIONS_TTL = 60.0
_sessions_cache: Dict[str, Any] = {"key": None, "at": 0.0, "value": None}


def _parse_ts(val: Any) -> Optional[float]:
    if not val:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        s = str(val)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _load_workers() -> List[Dict[str, Any]]:
    out = []
    for path in glob.glob(os.path.join(WORKERS_DIR, "worker_*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                out.append(d)
        except Exception:
            continue
    return out


def _record_time(d: Dict[str, Any]) -> Optional[float]:
    return _parse_ts(d.get("completed_at")) or _parse_ts(d.get("started_at")) or _parse_ts(d.get("created_at"))


def _wall_seconds(d: Dict[str, Any]) -> Optional[float]:
    stats = d.get("stats") or {}
    if isinstance(stats, dict) and stats.get("wall_seconds") is not None:
        try:
            return float(stats["wall_seconds"])
        except Exception:
            pass
    s = _parse_ts(d.get("started_at"))
    e = _parse_ts(d.get("completed_at"))
    if s and e and e >= s:
        return e - s
    return None


# Short ranges use minute buckets so the chart has enough points.
MINUTE_BUCKETS = {"1h": ("5min", 300), "5h": ("15min", 900)}


def _bucket_start(ts: float, bucket: str) -> int:
    if bucket in ("5min", "15min"):
        step = 300 if bucket == "5min" else 900
        return int(ts // step * step)
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if bucket == "hour":
        dt = dt.replace(minute=0, second=0, microsecond=0)
    else:
        dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(dt.timestamp())


def compute(range_key: str = "24h", metric: str = "tokens", group: str = "provider", end: Optional[float] = None) -> Dict[str, Any]:
    range_key = range_key if range_key in RANGES else "24h"
    metric = metric if metric in METRICS else "tokens"
    group = group if group in GROUPS else "provider"
    if range_key in MINUTE_BUCKETS:
        bucket_kind, step = MINUTE_BUCKETS[range_key]
    else:
        bucket_kind = "hour" if range_key == "24h" else "day"
        step = 3600 if bucket_kind == "hour" else 86400
    if end is not None:
        end = float(_bucket_start(end, bucket_kind))
    cache_key = (range_key, metric, group, end)
    now = time.time()
    if _cache["key"] == cache_key and _cache["value"] is not None and now - _cache["at"] < _TTL:
        return _cache["value"]

    window = RANGES[range_key]
    t_end = min(end, now) if end else now
    cutoff = t_end - window
    start_bucket = _bucket_start(cutoff, bucket_kind)
    end_bucket = _bucket_start(t_end, bucket_kind)
    buckets = list(range(start_bucket, end_bucket + step, step))

    workers = _load_workers()
    in_range = []
    for d in workers:
        ts = _record_time(d)
        if ts is not None and ts >= cutoff and ts <= t_end:
            in_range.append((ts, d))

    series_map: Dict[str, Dict[str, Any]] = {}
    provider_agg: Dict[str, Dict[str, Any]] = {}
    breakdown_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    totals = {"processed": 0, "cached_input": 0, "uncached_input": 0, "output": 0, "thinking": 0}
    type_sums = {"input": 0, "cache_read": 0, "output": 0, "thinking": 0}
    speeds: List[float] = []
    run_secs: List[float] = []
    total_val = 0.0
    count = 0

    def add_series(sid: str, name: str, bidx: int, val: float):
        s = series_map.setdefault(sid, {"id": sid, "name": name, "values": [0.0] * len(buckets)})
        if 0 <= bidx < len(s["values"]):
            s["values"][bidx] += val

    def metric_value(d: Dict[str, Any]) -> Tuple[float, bool]:
        usage = d.get("usage") or {}
        stats = d.get("stats") or {}
        inp = int(usage.get("input") or 0)
        out = int(usage.get("output") or 0)
        cache = int(usage.get("cache_read") or 0)
        thinking = stats.get("thinking_tokens") if isinstance(stats, dict) else None
        if metric == "tokens":
            return float(inp + out + cache), True
        if metric == "time":
            ws = _wall_seconds(d)
            return (float(ws), True) if ws is not None else (0.0, False)
        if metric == "speed":
            ws = _wall_seconds(d)
            if ws and ws > 0 and out > 0:
                return float(out) / ws, True
            return 0.0, False
        if metric == "work":
            if str(d.get("status") or "") not in FINISHED:
                return 0.0, False
            edits = int(stats.get("edits") or 0) if isinstance(stats, dict) else 0
            files = int(stats.get("files_changed") or 0) if isinstance(stats, dict) else 0
            return float(1 + edits + files), True
        if metric == "tool_calls":
            if isinstance(stats, dict) and stats.get("tool_calls") is not None:
                return float(int(stats.get("tool_calls") or 0)), True
            return 0.0, False
        if metric == "thinking":
            if thinking is not None:
                try:
                    return float(int(thinking)), True
                except Exception:
                    return 0.0, False
            return 0.0, False
        if metric == "cache":
            total = inp + out + cache
            return (float(cache) / total * 100.0, True) if total > 0 else (0.0, False)
        if metric == "failures":
            return (1.0, True) if str(d.get("status") or "") in FAIL_STATUSES else (0.0, False)
        return 0.0, False

    for ts, d in in_range:
        usage = d.get("usage") or {}
        stats = d.get("stats") or {}
        inp = int(usage.get("input") or 0)
        out = int(usage.get("output") or 0)
        cache = int(usage.get("cache_read") or 0)
        think = int(stats.get("thinking_tokens") or 0) if isinstance(stats, dict) and stats.get("thinking_tokens") is not None else 0
        totals["processed"] += inp + out + cache
        totals["cached_input"] += cache
        totals["uncached_input"] += max(0, inp - cache)
        totals["output"] += out
        totals["thinking"] += think
        type_sums["input"] += inp
        type_sums["cache_read"] += cache
        type_sums["output"] += out
        type_sums["thinking"] += think
        ws = _wall_seconds(d)
        if ws and ws > 0 and out > 0:
            speeds.append(out / ws)
            run_secs.append(ws)

        val, ok = metric_value(d)
        if not ok:
            if metric not in ("failures", "cache"):
                continue
        if metric == "failures" and val == 0:
            pass
        elif metric == "cache" and not ok:
            continue
        else:
            if not (metric == "failures" and val == 0):
                count += 1
        total_val += val

        provider = str(d.get("provider") or "unknown")
        model = str(d.get("model") or "unknown")
        b = _bucket_start(ts, bucket_kind)
        try:
            bidx = buckets.index(b)
        except ValueError:
            bidx = min(range(len(buckets)), key=lambda i: abs(buckets[i] - b)) if buckets else -1
        sid = provider if group == "provider" else model if group == "model" else _hour_label(b)
        sname = sid
        add_series(sid, sname, bidx, val)

        pa = provider_agg.setdefault(provider, {"id": provider, "name": provider, "value": 0.0, "count": 0})
        pa["value"] += val
        pa["count"] += 1
        bk = (model, provider)
        ba = breakdown_map.setdefault(bk, {"model": model, "provider": provider, "value": 0.0, "count": 0})
        ba["value"] += val
        ba["count"] += 1

    # normalize shares
    by_provider = sorted(provider_agg.values(), key=lambda x: -x["value"])
    for p in by_provider:
        p["share"] = round((p["value"] / total_val * 100) if total_val else 0.0, 2)
        p["value"] = _round_val(p["value"], metric)
    breakdown = sorted(breakdown_map.values(), key=lambda x: -x["value"])
    for b in breakdown:
        b["share"] = round((b["value"] / total_val * 100) if total_val else 0.0, 2)
        b["value"] = _round_val(b["value"], metric)

    if metric == "speed":
        speeds.sort()
        med = speeds[len(speeds)//2] if speeds else 0.0
        avg_run = (sum(run_secs) / len(run_secs)) if run_secs else 0.0
        total_out = _round_val(med, metric)
        speed_note = {"median_output_tokens_per_sec": round(med, 2), "avg_run_seconds": round(avg_run, 2)}
    else:
        total_out = _round_val(total_val, metric)
        speed_note = None

    sess_data = _compute_sessions_cached(range_key)
    result = {
        "range": range_key,
        "metric": metric,
        "unit": _unit(metric),
        "total": total_out,
        "count": count,
        "bucket": bucket_kind,
        "buckets": buckets,
        "series": sorted(series_map.values(), key=lambda s: -sum(s["values"])),
        "by_provider": by_provider,
        "totals": totals,
        "by_type": type_sums,
        "breakdown": breakdown,
        "sessions": sess_data["sessions"],
        "principal_cache_read": sess_data["principal_cache_read"],
        "worker_cache_read": sess_data["worker_cache_read"],
        "sources": [
            "worker records (~/.subway-context/workers/worker_*.json): usage.input, usage.output, usage.cache_read",
            "worker records meta.stats when present: tool_calls, edits, thinking_tokens, files_changed, wall_seconds",
            "timestamps: started_at, completed_at, created_at",
            "speed derived from output tokens / wall_seconds (median tokens/sec, avg run seconds)",
            "tool_calls/thinking/work counters require persisted meta.stats; missing stats contribute 0 for those counters",
        ],
        "generated_at": int(now),
    }
    if speed_note:
        result["speed"] = speed_note
    _cache.update({"key": cache_key, "at": now, "value": result})
    return result


def _hour_label(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:00")


def _unit(metric: str) -> str:
    return {
        "tokens": "tokens", "time": "seconds", "speed": "tokens/sec",
        "work": "units", "tool_calls": "calls", "thinking": "tokens",
        "cache": "percent", "failures": "workers",
    }.get(metric, "")


def _round_val(v: float, metric: str) -> Any:
    if metric in ("cache", "speed"):
        return round(v, 2)
    return int(round(v))


def _get_claude_projects_dir() -> str:
    if CLAUDE_PROJECTS_DIR:
        return CLAUDE_PROJECTS_DIR
    return os.path.expanduser(os.path.join("~", ".claude", "projects"))


_file_agg: Dict[str, Tuple[float, int, Dict[str, Any]]] = {}


def _scan_claude_file(fpath: str) -> Dict[str, Any]:
    try:
        st = os.stat(fpath)
        mtime = st.st_mtime
        size = st.st_size
    except OSError:
        return {}

    cached = _file_agg.get(fpath)
    if cached is not None and cached[0] == mtime and cached[1] == size:
        return cached[2]

    is_worker = False
    title: Optional[str] = None
    session_id: Optional[str] = None
    seen_mids = set()
    records: List[Tuple[float, int, int]] = []

    try:
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue

                # entrypoint check
                if d.get("entrypoint") == "sdk-cli":
                    is_worker = True

                if not session_id:
                    session_id = d.get("sessionId") or d.get("session_id")
                if not title and (d.get("title") or d.get("session_title")):
                    title = str(d.get("title") or d.get("session_title"))

                msg = d.get("message")
                target = msg if isinstance(msg, dict) else d
                usage = target.get("usage")
                if not isinstance(usage, dict):
                    continue

                mid = target.get("id") or d.get("uuid") or d.get("id")
                if mid:
                    if mid in seen_mids:
                        continue
                    seen_mids.add(mid)

                ts_val = _parse_ts(d.get("timestamp") or target.get("timestamp"))
                if ts_val is None:
                    continue

                cr = int(usage.get("cache_read_input_tokens") or usage.get("cache_read") or 0)
                cc = int(usage.get("cache_creation_input_tokens") or 0)
                inp = int(usage.get("input_tokens") or usage.get("input") or 0)

                ctx = cr + cc + inp
                records.append((ts_val, cr, ctx))

        agg: Dict[str, Any] = {
            "is_worker": is_worker,
            "title": title,
            "session_id": session_id,
            "records": records,
        }
        _file_agg[fpath] = (mtime, size, agg)
        return agg
    except Exception:
        return {}


def _compute_sessions_data(range_key: str = "24h") -> Dict[str, Any]:
    range_key = range_key if range_key in RANGES else "24h"
    now = time.time()
    cutoff = now - RANGES[range_key]

    all_sessions: List[Dict[str, Any]] = []

    # 1. Claude sessions from ~/.claude/projects/*/*.jsonl
    projects_dir = _get_claude_projects_dir()
    jsonl_pattern = os.path.join(projects_dir, "*", "*.jsonl")
    for fpath in glob.glob(jsonl_pattern):
        try:
            try:
                if os.path.getmtime(fpath) < cutoff:
                    continue
            except OSError:
                continue

            agg = _scan_claude_file(fpath)
            if not agg:
                continue

            records = agg.get("records") or []
            calls = 0
            cache_read = 0
            peak_ctx = 0
            last_ts: Optional[float] = None

            for ts_val, cr, ctx in records:
                if ts_val < cutoff:
                    continue
                calls += 1
                cache_read += cr
                if ctx > peak_ctx:
                    peak_ctx = ctx
                if last_ts is None or ts_val > last_ts:
                    last_ts = ts_val

            if calls > 0:
                session_id = agg.get("session_id")
                if not session_id:
                    session_id = os.path.splitext(os.path.basename(fpath))[0]
                title = agg.get("title")
                if not title:
                    title = os.path.basename(os.path.dirname(fpath))
                kind = "worker" if agg.get("is_worker") else "principal"
                all_sessions.append({
                    "id": str(session_id)[:8],
                    "provider": "claude",
                    "kind": kind,
                    "title": title,
                    "calls": calls,
                    "cache_read": cache_read,
                    "peak_ctx": peak_ctx,
                    "last_ts": last_ts or 0.0,
                })
        except Exception:
            continue

    # 2. Antigravity / others from worker records (where provider != "claude")
    workers = _load_workers()
    for d in workers:
        provider = str(d.get("provider") or "unknown")
        if provider == "claude":
            continue
        ts = _record_time(d)
        if ts is None or ts < cutoff:
            continue
        usage = d.get("usage") or {}
        inp = int(usage.get("input") or 0)
        cr = int(usage.get("cache_read") or 0)
        stats = d.get("stats") or {}
        calls = int(stats.get("tool_calls") or 1) if isinstance(stats, dict) else 1
        wid = str(d.get("id") or d.get("worker_id") or "worker")[:8]
        title = str(d.get("role") or d.get("task") or d.get("model") or "worker")
        all_sessions.append({
            "id": wid,
            "provider": provider,
            "kind": "unknown",
            "title": title,
            "calls": calls,
            "cache_read": cr,
            "peak_ctx": inp + cr,
            "last_ts": ts,
        })

    principal_cache_read = sum(s["cache_read"] for s in all_sessions if s["kind"] == "principal")
    worker_cache_read = sum(s["cache_read"] for s in all_sessions if s["kind"] == "worker")

    all_sessions.sort(key=lambda s: -s["cache_read"])
    top_sessions = all_sessions[:15]

    return {
        "sessions": top_sessions,
        "principal_cache_read": principal_cache_read,
        "worker_cache_read": worker_cache_read,
    }


def _compute_sessions_cached(range_key: str = "24h") -> Dict[str, Any]:
    range_key = range_key if range_key in RANGES else "24h"
    now = time.time()
    if _sessions_cache["key"] == range_key and _sessions_cache["value"] is not None and now - _sessions_cache["at"] < _SESSIONS_TTL:
        return _sessions_cache["value"]
    val = _compute_sessions_data(range_key)
    _sessions_cache.update({"key": range_key, "at": now, "value": val})
    return val


def sessions(range_key: str = "24h") -> List[Dict[str, Any]]:
    return _compute_sessions_cached(range_key)["sessions"]
