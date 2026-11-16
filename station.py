import os, sys, socket, platform, json, uuid, time, contextlib
from typing import Dict, Any, List, Optional
import server as _s

def get_station_id() -> str:
    path = os.path.join(_s.SUBWAY_DIR, "station_id.txt")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                sid = f.read().strip()
                if sid: return sid
        except Exception:
            pass
    sid = f"station_{socket.gethostname().lower().split('.')[0]}_{uuid.uuid4().hex[:6]}"
    try:
        os.makedirs(_s.SUBWAY_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(sid)
    except Exception:
        pass
    return sid

TELEMETRY_FILE = "edge_telemetry.json"
TELEMETRY_STATES = {
    "battery_state": ("charging", "unplugged"),
    "thermal_state": ("nominal", "fair", "serious", "critical"),
    "network_type": ("wifi", "cellular"),
}


def telemetry_path() -> str:
    return os.path.join(_s.SUBWAY_DIR, TELEMETRY_FILE)


def get_telemetry() -> Optional[Dict[str, Any]]:
    """The last telemetry a phone reported, or None. A corrupt file is not a crash."""
    try:
        with open(telemetry_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _telemetry_error(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return "body must be a JSON object"
    for key, allowed in TELEMETRY_STATES.items():
        value = str(payload.get(key) or "").strip().lower()
        if value not in allowed:
            return f"{key} must be one of {', '.join(allowed)}"
    pct = payload.get("battery_pct")
    if pct is not None:
        try:
            pct = int(pct)
        except (TypeError, ValueError):
            return "battery_pct must be an integer 0-100"
        if not 0 <= pct <= 100:
            return "battery_pct must be an integer 0-100"
    return ""


def set_telemetry(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Store a phone's battery, thermal and network state. Raises ValueError when malformed."""
    problem = _telemetry_error(payload)
    if problem:
        raise ValueError(problem)
    clean = {
        "device_id": str(payload.get("device_id") or "").strip()[:64],
        "battery_state": str(payload["battery_state"]).strip().lower(),
        "battery_pct": int(payload["battery_pct"]) if payload.get("battery_pct") is not None else None,
        "thermal_state": str(payload["thermal_state"]).strip().lower(),
        "network_type": str(payload["network_type"]).strip().lower(),
        "low_power": payload.get("low_power") is True,
        "updated_at": time.time(),
    }
    os.makedirs(_s.SUBWAY_DIR, exist_ok=True)
    _s._write_json_atomic(telemetry_path(), clean)
    return clean


def get_station_card() -> Dict[str, Any]:
    """Structured identity of this Subway station (Roadmap Item 38a3)."""
    import local_models, workers, usage
    hostname = socket.gethostname()
    person = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
    try:
        mem_snap = local_models.memory_snapshot() or {}
    except Exception:
        mem_snap = {}

    # Detected providers and models
    try:
        catalog = local_models.model_catalog() or []
    except Exception:
        try:
            catalog = _s._model_catalog(_s._catalog_stamp()) or []
        except Exception:
            catalog = []
    providers = sorted(list({m.get("provider") for m in catalog if isinstance(m, dict) and m.get("provider")}))

    # Capacity and quotas
    try:
        cap = workers.capacity() or {}
    except Exception:
        cap = {}
    quotas = usage.get_quotas() if hasattr(usage, "get_quotas") else {}

    routes = ["loopback:127.0.0.1"]
    bound_host = getattr(_s, "HUD_HOST", "127.0.0.1")
    if bound_host and bound_host not in ("127.0.0.1", "0.0.0.0"):
        routes.append(f"custom:{bound_host}")

    total_bytes = mem_snap.get("total_bytes") or mem_snap.get("total") or 0
    free_bytes = mem_snap.get("free_bytes") or mem_snap.get("available") or 0

    # Local models with memory-fit signal (best-effort; never blocks the card).
    local_models = []
    try:
        for m in (catalog or []):
            if not isinstance(m, dict):
                continue
            prov = str(m.get("provider") or "")
            is_local = bool(m.get("local")) or prov in ("ollama", "local", "lmstudio")
            if not is_local:
                continue
            rec = dict(m)
            est = rec.get("memory_bytes") or rec.get("size_bytes")
            if est is None:
                with contextlib.suppress(Exception):
                    import local_runtime
                    est = local_runtime.estimate_model_bytes(rec)
            rec["memory_fit"] = bool(est) and bool(free_bytes) and int(est) <= int(free_bytes)
            if est:
                rec["memory_bytes"] = int(est)
            local_models.append(rec)
    except Exception:
        local_models = []

    quota_left = {}
    try:
        for src, info in (quotas or {}).items():
            if not isinstance(info, dict):
                continue
            left = info.get("remaining")
            if left is None and info.get("limit") is not None and info.get("used") is not None:
                with contextlib.suppress(Exception):
                    left = max(0, int(info["limit"]) - int(info["used"]))
            quota_left[src] = left
    except Exception:
        quota_left = {}

    default_initiator = "principal"
    try:
        import thread_state
        default_initiator = str(thread_state.thread_config().get("default_initiator") or "principal")
    except Exception:
        default_initiator = "principal"

    return {
        "station_id": get_station_id(),
        "person": person,
        "device": {
            "hostname": hostname,
            "platform": sys.platform,
            "os": platform.system(),
            "arch": platform.machine(),
            "total_ram_gb": round(total_bytes / (1024**3), 1),
            "free_ram_gb": round(free_bytes / (1024**3), 1),
        },
        "version": getattr(_s, "VERSION", "1.3.8"),
        "providers": providers,
        "models": catalog,
        "models_count": len(catalog),
        "local_models": local_models,
        "capacity": cap,
        "worker_capacity": cap,
        "quotas": quotas,
        "quota_left": quota_left,
        "default_initiator": default_initiator,
        "routes": routes,
        # A phone's own state, so routing can refuse work it should not be given.
        "telemetry": get_telemetry(),
        "edge_eligible": _edge_eligible(),
    }


def _edge_eligible() -> bool:
    """True only when this station may take work from elsewhere (charging, nominal, wifi)."""
    import local_models

    return bool(local_models.edge_telemetry_ok(get_telemetry())[0])
