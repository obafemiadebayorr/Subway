import os, sys, socket, platform, json, uuid, time, contextlib
from typing import Dict, Any, List, Optional
import server as _s

def get_display_name() -> str:
    """User-chosen station name from config.json (`station_name`), else the hostname without .local."""
    try:
        cfg = _s._load_json("config.json") or {}
    except Exception:
        cfg = {}
    name = str(cfg.get("station_name") or "").strip() if isinstance(cfg, dict) else ""
    if name:
        return name[:60]
    host = socket.gethostname()
    return host[:-6] if host.lower().endswith(".local") else host


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


def _is_finite_number(value: Any) -> bool:
    import math

    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def telemetry_valid(t: Any, max_age_seconds: Optional[float] = None) -> bool:
    """Return True only when *t* is a dict with valid free_ram_mb and updated_at."""
    if not isinstance(t, dict):
        return False
    free_ram = t.get("free_ram_mb")
    if not _is_finite_number(free_ram) or float(free_ram) < 0:
        return False
    updated_at = t.get("updated_at")
    if not _is_finite_number(updated_at) or float(updated_at) <= 0:
        return False
    if max_age_seconds is not None:
        if not _is_finite_number(max_age_seconds) or float(max_age_seconds) < 0:
            return False
        if (time.time() - float(updated_at)) > float(max_age_seconds):
            return False
    return True


def _linux_memory_mb() -> tuple[float, float]:
    meminfo: Dict[str, int] = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for line in f:
            if ":" not in line:
                continue
            key, rest = line.split(":", 1)
            parts = rest.strip().split()
            if parts and parts[0].isdigit():
                meminfo[key.strip()] = int(parts[0])
    total_kb = meminfo.get("MemTotal", 0)
    if "MemAvailable" in meminfo:
        free_kb = meminfo["MemAvailable"]
    else:
        free_kb = meminfo.get("MemFree", 0) + meminfo.get("Buffers", 0) + meminfo.get("Cached", 0)
    return round(free_kb / 1024.0, 1), round(total_kb / 1024.0, 1)


def _macos_memory_mb() -> tuple[float, float]:
    import re
    import subprocess

    total_mb = 0.0
    free_mb = 0.0
    raw_mem = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, timeout=2)
    total_bytes = int(str(raw_mem).strip())
    total_mb = round(total_bytes / (1024.0 * 1024.0), 1)

    vm_out = subprocess.check_output(["vm_stat"], text=True, timeout=2)
    page_size = 4096
    m_page = re.search(r"page size of (\d+) bytes", vm_out)
    if m_page:
        page_size = int(m_page.group(1))
    pages: Dict[str, int] = {}
    for line in vm_out.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        digits = v.strip().rstrip(".").replace(",", "")
        if digits.isdigit():
            pages[k.strip().lower()] = int(digits)
    free_pages = (
        pages.get("pages free", 0)
        + pages.get("pages inactive", 0)
        + pages.get("pages speculative", 0)
    )
    free_mb = round((free_pages * page_size) / (1024.0 * 1024.0), 1)
    return free_mb, total_mb


def _windows_memory_mb() -> tuple[float, float]:
    import ctypes

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = _MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return 0.0, 0.0
    free_mb = round(stat.ullAvailPhys / (1024.0 * 1024.0), 1)
    total_mb = round(stat.ullTotalPhys / (1024.0 * 1024.0), 1)
    return free_mb, total_mb


def _host_memory_mb() -> tuple[float, float]:
    plat = sys.platform.lower()
    try:
        if plat.startswith("linux"):
            return _linux_memory_mb()
        if plat == "darwin":
            return _macos_memory_mb()
        if plat.startswith("win"):
            return _windows_memory_mb()
    except Exception:
        pass
    with contextlib.suppress(Exception):
        import local_models

        snap = local_models.memory_snapshot() or {}
        total_b = snap.get("total_bytes") or snap.get("total") or 0
        free_b = snap.get("free_bytes") or snap.get("available") or 0
        if total_b or free_b:
            return round(float(free_b) / (1024.0 * 1024.0), 1), round(float(total_b) / (1024.0 * 1024.0), 1)
    return 0.0, 0.0


def _detect_station_kind() -> str:
    with contextlib.suppress(Exception):
        cfg = _s._load_json("config.json") or {}
        if isinstance(cfg, dict):
            kind = str(cfg.get("station_kind") or cfg.get("device_kind") or "").strip().lower()
            if kind in ("desktop", "laptop", "phone"):
                return kind
    plat = sys.platform.lower()
    if plat in ("ios", "android"):
        return "phone"
    if plat == "darwin":
        with contextlib.suppress(Exception):
            import subprocess

            model = subprocess.check_output(["sysctl", "-n", "hw.model"], text=True, timeout=1)
            if "macbook" in str(model).lower():
                return "laptop"
    elif plat.startswith("linux"):
        for bat in ("/sys/class/power_supply/BAT0", "/sys/class/power_supply/BAT1"):
            if os.path.exists(bat):
                return "laptop"
    return "desktop"


def collect_host_telemetry(running_workers: Optional[int] = None) -> Dict[str, Any]:
    """Gather cross-platform host telemetry (RAM, CPU load, running workers, timestamp, kind)."""
    free_mb, total_mb = _host_memory_mb()
    cpu_load = 0.0
    try:
        if hasattr(os, "getloadavg"):
            load = float(os.getloadavg()[0])
            if _is_finite_number(load) and load >= 0:
                cpu_load = round(load, 3)
    except (AttributeError, OSError, TypeError, ValueError):
        cpu_load = 0.0

    rw = 0
    if running_workers is not None:
        rw = max(0, int(running_workers))
    else:
        with contextlib.suppress(Exception):
            import workers

            cap = workers.capacity() if hasattr(workers, "capacity") else {}
            if isinstance(cap, dict):
                for key in ("running", "active", "busy", "used"):
                    val = cap.get(key)
                    if _is_finite_number(val):
                        rw = max(0, int(val))
                        break

    return {
        "free_ram_mb": free_mb,
        "total_ram_mb": total_mb,
        "cpu_load": cpu_load,
        "running_workers": rw,
        "updated_at": time.time(),
        "kind": _detect_station_kind(),
    }


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


import threading as _threading

_PLACEMENT_LOCK = _threading.Lock()

PLACEMENT_WEIGHTS: Dict[str, Optional[float]] = {
    "prefer": 1.5,
    "normal": 1.0,
    "less": 0.5,
    "manual": None,
}

_PLACEMENT_ALIASES: Dict[str, str] = {
    "prefer": "prefer",
    "normal": "normal",
    "balanced": "normal",
    "less": "less",
    "less often": "less",
    "less_often": "less",
    "less-often": "less",
    "manual": "manual",
    "manual only": "manual",
    "manual_only": "manual",
    "manual-only": "manual",
}


def normalize_placement_preference(value: Any, *, default: Optional[str] = None) -> str:
    """Normalize a placement preference name, display alias, or numeric weight."""
    if value is None:
        if default is not None:
            return default
        return "manual"
    if isinstance(value, bool):
        raise ValueError("placement preference must be prefer, normal, less, or manual")
    if isinstance(value, (int, float)):
        num = float(value)
        if num == 1.5:
            return "prefer"
        if num == 1.0:
            return "normal"
        if num == 0.5:
            return "less"
        if num == 0.0:
            return "manual"
        raise ValueError("placement weight must be 1.5, 1.0, 0.5, or 0")
    key = " ".join(str(value).strip().lower().split())
    if key in _PLACEMENT_ALIASES:
        return _PLACEMENT_ALIASES[key]
    if key == "1.5":
        return "prefer"
    if key in ("1", "1.0"):
        return "normal"
    if key == "0.5":
        return "less"
    if key in ("0", "0.0", "none", "off", "excluded"):
        return "manual"
    if default is not None:
        return default
    raise ValueError(f"unknown placement preference: {value!r}")


def placement_weight(pref: Any) -> Optional[float]:
    """Return numeric score multiplier (1.5 / 1.0 / 0.5) or None when manual/excluded."""
    canon = normalize_placement_preference(pref, default="normal")
    return PLACEMENT_WEIGHTS[canon]


def _load_local_config() -> Dict[str, Any]:
    try:
        cfg = _s._load_json("config.json")
    except Exception:
        return {}
    return dict(cfg) if isinstance(cfg, dict) else {}


def get_placement_preference(peer_id: str) -> str:
    """Return local-only placement preference for `peer_id` (default: 'normal')."""
    pid = str(peer_id or "").strip()
    if not pid:
        return "normal"
    with _PLACEMENT_LOCK:
        cfg = _load_local_config()
    prefs = cfg.get("placement_preferences")
    if not isinstance(prefs, dict):
        return "normal"
    return normalize_placement_preference(prefs.get(pid), default="normal")


def set_placement_preference(peer_id: str, weight: Any) -> str:
    """Store local-only placement preference for `peer_id` in config.json (not shared)."""
    pid = str(peer_id or "").strip()
    if not pid:
        raise ValueError("peer_id is required")
    canon = normalize_placement_preference(weight)
    with _PLACEMENT_LOCK:
        cfg = _load_local_config()
        prefs = dict(cfg.get("placement_preferences")) if isinstance(cfg.get("placement_preferences"), dict) else {}
        prefs[pid] = canon
        cfg["placement_preferences"] = prefs
        os.makedirs(_s.SUBWAY_DIR, exist_ok=True)
        _s._write_json_atomic(os.path.join(_s.SUBWAY_DIR, "config.json"), cfg)
    return canon


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

    edge = get_telemetry()
    if telemetry_valid(edge):
        card_telemetry = edge
    elif (
        isinstance(edge, dict)
        and all(k in edge for k in TELEMETRY_STATES)
        and _is_finite_number(edge.get("updated_at"))
        and float(edge["updated_at"]) > 0
    ):
        card_telemetry = collect_host_telemetry()
        card_telemetry.update(edge)
        card_telemetry["kind"] = edge.get("kind") or "phone"
    else:
        card_telemetry = collect_host_telemetry()

    return {
        "station_id": get_station_id(),
        "person": person,
        "display_name": get_display_name(),
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
        # Host or phone telemetry so routing/placement has live capacity & state.
        "telemetry": card_telemetry,
        "edge_eligible": _edge_eligible(),
    }


def _edge_eligible() -> bool:
    """True only when this station may take work from elsewhere (charging, nominal, wifi)."""
    import local_models

    return bool(local_models.edge_telemetry_ok(get_telemetry())[0])
