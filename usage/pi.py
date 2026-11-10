"""Pi/DeepAI usage tracking."""
from __future__ import annotations

import datetime
import glob
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

try:
    import server as _s
except Exception:
    _s = sys.modules.get("server")


def _pi_session_files() -> List[str]:
    files = []
    try:
        home = getattr(_s, "HOME", None) or os.path.expanduser("~")
    except Exception:
        home = os.path.expanduser("~")
    patterns = [
        os.path.expanduser("~/.deepai-code/userdata/*/pi/*/sessions/**/*.jsonl"),
        os.path.join(home, ".deepai-code", "userdata", "*", "pi", "*", "sessions", "**", "*.jsonl"),
        os.path.join(home, ".pi", "agent", "sessions", "**", "*.jsonl"),
    ]
    try:
        pi_dir = getattr(_s, "PI_DIR", None)
        if pi_dir:
            patterns.append(os.path.join(pi_dir, "sessions", "**", "*.jsonl"))
    except Exception:
        pass
    for pattern in patterns:
        try:
            files.extend(glob.glob(pattern, recursive=True))
        except Exception:
            continue
    seen = set()
    result = []
    for f in files:
        if f in seen:
            continue
        seen.add(f)
        if os.path.isfile(f):
            result.append(f)
    return result


def _load_pi_state() -> Dict[str, Any]:
    try:
        path = os.path.join(_s.SUBWAY_DIR, "pi_usage_offsets.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}


def _save_pi_state(state: Dict[str, Any]) -> None:
    try:
        _s._ensure_subway_dir()
        path = os.path.join(_s.SUBWAY_DIR, "pi_usage_offsets.json")
        try:
            with _s._state_lock("pi_usage"):
                try:
                    _s._write_json_atomic(path, state)
                except Exception:
                    import tempfile

                    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            json.dump(state, f, indent=2)
                        os.replace(tmp, path)
                    except Exception:
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass
                        raise
        except Exception:
            pass
    except Exception:
        pass


def _pi_usage(window_seconds: int = 18000) -> Dict[str, Any]:
    now = time.time()
    window_seconds = int(window_seconds)
    seven_days_ago = now - 7 * 86400

    state = _load_pi_state()
    if not isinstance(state.get("events"), list):
        state["events"] = []
    if not isinstance(state.get("offsets"), dict):
        state["offsets"] = {}

    files = _pi_session_files()

    sessions_active = 0
    for path in files:
        try:
            st = os.stat(path)
            if st.st_mtime >= seven_days_ago:
                sessions_active += 1
        except Exception:
            continue

    for path in files:
        try:
            st = os.stat(path)
            if st.st_mtime < seven_days_ago:
                continue
        except Exception:
            continue

        try:
            file_size = os.path.getsize(path)
        except Exception:
            continue

        file_key = os.path.abspath(path)
        offset = int(state["offsets"].get(file_key, 0))
        if file_size < offset:
            offset = 0

        new_offset = offset
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                while True:
                    pos = f.tell()
                    raw = f.readline()
                    if not raw:
                        break
                    if not raw.endswith(b"\n"):
                        break
                    new_offset = pos + len(raw)
                    try:
                        line = raw.decode("utf-8", errors="replace")
                    except Exception:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    usage = None
                    model = None
                    msg = None
                    if isinstance(event, dict):
                        if isinstance(event.get("usage"), dict):
                            usage = event.get("usage")
                        msg = event.get("message")
                        if isinstance(msg, dict):
                            if isinstance(msg.get("usage"), dict) and usage is None:
                                usage = msg.get("usage")
                            if not model and msg.get("model"):
                                model = msg.get("model")
                        if not model and event.get("model"):
                            model = event.get("model")
                    if not usage or not isinstance(usage, dict):
                        continue
                    ts = None
                    for key in ("timestamp", "time", "ts", "created_at", "createdAt"):
                        if isinstance(event.get(key), (int, float)):
                            ts = float(event[key])
                            break
                        elif isinstance(event.get(key), str):
                            try:
                                ts = _s.sort_key_ts(event[key])
                                break
                            except Exception:
                                pass
                    if ts is None and isinstance(msg, dict):
                        for key in ("timestamp", "time", "ts", "created_at"):
                            if isinstance(msg.get(key), (int, float)):
                                ts = float(msg[key])
                                break
                    if ts is not None and ts > 1e11:
                        ts = ts / 1000.0
                    inp = int(usage.get("input") or usage.get("inputTokens") or usage.get("promptTokens") or 0)
                    out = int(usage.get("output") or usage.get("outputTokens") or usage.get("completionTokens") or 0)
                    cache_read = int(usage.get("cacheRead") or usage.get("cache_read") or usage.get("cacheReadTokens") or usage.get("cache_read_input_tokens") or 0)
                    reasoning = int(usage.get("reasoning") or usage.get("reasoningTokens") or 0)
                    state["events"].append({
                        "ts": ts,
                        "model": str(model) if model else None,
                        "inp": inp,
                        "out": out,
                        "cache_read": cache_read,
                        "reasoning": reasoning,
                    })
        except Exception:
            continue
        state["offsets"][file_key] = new_offset

    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_reasoning = 0
    per_model: Dict[str, Dict[str, int]] = {}
    # Same shape the other providers report, so DeepAI can sit on the spend chart
    # and in the 5h/7d token table beside them.
    hourly = [0] * 24
    week = {"input": 0, "output": 0, "cache_read": 0, "total": 0}
    seven_cutoff = now - 7 * 86400
    for ev in state["events"]:
        ts = ev.get("ts")
        if ts is not None:
            age = now - ts
            if 0 <= age < 24 * 3600:
                hourly[23 - min(23, int(age // 3600))] += int(ev.get("inp") or 0) + int(ev.get("out") or 0)
            if ts >= seven_cutoff:
                week["input"] += int(ev.get("inp") or 0)
                week["output"] += int(ev.get("out") or 0)
                week["cache_read"] += int(ev.get("cache_read") or 0)
                week["total"] += (int(ev.get("inp") or 0) + int(ev.get("out") or 0)
                                  + int(ev.get("cache_read") or 0) + int(ev.get("reasoning") or 0))
            if ts < now - window_seconds:
                continue
        inp = int(ev.get("inp") or 0)
        out = int(ev.get("out") or 0)
        cache_read = int(ev.get("cache_read") or 0)
        reasoning = int(ev.get("reasoning") or 0)
        total_input += inp
        total_output += out
        total_cache_read += cache_read
        total_reasoning += reasoning
        model_id = ev.get("model")
        if model_id:
            if model_id not in per_model:
                per_model[model_id] = {"input": 0, "output": 0, "total": 0}
            per_model[model_id]["input"] += inp
            per_model[model_id]["output"] += out
            per_model[model_id]["total"] += inp + out + cache_read + reasoning

    _save_pi_state(state)

    result = {
        "input": total_input,
        "output": total_output,
        "cache_read": total_cache_read,
        "reasoning": total_reasoning,
        "total": total_input + total_output + total_cache_read + total_reasoning,
        "per_model": per_model,
        "sessions_active": sessions_active,
        "hourly": hourly,
        "5h": {"input": total_input, "output": total_output, "cache_read": total_cache_read,
               "total": total_input + total_output + total_cache_read + total_reasoning},
        "7d": week,
    }
    return result


def _pi_limits() -> Dict[str, Any]:
    return {"plan": "subscription", "windows": [], "source": "none"}


# DeepAI's real allowance is Theos: a daily pot, a published per-request price per
# model, and a 403 once it's empty. Nothing is served over an API, so everything
# below is read off disk: prices from DeepAI's own model catalog, requests and the
# exhaustion notice from the session transcripts.
_THEOS_REFRESH_RE = re.compile(r"refresh at ([0-9T:.+\-]+Z?)")


def _theos_catalog_file() -> Optional[str]:
    """DeepAI's model catalog, if this machine has one."""
    try:
        home = getattr(_s, "HOME", None) or os.path.expanduser("~")
    except Exception:
        home = os.path.expanduser("~")
    candidates = [os.path.join(home, ".deepai-code", "userdata", "deepai", "pi", "deepai", "models.json")]
    try:
        candidates.extend(sorted(glob.glob(os.path.join(home, ".deepai-code", "userdata", "*", "pi", "*", "models.json"))))
    except Exception:
        pass
    for path in candidates:
        try:
            if os.path.isfile(path):
                return path
        except Exception:
            continue
    return None


def _theos_catalog() -> Dict[str, Dict[str, Any]]:
    """{model id: {"name", "cost"}} from the catalog. cost is None when DeepAI
    publishes no price for that model, never a guessed zero."""
    path = _theos_catalog_file()
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        return {}
    models = None
    try:
        models = doc["providers"]["deepai"]["models"]
    except Exception:
        models = None
    if not isinstance(models, list):
        return {}
    catalog: Dict[str, Dict[str, Any]] = {}
    for entry in models:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not model_id:
            continue
        raw = entry.get("theosPerRequest")
        cost: Optional[int] = None
        if raw is not None:
            try:
                cost = int(str(raw).strip())
            except Exception:
                cost = None
        catalog[str(model_id)] = {"name": str(entry.get("name") or model_id), "cost": cost}
    return catalog


def _theos_refresh_epoch(text: str) -> Optional[float]:
    """Epoch for the ISO stamp in "They refresh at 2026-11-07T11:53:17+00:00."."""
    stamp = (text or "").strip().rstrip(".")
    if not stamp:
        return None
    if stamp.endswith("Z"):
        stamp = stamp[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(stamp)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    try:
        return parsed.timestamp()
    except Exception:
        return None


def _theos_local_midnight(now: float) -> float:
    try:
        lt = time.localtime(now)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    except Exception:
        return now - 86400


@_s.ttl_cached(60.0)
def _theos() -> Dict[str, Any]:
    """Today's Theos spend, measured. No catalog means no numbers, not zeroes.
    Cached for a minute: it re-reads every session file touched today."""
    now = time.time()
    catalog = _theos_catalog()
    if not catalog:
        return {"models": [], "note": "catalog unavailable", "computed_at": now}

    midnight = _theos_local_midnight(now)
    counts: Dict[str, int] = {}
    refreshes: List[float] = []

    for path in _pi_session_files():
        try:
            if os.stat(path).st_mtime < midnight:
                continue
        except Exception:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    if "theos_exhausted" in raw or "theos_insufficient" in raw:
                        for stamp in _THEOS_REFRESH_RE.findall(raw):
                            epoch = _theos_refresh_epoch(stamp)
                            if epoch is not None:
                                refreshes.append(epoch)
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(event, dict):
                        continue
                    # Same two shapes _pi_usage reads: usage/model at the top level,
                    # or nested under "message" (what pi actually writes today).
                    usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
                    model = event.get("model")
                    msg = event.get("message")
                    if isinstance(msg, dict):
                        if usage is None and isinstance(msg.get("usage"), dict):
                            usage = msg.get("usage")
                        if not model:
                            model = msg.get("model")
                    if not isinstance(usage, dict) or not model:
                        continue
                    model_id = str(model)
                    if model_id not in catalog and "/" in model_id:
                        bare = model_id.rsplit("/", 1)[-1]  # "deepai/glm-5.3" -> "glm-5.3"
                        if bare in catalog:
                            model_id = bare
                    counts[model_id] = counts.get(model_id, 0) + 1
        except Exception:
            continue

    spend_today = 0
    for model_id, count in counts.items():
        cost = (catalog.get(model_id) or {}).get("cost")
        if isinstance(cost, int):
            spend_today += cost * count

    models = [{"id": model_id, "name": meta.get("name") or model_id,
               "cost": meta.get("cost"), "requests": counts.get(model_id, 0)}
              for model_id, meta in catalog.items()]
    models.sort(key=lambda m: (-(m["requests"] or 0), -(m["cost"] or 0), m["id"]))

    # A file touched today can still hold yesterday's notice, and assistant text
    # can quote a theos error verbatim. Only a refresh still ahead of us means
    # the pot is empty right now.
    refresh_at = max(refreshes) if refreshes else None
    exhausted = bool(refresh_at is not None and refresh_at > now)

    return {"models": models, "spend_today": int(spend_today), "exhausted": exhausted,
            "refresh_at": refresh_at, "computed_at": now}
