import os, sys, socket, platform, json, uuid
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
        "version": getattr(_s, "VERSION", "1.3.7"),
        "providers": providers,
        "models_count": len(catalog),
        "capacity": cap,
        "quotas": quotas,
        "default_initiator": "principal",
        "routes": routes
    }
