"""Work metrics (roadmap item 23): worker outcomes, tokens by provider, and daily series. Read-only."""
import time
from typing import Any, Dict, List

RANGES = {"7d": 7, "30d": 30, "90d": 90}
HOURLY_UPTO = 3 * 86400  # ranges up to 3 days are bucketed by hour


def parse_range(range_: str) -> int:
    """'5h', '24h', '3d', '30d' -> seconds. Unknown -> 30 days. Clamped to 1 hour .. 365 days."""
    import re
    m = re.fullmatch(r"\s*(\d+)\s*([hd])\s*", str(range_ or ""))
    if not m:
        return 30 * 86400
    n, unit = int(m.group(1)), m.group(2)
    secs = n * (3600 if unit == "h" else 86400)
    return max(3600, min(365 * 86400, secs))


def _label(secs: int) -> str:
    return f"{secs // 3600}h" if secs < 86400 or secs % 86400 else f"{secs // 86400}d"
FINAL_OK = {"completed"}
FINAL_BAD = {"failed", "error", "timeout"}
STOPPED = {"stopped", "rejected", "cancelled", "budget_stopped"}


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


def _workers() -> List[Dict[str, Any]]:
    try:
        from workers.outcomes import _all_workers
        return [w for w in _all_workers(include_archive=True) if isinstance(w, dict)]
    except Exception:
        return []


def _tokens(w: Dict[str, Any]) -> int:
    try:
        from workers.activity import _worker_tokens
        return int(_worker_tokens(w) or 0)
    except Exception:
        return 0


def landed_lines(days: int, hours: int = 0, hourly: bool = False) -> List[Dict[str, Any]]:
    """Per-day commits and lines added/deleted on this repo (read-only git log). Empty on any failure."""
    import os
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        since = f"{int(hours)} hours ago" if hours else f"{int(days)} days ago"
        datefmt = "--date=format:%Y-%m-%d %H:00" if hourly else "--date=short"
        out = subprocess.run(["git", "log", f"--since={since}", "--numstat", "--no-merges",
                              "--pretty=@@%ad", datefmt],
                             cwd=root, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    per: Dict[str, Dict[str, int]] = {}
    day = ""
    for line in out.splitlines():
        if line.startswith("@@"):
            day = line[2:].strip()
            per.setdefault(day, {"commits": 0, "added": 0, "deleted": 0})["commits"] += 1
            continue
        parts = line.split("\t")
        if day and len(parts) == 3:
            a, d = parts[0], parts[1]
            per[day]["added"] += int(a) if a.isdigit() else 0
            per[day]["deleted"] += int(d) if d.isdigit() else 0
    return [dict(date=k, **v) for k, v in sorted(per.items())]


def summary(range_: str = "30d") -> Dict[str, Any]:
    secs = parse_range(range_)
    days = max(1, secs // 86400)
    hourly = secs <= HOURLY_UPTO
    since = time.time() - secs
    bucket = "%Y-%m-%d %H:00" if hourly else "%Y-%m-%d"
    outcomes = {"completed": 0, "failed": 0, "stopped": 0, "running": 0, "rerouted": 0}
    by_provider: Dict[str, Dict[str, int]] = {}
    daily: Dict[str, Dict[str, int]] = {}
    for w in _workers():
        t = _ts(w.get("created_at"))
        if t < since:
            continue
        st = str(w.get("status") or "")
        if st in FINAL_OK:
            outcomes["completed"] += 1
        elif st in FINAL_BAD:
            outcomes["failed"] += 1
        elif st in STOPPED:
            outcomes["stopped"] += 1
        else:
            outcomes["running"] += 1
        if w.get("rerouted_from") or w.get("escalated_from"):
            outcomes["rerouted"] += 1
        tok = _tokens(w)
        prov = str(w.get("provider") or "unknown")
        p = by_provider.setdefault(prov, {"workers": 0, "tokens": 0})
        p["workers"] += 1
        p["tokens"] += tok
        day = time.strftime(bucket, time.localtime(t))
        d = daily.setdefault(day, {"workers": 0, "tokens": 0, "completed": 0, "failed": 0})
        d["workers"] += 1
        d["tokens"] += tok
        if st in FINAL_OK:
            d["completed"] += 1
        elif st in FINAL_BAD:
            d["failed"] += 1
    finished = outcomes["completed"] + outcomes["failed"] + outcomes["stopped"]
    return {
        "range": _label(secs),
        "bucket": "hour" if hourly else "day",
        "outcomes": outcomes,
        "success_rate": round(outcomes["completed"] / finished, 3) if finished else None,
        "by_provider": by_provider,
        "daily": [dict(date=k, **v) for k, v in sorted(daily.items())],
        "git": landed_lines(days, hours=secs // 3600 if hourly else 0, hourly=hourly),
    }
