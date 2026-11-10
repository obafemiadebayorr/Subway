"""Pi/DeepAI usage tracking."""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from typing import Any, Dict, List

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
    for ev in state["events"]:
        ts = ev.get("ts")
        if ts is not None:
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
    }
    return result


def _pi_limits() -> Dict[str, Any]:
    return {"plan": "subscription", "windows": [], "source": "none"}
