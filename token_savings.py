"""Token-savings counter: what Subway sent instead of the whole files (code packets, ~skel, read_symbol, read_outline).

Tokens are estimated as chars / 4. Daily buckets in <SUBWAY_DIR>/token_savings.json, kept 90 days.
"""
import json
import os
import threading
import time
from typing import Any, Dict, Iterable

KINDS = ("packet", "skel", "read_symbol", "read_outline")
KEEP_DAYS = 90
_LOCK = threading.Lock()


def _path() -> str:
    try:
        import subway_home
        base = subway_home.subway_dir()
    except Exception:
        base = os.path.expanduser("~/.subway")
    return os.path.join(base, "token_savings.json")


def _load() -> Dict[str, Any]:
    try:
        with open(_path()) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def file_chars(paths: Iterable[str]) -> int:
    total = 0
    for p in set(paths):
        try:
            total += os.path.getsize(p)
        except OSError:
            pass
    return total


def record(kind: str, raw_chars: int, sent_chars: int) -> None:
    """Count one use. raw = what reading the whole file(s) would cost; sent = what was actually sent."""
    if kind not in KINDS:
        return
    raw, sent = max(0, int(raw_chars or 0)), max(0, int(sent_chars or 0))
    day = time.strftime("%Y-%m-%d")
    with _LOCK:
        d = _load()
        days = d.setdefault("days", {})
        b = days.setdefault(day, {}).setdefault(kind, {"uses": 0, "raw_chars": 0, "sent_chars": 0})
        b["uses"] += 1
        b["raw_chars"] += raw
        b["sent_chars"] += sent
        for old in sorted(days)[:-KEEP_DAYS]:
            days.pop(old, None)
        p = _path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, p)


def summary(days: int = 1) -> Dict[str, Any]:
    """Totals for the last `days` days (1 = today), per kind and overall, in estimated tokens."""
    all_days = _load().get("days", {})
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - (max(1, int(days)) - 1) * 86400))
    per: Dict[str, Dict[str, int]] = {}
    for day, kinds in all_days.items():
        if day < cutoff or not isinstance(kinds, dict):
            continue
        for k, b in kinds.items():
            t = per.setdefault(k, {"uses": 0, "raw_chars": 0, "sent_chars": 0})
            for f in t:
                t[f] += int(b.get(f) or 0)
    out: Dict[str, Any] = {"days": max(1, int(days)), "kinds": {}}
    raw_all = sent_all = 0
    for k, t in per.items():
        raw_all += t["raw_chars"]
        sent_all += t["sent_chars"]
        out["kinds"][k] = {"uses": t["uses"], "raw_tokens": t["raw_chars"] // 4, "sent_tokens": t["sent_chars"] // 4,
                           "saved_tokens": max(0, t["raw_chars"] - t["sent_chars"]) // 4}
    out["raw_tokens"] = raw_all // 4
    out["sent_tokens"] = sent_all // 4
    out["saved_tokens"] = max(0, raw_all - sent_all) // 4
    out["saved_pct"] = round(100.0 * (raw_all - sent_all) / raw_all, 1) if raw_all else 0.0
    series = []
    for i in range(out["days"] - 1, -1, -1):
        day = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
        kinds = all_days.get(day)
        kinds = kinds if isinstance(kinds, dict) else {}
        raw = sum(int((b or {}).get("raw_chars") or 0) for b in kinds.values())
        sent = sum(int((b or {}).get("sent_chars") or 0) for b in kinds.values())
        series.append({"day": day, "sent_tokens": sent // 4, "saved_tokens": max(0, raw - sent) // 4})
    out["series"] = series
    return out
