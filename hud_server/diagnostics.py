"""GET /api/diagnostics: host/process diagnostics for the HUD.

Collectors use subprocess with timeouts. psutil is NOT installed.
Never fakes values: missing/failing collectors report in collection.collectors.
"""
from __future__ import annotations

import glob
import subway_home
import json
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

WORKERS_DIR = os.path.join(subway_home.subway_dir(), "workers")
_TTL = 1.0
_cache: Dict[str, Any] = {"key": "diag", "at": 0.0, "value": None}
_prev_disk: Dict[str, Any] = {"at": 0.0, "read_bytes": None, "write_bytes": None}


def _run(cmd: List[str], timeout: float = 1.5) -> Tuple[bool, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, p.stdout or "", p.stderr or ""
    except Exception as e:
        return False, "", str(e)


def _parse_size_to_bytes(token: str) -> Optional[float]:
    if not token:
        return None
    m = re.match(r"([0-9.]+)\s*([KMGT]?B)?", token.strip(), re.I)
    if not m:
        return None
    n = float(m.group(1))
    unit = (m.group(2) or "B").upper()
    mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}.get(unit, 1)
    return n * mult


def _collect_ps(collectors: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok, out, err = _run(["ps", "-axo", "pid,ppid,pcpu,rss,comm"], 2.0)
    if not ok:
        collectors.append({"name": "ps", "ok": False, "error": err or "ps failed"})
        return {"rows": [], "process_count": None, "cpu_pct": None, "rss_bytes": None}
    rows = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            rows.append({"pid": int(parts[0]), "ppid": int(parts[1]), "pcpu": float(parts[2]), "rss_kb": int(parts[3]), "comm": parts[4]})
        except Exception:
            continue
    collectors.append({"name": "ps", "ok": True, "error": None})
    return {
        "rows": rows,
        "process_count": len(rows),
        "cpu_pct": round(sum(r["pcpu"] for r in rows), 2),
        "rss_bytes": sum(r["rss_kb"] for r in rows) * 1024,
    }


def _collect_disk(collectors: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    ok, out, err = _run(["top", "-l", "1", "-s", "0"], 2.5)
    if not ok:
        collectors.append({"name": "top-disks", "ok": False, "error": err or "top failed"})
        return {"read_bps": None, "write_bps": None}
    m = re.search(r"Disks:.*?([0-9,.]+[KMGT]?B?)/?([0-9,.]+[KMGT]?B?)?\s*read.*?([0-9,.]+[KMGT]?B?)/?([0-9,.]+[KMGT]?B?)?\s*written", out, re.I | re.S)
    if not m:
        collectors.append({"name": "top-disks", "ok": False, "error": "could not parse Disks line"})
        return {"read_bps": None, "write_bps": None}
    def pick(a, b):
        for t in (b, a):
            if t and re.search(r"[KMGT]B", t, re.I):
                v = _parse_size_to_bytes(t)
                if v is not None:
                    return v
        return _parse_size_to_bytes(a or "")
    rb = pick(m.group(1), m.group(2))
    wb = pick(m.group(3), m.group(4))
    now = time.time()
    read_bps = write_bps = None
    if rb is not None and wb is not None and _prev_disk.get("read_bytes") is not None and now > _prev_disk["at"]:
        dt = now - _prev_disk["at"]
        if dt > 0:
            read_bps = max(0.0, (rb - _prev_disk["read_bytes"]) / dt)
            write_bps = max(0.0, (wb - _prev_disk["write_bytes"]) / dt)
    _prev_disk.update({"at": now, "read_bytes": rb, "write_bytes": wb})
    collectors.append({"name": "top-disks", "ok": True, "error": None})
    return {"read_bps": round(read_bps, 2) if read_bps is not None else None, "write_bps": round(write_bps, 2) if write_bps is not None else None}


def _collect_batt(collectors: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok, out, err = _run(["pmset", "-g", "batt"], 1.5)
    if not ok:
        collectors.append({"name": "pmset-batt", "ok": False, "error": err or "pmset failed"})
        return {"power_source": None}
    src = "AC" if "AC Power" in out else "Battery" if "Battery Power" in out else None
    collectors.append({"name": "pmset-batt", "ok": True, "error": None})
    return {"power_source": src}


def _collect_therm(collectors: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok, out, err = _run(["pmset", "-g", "therm"], 1.5)
    if not ok:
        collectors.append({"name": "pmset-therm", "ok": False, "error": err or "pmset failed"})
        return {"thermal": None}
    thermal = None
    for line in out.splitlines():
        if "CPU_Speed_Limit" in line or "thermal" in line.lower():
            thermal = (thermal or "") + line.strip() + " "
    collectors.append({"name": "pmset-therm", "ok": True, "error": None})
    return {"thermal": thermal.strip() if thermal else "nominal"}


def _collect_lowpower(collectors: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok, out, err = _run(["pmset", "-g"], 1.5)
    if not ok:
        collectors.append({"name": "pmset-lowpower", "ok": False, "error": err or "pmset failed"})
        return {"low_power": None}
    m = re.search(r"lowpowermode\s+(\d+)", out)
    collectors.append({"name": "pmset-lowpower", "ok": True, "error": None})
    return {"low_power": bool(int(m.group(1))) if m else None}


def _collect_load(collectors: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok, out, err = _run(["sysctl", "-n", "vm.loadavg"], 1.0)
    if not ok:
        collectors.append({"name": "loadavg", "ok": False, "error": err or "sysctl failed"})
        return {"load_avg": None}
    collectors.append({"name": "loadavg", "ok": True, "error": None})
    return {"load_avg": out.strip() or None}


def _collect_uptime(collectors: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok, out, err = _run(["uptime"], 1.0)
    if not ok:
        collectors.append({"name": "uptime", "ok": False, "error": err or "uptime failed"})
        return {"uptime": None}
    collectors.append({"name": "uptime", "ok": True, "error": None})
    return {"uptime": out.strip() or None}


def _worker_pids() -> List[int]:
    pids = []
    for path in glob.glob(os.path.join(WORKERS_DIR, "worker_*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("status") in ("running", "stopping") and d.get("pid"):
                pids.append(int(d["pid"]))
        except Exception:
            continue
    return pids


def compute() -> Dict[str, Any]:
    now = time.time()
    if _cache["value"] is not None and now - _cache["at"] < _TTL:
        return _cache["value"]
    t0 = time.time()
    collectors: List[Dict[str, Any]] = []
    ps = _collect_ps(collectors)
    disk = _collect_disk(collectors)
    batt = _collect_batt(collectors)
    therm = _collect_therm(collectors)
    low = _collect_lowpower(collectors)
    load = _collect_load(collectors)
    up = _collect_uptime(collectors)

    rows = ps.get("rows") or []
    wpids = set(_worker_pids())
    hud_rows = [r for r in rows if "python" in (r.get("comm") or "").lower() and ("hud" in (r.get("comm") or "").lower() or "server" in (r.get("comm") or "").lower())]
    worker_rows = [r for r in rows if r.get("pid") in wpids or "worker" in (r.get("comm") or "").lower()]
    backend_rows = [r for r in rows if r not in worker_rows and r not in hud_rows and ("python" in (r.get("comm") or "").lower() or "node" in (r.get("comm") or "").lower() or "opencode" in (r.get("comm") or "").lower() or "claude" in (r.get("comm") or "").lower())]
    def gsum(rs):
        return {"processes": len(rs), "cpu": round(sum(r["pcpu"] for r in rs), 2), "rss": sum(r["rss_kb"] for r in rs) * 1024, "read_bps": None, "write_bps": None}
    groups = [
        {"name": "Backend + agents", **gsum(backend_rows)},
        {"name": "Workers", **gsum(worker_rows)},
        {"name": "HUD + monitor", **gsum(hud_rows)},
    ]

    collectors.append({"name": "cpu_speed_limit", "ok": False, "error": "no supported source on this host; showing Unknown"})
    result = {
        "healthy": bool(ps.get("rows")) and not any(not c.get("ok") and c.get("name") == "ps" for c in collectors),
        "sampled_at": int(now),
        "cpu_pct": ps.get("cpu_pct"),
        "rss_bytes": ps.get("rss_bytes"),
        "process_count": ps.get("process_count"),
        "read_bps": disk.get("read_bps"),
        "write_bps": disk.get("write_bps"),
        "cpu_speed_limit": None,
        "groups": groups,
        "host": {
            "power_source": batt.get("power_source"),
            "low_power": low.get("low_power"),
            "idle": None,
            "thermal": therm.get("thermal"),
            "load_avg": load.get("load_avg"),
            "uptime": up.get("uptime"),
        },
        "collection": {"ms": int((time.time() - t0) * 1000), "collectors": collectors},
    }
    _cache.update({"at": now, "value": result})
    return result
