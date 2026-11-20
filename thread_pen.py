"""Per-thread pen lease store for multi-device own-fleet sessions.

Stores exclusive write leases keyed ``thread:<id>`` in ``SUBWAY_DIR/thread_pens.json``
using ``_s._state_lock`` and ``_s._write_json_atomic`` so concurrent callers across
threads and processes cannot collide or leave partial JSON on disk.
"""

from __future__ import annotations

import math
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import server as _s

PEN_FILE = "thread_pens.json"
LOCK_NAME = "thread_pen"
DEFAULT_TTL = 120.0
MAX_TTL = 3600.0
_PREFIX = "thread:"

_INPROC_LOCK = threading.RLock()


def pen_path() -> str:
    """Return the path to the pen lease store inside the active SUBWAY_DIR."""
    return os.path.join(_s.SUBWAY_DIR, PEN_FILE)


def pen_key(thread_id: str) -> str:
    """Normalize a thread identifier to its canonical ``thread:<id>`` lease key."""
    if not isinstance(thread_id, str):
        raise ValueError("thread_id must be a non-empty string")
    raw = thread_id.strip()
    if raw.startswith(_PREFIX):
        raw = raw[len(_PREFIX):].strip()
    if not raw:
        raise ValueError("thread_id must be a non-empty string")
    return f"{_PREFIX}{raw}"


def _raw_thread_id(key: str) -> str:
    return key[len(_PREFIX):] if key.startswith(_PREFIX) else key


def _normalize_holder(holder: str) -> str:
    if not isinstance(holder, str):
        raise ValueError("holder must be a non-empty string")
    cleaned = holder.strip()
    if not cleaned:
        raise ValueError("holder must be a non-empty string")
    return cleaned


def _normalize_ttl(ttl: Any) -> float:
    if isinstance(ttl, bool):
        raise ValueError("ttl must be a positive number")
    try:
        val = float(ttl)
    except (TypeError, ValueError) as exc:
        raise ValueError("ttl must be a positive number") from exc
    if not math.isfinite(val) or val <= 0:
        raise ValueError("ttl must be a positive number")
    return min(val, MAX_TTL)


def _resolve_now(now: Optional[float]) -> float:
    if now is None:
        return time.time()
    if isinstance(now, bool):
        raise ValueError("now must be a finite timestamp")
    val = float(now)
    if not math.isfinite(val):
        raise ValueError("now must be a finite timestamp")
    return val


def _safe_ts(value: Any, fallback: float) -> float:
    try:
        val = float(value)
        return val if math.isfinite(val) else fallback
    except (TypeError, ValueError):
        return fallback


def _is_active(entry: Any, now: float) -> bool:
    if not isinstance(entry, dict):
        return False
    holder = entry.get("holder")
    if not isinstance(holder, str) or not holder.strip():
        return False
    try:
        exp = float(entry.get("expires_at", 0.0))
    except (TypeError, ValueError):
        return False
    return math.isfinite(exp) and exp > now


def _read_store_unlocked() -> Tuple[Dict[str, Dict[str, Any]], bool]:
    """Read raw pens dict from disk; fail open on missing or corrupt files."""
    data = _s._load_json(PEN_FILE)
    raw_pens = data.get("pens")
    if not isinstance(raw_pens, dict):
        return {}, bool(data)
    clean: Dict[str, Dict[str, Any]] = {}
    dirty = False
    for k, v in raw_pens.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            dirty = True
            continue
        clean[k] = dict(v)
    return clean, dirty


def _split_active_expired(
    pens: Dict[str, Dict[str, Any]],
    now: float,
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    active: Dict[str, Dict[str, Any]] = {}
    expired: List[Dict[str, Any]] = []
    for k, v in pens.items():
        if _is_active(v, now):
            active[k] = v
        else:
            record = dict(v) if isinstance(v, dict) else {"raw": v}
            record.setdefault("key", k)
            expired.append(record)
    return active, expired


def _write_store_unlocked(pens: Dict[str, Dict[str, Any]]) -> None:
    path = pen_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _s._write_json_atomic(path, {"pens": pens})


def _make_record(
    key: str,
    holder: str,
    now: float,
    ttl: float,
    *,
    acquired_at: Optional[float] = None,
    generation: int = 1,
    metadata: Optional[Dict[str, Any]] = None,
    previous_holder: Optional[str] = None,
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "key": key,
        "thread_id": _raw_thread_id(key),
        "holder": holder,
        "acquired_at": now if acquired_at is None else acquired_at,
        "renewed_at": now,
        "expires_at": now + ttl,
        "ttl": ttl,
        "generation": generation,
    }
    if metadata and isinstance(metadata, dict):
        safe_meta = {str(k): v for k, v in metadata.items() if str(k) not in record}
        if safe_meta:
            record["metadata"] = safe_meta
    if previous_holder:
        record["previous_holder"] = previous_holder
    return record


def _err(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "code": code, "error": message}
    out.update(extra)
    return out


def _validate_common(
    thread_id: str,
    holder: Optional[str],
    ttl: Optional[float],
    now: Optional[float],
) -> Tuple[Optional[Tuple[str, str, float, float]], Optional[Dict[str, Any]]]:
    try:
        key = pen_key(thread_id)
    except ValueError as exc:
        return None, _err("invalid_thread_id", str(exc))
    clean_holder = ""
    if holder is not None:
        try:
            clean_holder = _normalize_holder(holder)
        except ValueError as exc:
            return None, _err("invalid_holder", str(exc), key=key)
    clean_ttl = DEFAULT_TTL
    if ttl is not None:
        try:
            clean_ttl = _normalize_ttl(ttl)
        except ValueError as exc:
            return None, _err("invalid_ttl", str(exc), key=key)
    try:
        ts = _resolve_now(now)
    except ValueError as exc:
        return None, _err("invalid_time", str(exc), key=key)
    return (key, clean_holder, clean_ttl, ts), None


def acquire(
    thread_id: str,
    holder: str,
    ttl: float = DEFAULT_TTL,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Acquire or re-acquire the exclusive pen lease for ``thread:<id>``."""
    parsed, err = _validate_common(thread_id, holder, ttl, now)
    if err is not None or parsed is None:
        return err or _err("invalid_args", "Invalid arguments")
    key, clean_holder, clean_ttl, ts = parsed

    with _INPROC_LOCK, _s._state_lock(LOCK_NAME):
        raw, dirty = _read_store_unlocked()
        active, expired = _split_active_expired(raw, ts)
        existing = active.get(key)

        if existing is not None:
            current_holder = existing["holder"]
            if current_holder != clean_holder:
                if expired or dirty:
                    _write_store_unlocked(active)
                return _err(
                    "held",
                    f"Pen for {key} is already held by {current_holder}",
                    acquired=False,
                    renewed=False,
                    key=key,
                    holder=current_holder,
                    expires_at=existing["expires_at"],
                    pen=dict(existing),
                )
            gen = int(existing.get("generation") or 1) + 1
            merged_meta = metadata if metadata is not None else existing.get("metadata")
            record = _make_record(
                key,
                clean_holder,
                ts,
                clean_ttl,
                acquired_at=_safe_ts(existing.get("acquired_at"), ts),
                generation=gen,
                metadata=merged_meta if isinstance(merged_meta, dict) else None,
            )
            active[key] = record
            _write_store_unlocked(active)
            return {"ok": True, "acquired": False, "renewed": True, "key": key, "pen": dict(record)}

        record = _make_record(key, clean_holder, ts, clean_ttl, metadata=metadata)
        active[key] = record
        _write_store_unlocked(active)
        return {"ok": True, "acquired": True, "renewed": False, "key": key, "pen": dict(record)}


def renew(
    thread_id: str,
    holder: str,
    ttl: float = DEFAULT_TTL,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Extend an active pen lease held by ``holder``."""
    parsed, err = _validate_common(thread_id, holder, ttl, now)
    if err is not None or parsed is None:
        return err or _err("invalid_args", "Invalid arguments")
    key, clean_holder, clean_ttl, ts = parsed

    with _INPROC_LOCK, _s._state_lock(LOCK_NAME):
        raw, dirty = _read_store_unlocked()
        active, expired = _split_active_expired(raw, ts)
        existing = active.get(key)
        if existing is None:
            if expired or dirty:
                _write_store_unlocked(active)
            return _err("not_held", f"No active pen lease for {key}", renewed=False, key=key, pen=None)
        current_holder = existing["holder"]
        if current_holder != clean_holder:
            if expired or dirty:
                _write_store_unlocked(active)
            return _err(
                "not_holder",
                f"Pen for {key} is held by {current_holder}, not {clean_holder}",
                renewed=False,
                key=key,
                holder=current_holder,
                pen=dict(existing),
            )
        gen = int(existing.get("generation") or 1) + 1
        existing_meta = existing.get("metadata")
        record = _make_record(
            key,
            clean_holder,
            ts,
            clean_ttl,
            acquired_at=_safe_ts(existing.get("acquired_at"), ts),
            generation=gen,
            metadata=existing_meta if isinstance(existing_meta, dict) else None,
            previous_holder=existing.get("previous_holder"),
        )
        active[key] = record
        _write_store_unlocked(active)
        return {"ok": True, "renewed": True, "key": key, "pen": dict(record)}


def release(
    thread_id: str,
    holder: str = "",
    *,
    force: bool = False,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Release an active pen lease; idempotent when unheld or already expired."""
    parsed, err = _validate_common(thread_id, None if force else holder, None, now)
    if err is not None or parsed is None:
        return err or _err("invalid_args", "Invalid arguments")
    key, clean_holder, _, ts = parsed

    with _INPROC_LOCK, _s._state_lock(LOCK_NAME):
        raw, dirty = _read_store_unlocked()
        active, expired = _split_active_expired(raw, ts)
        existing = active.get(key)
        if existing is None:
            if expired or dirty:
                _write_store_unlocked(active)
            return {"ok": True, "released": False, "key": key, "pen": None}
        current_holder = existing["holder"]
        if not force and current_holder != clean_holder:
            if expired or dirty:
                _write_store_unlocked(active)
            return _err(
                "not_holder",
                f"Pen for {key} is held by {current_holder}, not {clean_holder}",
                released=False,
                key=key,
                holder=current_holder,
                pen=dict(existing),
            )
        previous = dict(active.pop(key))
        _write_store_unlocked(active)
        return {"ok": True, "released": True, "key": key, "pen": None, "previous": previous}


def handover(
    thread_id: str,
    from_holder: str,
    to_holder: str,
    ttl: float = DEFAULT_TTL,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Atomically transfer an active pen lease from ``from_holder`` to ``to_holder``."""
    parsed, err = _validate_common(thread_id, from_holder, ttl, now)
    if err is not None or parsed is None:
        return err or _err("invalid_args", "Invalid arguments")
    key, clean_from, clean_ttl, ts = parsed
    try:
        clean_to = _normalize_holder(to_holder)
    except ValueError as exc:
        return _err("invalid_holder", str(exc), key=key)

    with _INPROC_LOCK, _s._state_lock(LOCK_NAME):
        raw, dirty = _read_store_unlocked()
        active, expired = _split_active_expired(raw, ts)
        existing = active.get(key)
        if existing is None:
            if expired or dirty:
                _write_store_unlocked(active)
            return _err("not_held", f"No active pen lease for {key}", handed_over=False, key=key, pen=None)
        current_holder = existing["holder"]
        if current_holder != clean_from:
            if expired or dirty:
                _write_store_unlocked(active)
            return _err(
                "not_holder",
                f"Pen for {key} is held by {current_holder}, not {clean_from}",
                handed_over=False,
                key=key,
                holder=current_holder,
                pen=dict(existing),
            )
        gen = int(existing.get("generation") or 1) + 1
        existing_meta = existing.get("metadata")
        switched = clean_to != clean_from
        record = _make_record(
            key,
            clean_to,
            ts,
            clean_ttl,
            acquired_at=ts if switched else _safe_ts(existing.get("acquired_at"), ts),
            generation=gen,
            metadata=existing_meta if isinstance(existing_meta, dict) else None,
            previous_holder=clean_from if switched else existing.get("previous_holder"),
        )
        active[key] = record
        _write_store_unlocked(active)
        return {"ok": True, "handed_over": switched, "key": key, "pen": dict(record)}


def expire(*, now: Optional[float] = None) -> Dict[str, Any]:
    """Prune all expired pen leases from disk and return a summary."""
    ts = _resolve_now(now)
    with _INPROC_LOCK, _s._state_lock(LOCK_NAME):
        raw, dirty = _read_store_unlocked()
        active, expired = _split_active_expired(raw, ts)
        if expired or dirty:
            _write_store_unlocked(active)
        return {"ok": True, "expired": expired, "count": len(expired), "active_count": len(active)}


def get_pen(thread_id: str, *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Return the active lease dict for ``thread:<id>``, or None if unheld or expired."""
    key = pen_key(thread_id)
    ts = _resolve_now(now)
    with _INPROC_LOCK, _s._state_lock(LOCK_NAME):
        raw, dirty = _read_store_unlocked()
        active, expired = _split_active_expired(raw, ts)
        if expired or dirty:
            _write_store_unlocked(active)
        entry = active.get(key)
        return dict(entry) if entry is not None else None


def list_pens(*, holder: Optional[str] = None, now: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """Return active pen leases keyed by ``thread:<id>``, optionally filtered by holder."""
    ts = _resolve_now(now)
    filter_holder = holder.strip() if isinstance(holder, str) and holder.strip() else None
    with _INPROC_LOCK, _s._state_lock(LOCK_NAME):
        raw, dirty = _read_store_unlocked()
        active, expired = _split_active_expired(raw, ts)
        if expired or dirty:
            _write_store_unlocked(active)
        if filter_holder is None:
            return {k: dict(v) for k, v in active.items()}
        return {k: dict(v) for k, v in active.items() if v.get("holder") == filter_holder}
