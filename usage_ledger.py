"""Usage ledger: per-worker cost attribution ("requested by" / "paid by").

Append-only JSONL next to the rest of Subway's state:
``<SUBWAY_DIR>/usage_ledger.jsonl``. Every line is a self-contained record so
the HUD can summarise spend per sender, payer and provider without loading
worker state. Corrupt or oversized lines never break a summary.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

LEDGER_NAME = "usage_ledger.jsonl"
MAX_LINE = 2048

_FIELDS = ("worker_id", "requested_by", "paid_by", "provider", "model",
           "station", "session_id", "mode")


def _subway_dir() -> Path:
    """Resolve SUBWAY_DIR the same way the rest of Subway does.

    Env var wins (tests point it at tmp_path); otherwise reuse peers.SUBWAY_DIR
    if already imported, else fall back to ``~/.subway``.
    """
    env = os.environ.get("SUBWAY_DIR")
    if env:
        return Path(env).expanduser()
    try:
        import subway_home

        return Path(str(subway_home.subway_dir())).expanduser()
    except Exception:
        return Path.home() / ".subway"


def ledger_path() -> Path:
    return _subway_dir() / LEDGER_NAME


def _line(rec: Dict[str, Any]) -> str:
    """Serialise to a single JSON line of at most MAX_LINE bytes."""
    raw = json.dumps(rec, separators=(",", ":"), ensure_ascii=False)
    if len(raw.encode("utf-8")) <= MAX_LINE:
        return raw
    for key in ("model", "station", "session_id", "mode", "requested_by",
                "paid_by", "worker_id"):
        rec[key] = str(rec.get(key) or "")[:40]
        raw = json.dumps(rec, separators=(",", ":"), ensure_ascii=False)
        if len(raw.encode("utf-8")) <= MAX_LINE:
            return raw
    minimal = {"ts": rec.get("ts"), "worker_id": str(rec.get("worker_id") or "")[:40],
               "requested_by": "", "paid_by": "", "provider": str(rec.get("provider") or "")[:40],
               "model": "", "tokens": rec.get("tokens") or 0, "station": "",
               "session_id": "", "mode": ""}
    return json.dumps(minimal, separators=(",", ":"), ensure_ascii=False)


def record(worker_id: str, requested_by: str, paid_by: str, provider: str,
           model: str, tokens: int, station: str, session_id: str = "",
           mode: str = "") -> Dict[str, Any]:
    """Append one usage record. Callers should still guard with try/except."""
    try:
        tok = max(0, int(tokens or 0))
    except (TypeError, ValueError):
        tok = 0
    rec: Dict[str, Any] = {
        "ts": round(time.time(), 3),
        "worker_id": str(worker_id or "")[:120],
        "requested_by": str(requested_by or "")[:120],
        "paid_by": str(paid_by or "")[:120],
        "provider": str(provider or "")[:60],
        "model": str(model or "")[:80],
        "tokens": tok,
        "station": str(station or "")[:80],
        "session_id": str(session_id or "")[:80],
        "mode": str(mode or "")[:16],
    }
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (_line(rec) + "\n").encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return rec


def summary(since_ts: float = 0, session_id: str = "") -> Dict[str, Any]:
    """Totals grouped by (requested_by, paid_by, provider).

    Unreadable or malformed lines are skipped; the ledger is never rewritten.
    """
    groups: Dict[tuple, Dict[str, Any]] = {}
    total = 0
    count = 0
    path = ledger_path()
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except FileNotFoundError:
        fh = None
    except OSError:
        fh = None
    if fh is not None:
        with fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(rec, dict):
                    continue
                try:
                    tok = int(rec.get("tokens") or 0)
                except (TypeError, ValueError):
                    continue
                if tok < 0:
                    continue
                try:
                    ts = float(rec.get("ts") or 0)
                except (TypeError, ValueError):
                    ts = 0.0
                if ts < float(since_ts or 0):
                    continue
                if session_id and str(rec.get("session_id") or "") != session_id:
                    continue
                key = (str(rec.get("requested_by") or ""), str(rec.get("paid_by") or ""),
                       str(rec.get("provider") or ""))
                g = groups.get(key)
                if g is None:
                    g = groups[key] = {"requested_by": key[0], "paid_by": key[1],
                                       "provider": key[2], "tokens": 0, "count": 0}
                g["tokens"] += tok
                g["count"] += 1
                total += tok
                count += 1
    out: List[Dict[str, Any]] = sorted(groups.values(), key=lambda g: (-g["tokens"], g["requested_by"]))
    return {"since": float(since_ts or 0), "session_id": session_id or "",
            "total_tokens": total, "count": count, "groups": out}
