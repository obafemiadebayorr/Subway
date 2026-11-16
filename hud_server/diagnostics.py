"""GET /api/diagnostics: host/process diagnostics for the HUD.

Collectors use subprocess with timeouts. psutil is NOT installed.
Never fakes values: missing/failing collectors report in collection.collectors.
"""
from __future__ import annotations

import glob
import concurrent.futures
import subway_home
import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

WORKERS_DIR = os.path.join(subway_home.subway_dir(), "workers")
_TTL = 1.0
_HISTORY_STEP = 5
_SAMPLER_ENABLED = True
_cache: Dict[str, Any] = {"key": "diag", "at": 0.0, "value": None}
_prev_disk: Dict[str, Any] = {"at": 0.0, "read_bytes": None, "write_bytes": None}
_history: deque = deque(maxlen=720)
_sampler_started = False
_last_demand = 0.0
_sampler_lock = threading.Lock()
_new_sample = threading.Condition()


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
    ok, out, err = _run(["ps", "-axo", "pid,ppid,pcpu,rss,args"], 2.0)
    if not ok:
        collectors.append({"name": "ps", "ok": False, "error": err or "ps failed"})
        return {"rows": [], "process_count": None, "cpu_pct": None, "rss_bytes": None}
    rows = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            args = (parts[4] or "")[:300]
            first = args.split()[0] if args.split() else ""
            rows.append({
                "pid": int(parts[0]),
                "ppid": int(parts[1]),
                "pcpu": float(parts[2]),
                "rss_kb": int(parts[3]),
                "args": args,
                "comm": os.path.basename(first),
            })
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
        return {"thermal": None, "thermal_raw": None}
    thermal_raw = "\n".join(l.strip() for l in out.splitlines() if l.strip())
    thermal = "nominal" if "No thermal warning" in thermal_raw else (thermal_raw or None)
    collectors.append({"name": "pmset-therm", "ok": True, "error": None})
    return {"thermal": thermal, "thermal_raw": thermal_raw}


def _collect_lowpower(collectors: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok, out, err = _run(["pmset", "-g"], 1.5)
    if not ok:
        collectors.append({"name": "pmset-lowpower", "ok": False, "error": err or "pmset failed"})
        return {"low_power": None}
    m = re.search(r"lowpowermode\s+(\d+)", out)
    collectors.append({"name": "pmset-lowpower", "ok": True, "error": None})
    return {"low_power": bool(int(m.group(1))) if m else None}


def _collect_load(collectors: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        a, b, c = os.getloadavg()
        collectors.append({"name": "loadavg", "ok": True, "error": None})
        return {"load_avg": f"{{ {a:.2f} {b:.2f} {c:.2f} }}"}
    except (AttributeError, OSError):
        pass
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


def _worker_pids() -> Dict[int, str]:
    """Map of pid -> worker id for running/stopping workers."""
    out: Dict[int, str] = {}
    for path in glob.glob(os.path.join(WORKERS_DIR, "worker_*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("status") in ("running", "stopping") and d.get("pid"):
                out[int(d["pid"])] = str(d.get("id") or d.get("worker_id") or os.path.basename(path)[len("worker_"):-len(".json")])
        except Exception:
            continue
    return out


def _ensure_sampler() -> None:
    global _sampler_started
    if not _SAMPLER_ENABLED or _sampler_started:
        return
    with _sampler_lock:
        if _sampler_started or not _SAMPLER_ENABLED:
            return
        _sampler_started = True

        def _loop() -> None:
            while True:
                time.sleep(_HISTORY_STEP)
                if time.monotonic() - _last_demand > 60.0:
                    continue
                try:
                    compute()
                except Exception:
                    continue

        t = threading.Thread(target=_loop, name="diag-sampler", daemon=True)
        t.start()


def _classify(rows: List[Dict[str, Any]], worker_map: Dict[int, str]) -> Dict[str, Dict[int, str]]:
    """Assign each row to at most one group: hud, workers, backend."""
    assign: Dict[int, str] = {}
    by_pid = {r["pid"]: r for r in rows}

    def parent(pid: int) -> Optional[Dict[str, Any]]:
        row = by_pid.get(pid)
        if row is None:
            return None
        ppid = row.get("ppid")
        if ppid is None or ppid <= 1:
            return None
        return by_pid.get(int(ppid))

    # hud
    for r in rows:
        args_l = (r.get("args") or "").lower()
        if "server.py hud" in args_l or "subway watch" in args_l or "hud_server" in args_l:
            assign[r["pid"]] = "hud"

    # workers: pid in map or descendant (walk ppid up to 6 levels)
    for r in rows:
        if r["pid"] in worker_map:
            assign[r["pid"]] = "workers"
            continue
        cur = r["pid"]
        for _ in range(6):
            p = parent(cur)
            if p is None:
                break
            if p["pid"] in worker_map:
                assign[r["pid"]] = "workers"
                break
            cur = p["pid"]

    # backend: subway-context path, known agent comms, or direct child of such a row
    agent_comms = {"claude", "opencode", "pi", "agy", "gemini"}
    backend_pids = set()
    for r in rows:
        args_l = (r.get("args") or "").lower()
        comm_l = (r.get("comm") or "").lower()
        if "subway-context/" in args_l or comm_l in agent_comms:
            backend_pids.add(r["pid"])
    for r in rows:
        if r["pid"] in assign:
            continue
        if r["pid"] in backend_pids:
            assign[r["pid"]] = "backend"
            continue
        p = parent(r["pid"])
        if p is not None and p["pid"] in backend_pids:
            assign[r["pid"]] = "backend"

    grouped: Dict[str, Dict[int, str]] = {"hud": {}, "workers": {}, "backend": {}}
    for pid, grp in assign.items():
        wid = worker_map.get(pid)
        grouped[grp][pid] = wid if wid else ""
    return grouped


def _home_tilde(text: str) -> str:
    home = os.path.expanduser("~")
    if home and home != "/":
        return text.replace(home, "~")
    return text


def _sampler_thread_needed() -> bool:
    return _SAMPLER_ENABLED


def _compute_fresh() -> Dict[str, Any]:
    """Compute a fresh diagnostics payload and update cache/history."""
    t0 = time.time()
    now = t0
    collectors: List[Dict[str, Any]] = []
    collectors: List[Dict[str, Any]] = []
    collector_fns = (
        ("ps", _collect_ps),
        ("disk", _collect_disk),
        ("batt", _collect_batt),
        ("therm", _collect_therm),
        ("low", _collect_lowpower),
        ("load", _collect_load),
        ("up", _collect_uptime),
    )
    jobs = {name: (fn, []) for name, fn in collector_fns}
    results: Dict[str, Dict[str, Any]] = {}
    local_entries: Dict[str, List[Dict[str, Any]]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
        futures = {name: (pool.submit(fn, local), local) for name, (fn, local) in jobs.items()}
        for name, (fut, local) in futures.items():
            results[name] = fut.result()
            local_entries[name] = local
    for name, _fn in collector_fns:
        collectors.extend(local_entries[name])
    ps = results["ps"]
    disk = results["disk"]
    batt = results["batt"]
    therm = results["therm"]
    low = results["low"]
    load = results["load"]
    up = results["up"]

    rows = ps.get("rows") or []
    worker_map = _worker_pids()
    grouped = _classify(rows, worker_map)

    def gsum(pids: Dict[int, str]) -> Dict[str, Any]:
        rs = [r for r in rows if r["pid"] in pids]
        return {"processes": len(rs), "cpu": round(sum(r["pcpu"] for r in rs), 2), "rss": sum(r["rss_kb"] for r in rs) * 1024, "read_bps": None, "write_bps": None}

    groups = [
        {"name": "Backend + agents", **gsum(grouped["backend"])},
        {"name": "Workers", **gsum(grouped["workers"])},
        {"name": "HUD + monitor", **gsum(grouped["hud"])},
    ]

    proc_rows = []
    for grp in ("backend", "workers", "hud"):
        for r in rows:
            if r["pid"] in grouped[grp]:
                proc_rows.append({
                    "pid": r["pid"],
                    "ppid": r["ppid"],
                    "name": r["comm"],
                    "cmd": _home_tilde(r.get("args") or "")[:120],
                    "group": grp,
                    "cpu": r["pcpu"],
                    "rss": r["rss_kb"] * 1024,
                    "worker_id": grouped[grp][r["pid"]],
                })
    proc_rows.sort(key=lambda x: (-x["cpu"], x["pid"]))
    proc_rows = proc_rows[:60]

    collectors.append({"name": "cpu_speed_limit", "ok": True, "status": "unsupported", "error": None, "detail": "No CPU speed limit source on Apple Silicon"})
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
        "processes": proc_rows,
        "host": {
            "power_source": batt.get("power_source"),
            "low_power": low.get("low_power"),
            "idle": None,
            "thermal": therm.get("thermal"),
            "thermal_raw": therm.get("thermal_raw"),
            "load_avg": load.get("load_avg"),
            "uptime": up.get("uptime"),
        },
        "collection": {"ms": int((time.time() - t0) * 1000), "collectors": collectors},
    }

    _history.append({
        "t": int(now),
        "cpu": result["cpu_pct"],
        "rss": result["rss_bytes"],
        "read_bps": result["read_bps"],
        "write_bps": result["write_bps"],
        "procs": result["process_count"],
        "groups": {
            "backend": groups[0]["cpu"],
            "workers": groups[1]["cpu"],
            "hud": groups[2]["cpu"],
        },
    })
    result["history"] = list(_history)
    result["history_step"] = _HISTORY_STEP

    if _sampler_thread_needed():
        _ensure_sampler()
    _cache.update({"at": now, "value": result})
    with _new_sample:
        _new_sample.notify_all()
    return result


def compute() -> Dict[str, Any]:
    global _last_demand
    _last_demand = time.monotonic()
    now = time.time()
    if _cache["value"] is not None and now - _cache["at"] < _TTL:
        return _cache["value"]
    return _compute_fresh()


def wait_sample(last_at: float, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """Block until the sampler stores a sample newer than last_at (None on timeout).

    Lets the SSE stream push each sample the moment the sampler takes it, instead of
    waking on its own 5 s timer out of phase with the sampler. Keeps the sampler awake."""
    global _last_demand
    _last_demand = time.monotonic()
    _ensure_sampler()
    deadline = time.monotonic() + timeout
    with _new_sample:
        while float(_cache.get("at") or 0.0) <= last_at:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            _new_sample.wait(left)
    return _cache["value"]
