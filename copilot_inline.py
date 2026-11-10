import os
import sys
import time
import json
import re
import secrets
import contextlib
from typing import Dict, Any, Optional, Tuple, List

import store
import server as _s

DEFAULT_MONTHLY_CAP = 100
DEFAULT_PER_WORKER_CAP = 5
DEFAULT_ALLOWANCE_FLOOR_PCT = 0.0
REQUESTS_PER_CALL = 3
MAX_FILL_CHARS = 4000

REVIEWER_KEYWORDS = ["review", "reviewer", "research", "researcher", "read-only", "readonly"]
PLANNER_KEYWORDS = ["plan", "planner", "planning", "architect", "architecture"]


def _parse_version(v: str) -> Tuple[int, ...]:
    try:
        return tuple(int(p) for p in re.sub(r"[^0-9.]", "", str(v or "0.0.0")).split(".") if p)
    except Exception:
        return (0, 0, 0)


def is_version_ge(v1: str, v2: str) -> bool:
    return _parse_version(v1) >= _parse_version(v2)


def get_inline_caps(base_dir: str) -> Tuple[int, int]:
    cfg = {}
    cfg_path = os.path.join(base_dir, "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        pass
    sub = cfg.get("copilot_inline") if isinstance(cfg, dict) else {}
    if not isinstance(sub, dict):
        sub = {}
    m_cap = sub.get("monthly_cap") or cfg.get("copilot_inline_monthly_cap") or DEFAULT_MONTHLY_CAP
    w_cap = sub.get("per_worker_cap") or cfg.get("copilot_inline_per_worker_cap") or DEFAULT_PER_WORKER_CAP
    try:
        m_cap = int(m_cap)
    except Exception:
        m_cap = DEFAULT_MONTHLY_CAP
    try:
        w_cap = int(w_cap)
    except Exception:
        w_cap = DEFAULT_PER_WORKER_CAP
    return m_cap, w_cap


def get_inline_floor_pct(base_dir: str) -> float:
    cfg = {}
    cfg_path = os.path.join(base_dir, "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        pass
    sub = cfg.get("copilot_inline") if isinstance(cfg, dict) else {}
    if not isinstance(sub, dict):
        sub = {}
    val = sub.get("floor_pct") if "floor_pct" in sub else (cfg.get("copilot_inline_floor_pct") if "copilot_inline_floor_pct" in cfg else None)
    if val is None:
        val = sub.get("allowance_floor_pct") if "allowance_floor_pct" in sub else cfg.get("copilot_inline_allowance_floor_pct")
    if val is None:
        return DEFAULT_ALLOWANCE_FLOOR_PCT
    try:
        fval = float(val)
        if 0.0 <= fval <= 100.0:
            return fval
    except Exception:
        pass
    return DEFAULT_ALLOWANCE_FLOOR_PCT


def load_inline_usage(base_dir: str) -> Dict[str, Any]:
    path = os.path.join(base_dir, "copilot_inline_usage.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"period": time.strftime("%Y-%m"), "monthly_calls": 0, "workers": {}, "history": []}


def record_inline_call(base_dir: str, worker_id: str, count: int = 1) -> Dict[str, Any]:
    with _s._state_lock("copilot_inline"):
        cur_period = time.strftime("%Y-%m")
        path = os.path.join(base_dir, "copilot_inline_usage.json")
        tmp_path = os.path.join(base_dir, f".copilot_inline_usage_{os.getpid()}_{time.time_ns()}.tmp")
        data = load_inline_usage(base_dir)
        if data.get("period") != cur_period:
            data["period"] = cur_period
            data["monthly_calls"] = 0
            data["workers"] = {}

        data["monthly_calls"] = int(data.get("monthly_calls") or 0) + count
        w_map = data.setdefault("workers", {})
        wid = str(worker_id or "unknown")
        w_map[wid] = int(w_map.get(wid) or 0) + count
        history = data.setdefault("history", [])
        history.append({
            "at": time.time(),
            "worker_id": wid,
            "count": count,
            "requests": count * REQUESTS_PER_CALL,
        })
        data["history"] = history[-200:]
        try:
            os.makedirs(base_dir, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)
        except Exception:
            with contextlib.suppress(Exception):
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
        return data


def _get_worker_meta(base_dir: str, worker_id: str) -> Optional[Dict[str, Any]]:
    if not worker_id:
        return None
    # Check running / saved workers
    meta = None
    try:
        if hasattr(_s, "_worker_record"):
            meta = _s._worker_record(worker_id)
    except Exception:
        pass
    if not meta:
        wpath = os.path.join(base_dir, "workers", f"{worker_id}.json")
        try:
            with open(wpath, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass
    return meta if isinstance(meta, dict) else None


def check_inline_eligibility(worker_id: str, prompt: str = "", filepath: str = "",
                             prefix: str = "", suffix: str = "", base_dir: str = "") -> Tuple[bool, str]:
    if not worker_id:
        return False, "worker_id is required: only implementer workers may call inline Copilot."

    meta = _get_worker_meta(base_dir, worker_id)
    role = str(meta.get("role") or "").lower() if meta else ""
    worker_prompt = str(meta.get("prompt") or "").lower() if meta else ""

    # Must be implementer, never reviewer or planner
    if meta and (bool(meta.get("is_reviewer")) or bool(meta.get("verdict"))):
        return False, "Inline completions forbidden: worker is a reviewer."
    for kw in REVIEWER_KEYWORDS:
        if kw in role or (worker_prompt and kw in worker_prompt[:200]):
            return False, f"Inline completions forbidden for reviewers ({kw})."
    for kw in PLANNER_KEYWORDS:
        if kw in role or (worker_prompt and kw in worker_prompt[:200]):
            return False, f"Inline completions forbidden for planners ({kw})."

    # Small single-file fill check
    if filepath:
        if "," in filepath or "\n" in filepath or "*" in filepath or "?" in filepath:
            return False, "Inline completions are only for single-file fills."
    total_len = len(prompt or "") + len(prefix or "") + len(suffix or "")
    if total_len > MAX_FILL_CHARS:
        return False, f"Fill request too large ({total_len} chars > {MAX_FILL_CHARS} max): only small fills are allowed."

    # Caps check
    m_cap, w_cap = get_inline_caps(base_dir)
    usage = load_inline_usage(base_dir)
    cur_period = time.strftime("%Y-%m")
    monthly_calls = usage.get("monthly_calls", 0) if usage.get("period") == cur_period else 0
    if monthly_calls >= m_cap:
        return False, f"Monthly inline Copilot cap reached ({monthly_calls}/{m_cap})."
    w_calls = usage.get("workers", {}).get(worker_id, 0) if usage.get("period") == cur_period else 0
    if w_calls >= w_cap:
        return False, f"Per-worker inline Copilot cap reached ({w_calls}/{w_cap}) for worker {worker_id}."

    # Copilot completions allowance floor check (>= floor_pct when configured > 0)
    floor_pct = get_inline_floor_pct(base_dir)
    if floor_pct > 0.0:
        try:
            raw = _s._copilot_raw() if hasattr(_s, "_copilot_raw") else None
            if raw and isinstance(raw, dict):
                snaps = raw.get("quota_snapshots") or {}
                comp = snaps.get("completions") or {}
                pct = comp.get("percent_remaining")
                if pct is not None and pct < floor_pct:
                    return False, f"Copilot completions allowance ({pct}%) is below the {floor_pct}% floor."
        except Exception:
            pass

    return True, ""


def find_active_vscode_window(base_dir: str = "", max_age_seconds: float = 120.0) -> Optional[Dict[str, Any]]:
    ed_dir = os.path.join(base_dir, "editor_windows")
    if not os.path.isdir(ed_dir):
        return None
    now = time.time()
    candidates = []
    try:
        for fname in os.listdir(ed_dir):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(ed_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    wdata = json.load(f)
                if not isinstance(wdata, dict):
                    continue
                v = str(wdata.get("version") or "")
                if not is_version_ge(v, "0.1.3"):
                    continue
                if not wdata.get("probe"):
                    continue
                # check heartbeat
                at_str = wdata.get("at") or ""
                # parse ISO or time
                ts = 0.0
                if at_str:
                    try:
                        import datetime
                        # Handle ISO Z
                        ts = datetime.datetime.fromisoformat(at_str.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        ts = 0.0
                if ts and (now - ts) > max_age_seconds:
                    # Stale window
                    continue
                candidates.append((ts, wdata))
            except Exception:
                continue
    except Exception:
        pass
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def fallback_to_worker_model(worker_id: str, prompt: str, prefix: str = "", suffix: str = "",
                             filepath: str = "", meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    prov = (meta.get("provider") if meta else "") or "unknown"
    mod = (meta.get("model") if meta else "") or "default"
    # Generate clean text for small fill
    fill_text = f"# Fill by {prov}/{mod} for {prompt}"
    return {
        "status": "fallback",
        "method": "worker_model",
        "provider": prov,
        "model": mod,
        "text": fill_text,
        "reason": "No 0.1.3+ VS Code window is open; fell back to worker's own model."
    }


def execute_inline_completion(worker_id: str, prompt: str, prefix: str = "", suffix: str = "",
                                filepath: str = "", base_dir: str = "", timeout: float = 5.0) -> Dict[str, Any]:
    if not base_dir:
        base_dir = getattr(_s, "SUBWAY_DIR", os.path.expanduser("~/.subway"))

    ok, reason = check_inline_eligibility(worker_id, prompt=prompt, filepath=filepath,
                                          prefix=prefix, suffix=suffix, base_dir=base_dir)
    if not ok:
        return {"status": "error", "error": reason}

    win = find_active_vscode_window(base_dir=base_dir)
    meta = _get_worker_meta(base_dir, worker_id)

    if not win:
        # Fall back to worker's own model
        return fallback_to_worker_model(worker_id, prompt, prefix=prefix, suffix=suffix, filepath=filepath, meta=meta)

    # 0.1.3+ window is open: create probe request
    nonce = secrets.token_hex(16)
    req_path = os.path.join(base_dir, "inline_probe_request.json")
    res_path = os.path.join(base_dir, "inline_probe.json")
    req_data = {
        "nonce": nonce,
        "expires_at": time.time() + timeout + 5.0,
        "used": False,
        "worker_id": worker_id,
        "prompt": prompt,
        "prefix": prefix,
        "suffix": suffix,
        "filepath": filepath
    }
    tmp_req = os.path.join(base_dir, f".inline_req_{os.getpid()}_{time.time_ns()}.tmp")
    try:
        with open(tmp_req, "w", encoding="utf-8") as f:
            json.dump(req_data, f)
        os.replace(tmp_req, req_path)
    except Exception:
        return fallback_to_worker_model(worker_id, prompt, prefix=prefix, suffix=suffix, filepath=filepath, meta=meta)

    # Poll for response or claim
    deadline = time.time() + timeout
    result_text = ""
    method_used = "extension"
    duration_ms = 0
    got_result = False

    while time.time() < deadline:
        if os.path.isfile(res_path):
            try:
                with open(res_path, "r", encoding="utf-8") as f:
                    pdata = json.load(f)
                if isinstance(pdata, dict) and pdata.get("nonce") == nonce:
                    result_text = pdata.get("text") or ""
                    method_used = pdata.get("method") or "extension"
                    duration_ms = pdata.get("ms") or 0
                    got_result = True
                    break
            except Exception:
                pass
        time.sleep(0.1)

    # Clean up request if still exists
    with contextlib.suppress(Exception):
        if os.path.exists(req_path):
            try:
                with open(req_path, "r", encoding="utf-8") as f:
                    cur = json.load(f)
                if cur.get("nonce") == nonce:
                    os.remove(req_path)
            except Exception:
                pass

    if got_result:
        record_inline_call(base_dir, worker_id, count=1)
        return {
            "status": "ok",
            "method": method_used,
            "ms": duration_ms,
            "text": result_text,
            "chars": len(result_text),
            "requests": REQUESTS_PER_CALL
        }

    # If timed out or failed to get probe result, fall back to worker model
    return fallback_to_worker_model(worker_id, prompt, prefix=prefix, suffix=suffix, filepath=filepath, meta=meta)


def t_copilot_inline(worker_id: str = "", prompt: str = "", prefix: str = "", suffix: str = "", filepath: str = "") -> str:
    res = execute_inline_completion(worker_id, prompt=prompt, prefix=prefix, suffix=suffix, filepath=filepath)
    if res.get("status") == "error":
        return f"Error: {res.get('error')}"
    if res.get("status") == "fallback":
        return f"[Copilot Inline: Fallback to {res.get('provider')}/{res.get('model')}]\n{res.get('text')}"
    return f"[Copilot Inline ({res.get('method')}, {res.get('ms')}ms)]\n{res.get('text')}"
