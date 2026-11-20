"""Release lanes core: manage git worktree lanes per project outside the repo."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

_LANE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
_GB = 1024 * 1024 * 1024
_RECOMMEND_TTL = 60.0
_STATUS_TTL = 10.0

_RECOMMEND_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_STATUS_CACHE: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_LOCK = threading.Lock()


def clear_cache() -> None:
    """Clear module-level recommend and status caches."""
    with _CACHE_LOCK:
        _RECOMMEND_CACHE.clear()
        _STATUS_CACHE.clear()


def _invalidate_status(project_dir: str) -> None:
    try:
        pid = project_id(project_dir)
    except Exception:
        pid = ""
    key = os.path.realpath(project_dir) if project_dir else ""
    with _CACHE_LOCK:
        if pid:
            _STATUS_CACHE.pop(pid, None)
        if key:
            _STATUS_CACHE.pop(key, None)


def _git(cwd: Optional[str], *args: str, check: bool = True) -> str:
    """Run a git command in cwd with a 20s timeout."""
    cmd = ["git", *(["-C", cwd] if cwd else []), *args]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        if check:
            raise ValueError(f"git {' '.join(args)} timed out") from exc
        return ""
    except OSError as exc:
        if check:
            raise ValueError(f"git {' '.join(args)} failed: {exc}") from exc
        return ""
    if res.returncode != 0:
        if check:
            msg = (res.stderr or res.stdout or f"git {' '.join(args)} failed").strip()
            raise ValueError(msg)
        return ""
    return res.stdout.rstrip("\r\n")


def _common_dir(project_dir: str) -> str:
    if not project_dir:
        raise ValueError("project_dir is required")
    current = os.path.realpath(project_dir)
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            break
        parent = os.path.dirname(current)
        if parent == current:
            raise ValueError(f"Not a git repository: {project_dir}")
        current = parent
    raw = _git(project_dir, "rev-parse", "--git-common-dir").strip()
    if not raw:
        raise ValueError(f"Not a git repository: {project_dir}")
    if os.path.isabs(raw):
        return os.path.realpath(raw)
    return os.path.realpath(os.path.join(project_dir, raw))


def _main_worktree(project_dir: str) -> str:
    return os.path.dirname(_common_dir(project_dir))


def project_id(project_dir: str) -> str:
    """Return 12-char sha1 hex of the realpath of git rev-parse --git-common-dir."""
    common = _common_dir(project_dir)
    return hashlib.sha1(common.encode("utf-8")).hexdigest()[:12]


def _path(project_dir: str) -> str:
    import server as _s

    return os.path.join(_s.SUBWAY_DIR, "projects", project_id(project_dir), "lanes.json")


def _main_lane(project_dir: str) -> Dict[str, Any]:
    main_wt = _main_worktree(project_dir)
    branch = _git(main_wt, "branch", "--show-current", check=False).strip()
    if not branch:
        branch = _git(main_wt, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
        if branch == "HEAD":
            branch = ""
    return {
        "name": "main",
        "branch": branch,
        "base": "",
        "worktree": main_wt,
        "role": "main",
        "version": "",
        "created_at": 0,
        "frozen": False,
    }


def _read_raw(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"lanes": [], "default": "", "threads": {}}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError, TypeError):
        return {"lanes": [], "default": "", "threads": {}}
    if not isinstance(raw, dict):
        return {"lanes": [], "default": "", "threads": {}}
    lanes_list = raw.get("lanes")
    if not isinstance(lanes_list, list):
        lanes_list = []
    cleaned: List[Dict[str, Any]] = []
    for item in lanes_list:
        if not isinstance(item, dict):
            continue
        if item.get("name") == "main" or item.get("role") == "main":
            continue
        role = item.get("role") if item.get("role") in ("ship", "dev") else "dev"
        entry = dict(item)
        entry["role"] = role
        if "frozen" not in entry:
            entry["frozen"] = role == "ship"
        else:
            entry["frozen"] = bool(entry["frozen"])
        cleaned.append(entry)
    default = raw.get("default")
    threads = raw.get("threads")
    return {
        "lanes": cleaned,
        "default": default if isinstance(default, str) else "",
        "threads": dict(threads) if isinstance(threads, dict) else {},
    }


def load(project_dir: str) -> Dict[str, Any]:
    """Load project lanes, always prepending the implicit main lane."""
    import server as _s

    p = _path(project_dir)
    with _s._state_lock("lanes"):
        data = _read_raw(p)
    main = _main_lane(project_dir)
    return {
        "lanes": [main, *data["lanes"]],
        "default": data["default"],
        "threads": data["threads"],
    }


def save(project_dir: str, data: Dict[str, Any]) -> None:
    """Atomically save project lanes (excluding the implicit main lane) under state lock."""
    import server as _s

    p = _path(project_dir)
    lanes_in = data.get("lanes") if isinstance(data, dict) else []
    persisted = [
        dict(item)
        for item in (lanes_in or [])
        if isinstance(item, dict)
        and item.get("name") != "main"
        and item.get("role") != "main"
    ]
    payload = {
        "lanes": persisted,
        "default": data.get("default", "") if isinstance(data, dict) else "",
        "threads": dict(data.get("threads") or {}) if isinstance(data, dict) else {},
    }
    with _s._state_lock("lanes"):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = f"{p}.tmp.{os.getpid()}.{threading.get_ident()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)


def _repo_size_bytes(project_dir: Optional[str]) -> int:
    target = project_dir or os.getcwd()
    out = _git(target, "count-objects", "-v", check=False)
    if not out:
        return 0
    size_kib = 0
    pack_kib = 0
    for line in out.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if not v.isdigit():
            continue
        if k == "size":
            size_kib = int(v)
        elif k == "size-pack":
            pack_kib = int(v)
    return (size_kib + pack_kib) * 1024


def _fmt_num(val: float) -> str:
    rounded = round(val, 1)
    if abs(rounded - int(rounded)) < 0.05:
        return str(int(round(val)))
    return f"{rounded:.1f}"


def recommend(project_dir: Optional[str] = None) -> Dict[str, Any]:
    """Recommend lane limit for this device based on RAM, CPUs, and disk free vs repo size."""
    import server as _s

    cache_key = os.path.realpath(project_dir) if project_dir else ""
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _RECOMMEND_CACHE.get(cache_key)
        if cached and (now - cached[0]) < _RECOMMEND_TTL:
            return dict(cached[1])

    mem = _s.memory_snapshot()
    avail = int(mem.get("available", 0))
    cpu_raw = os.cpu_count()
    cpus = cpu_raw if isinstance(cpu_raw, int) and cpu_raw > 0 else 2
    disk_target = project_dir or os.getcwd()
    try:
        disk_free = int(shutil.disk_usage(disk_target).free)
    except OSError:
        disk_free = int(shutil.disk_usage(os.getcwd()).free)
    repo_bytes = _repo_size_bytes(project_dir)

    ram_cap = int(avail // (1.5 * _GB))
    cpu_cap = (cpus // 2) or 1
    disk_cap = int(disk_free // max(1, 2 * repo_bytes))

    n = max(1, min(8, min(ram_cap, cpu_cap, disk_cap)))
    ram_gb = round(avail / _GB, 2)
    disk_free_gb = round(disk_free / _GB, 2)
    repo_mb = round(repo_bytes / (1024 * 1024), 2)
    reason = f"{_fmt_num(avail / _GB)} GB free RAM, {cpus} CPUs, {_fmt_num(disk_free / _GB)} GB free disk"

    result = {
        "recommended": n,
        "max_allowed": 2 * n,
        "reason": reason,
        "ram_gb": ram_gb,
        "cpus": cpus,
        "disk_free_gb": disk_free_gb,
        "repo_mb": repo_mb,
    }
    with _CACHE_LOCK:
        _RECOMMEND_CACHE[cache_key] = (now, dict(result))
    return result


def limit(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return effective lane limit, recommended, max_allowed, and unlocked flag."""
    import server as _s

    if cfg is None:
        cfg = _s._load_json("config.json") or {}
    rec = recommend()
    recommended = rec["recommended"]
    max_allowed = rec["max_allowed"]
    unlocked = bool(cfg.get("lanes_unlocked", False))

    raw_max = cfg.get("lanes_max", 0)
    try:
        custom = int(raw_max) if raw_max is not None else 0
    except (TypeError, ValueError):
        custom = 0

    chosen = recommended if custom <= 0 else custom
    upper = 64 if unlocked else max_allowed
    eff = max(1, min(upper, chosen))
    return {
        "limit": eff,
        "recommended": recommended,
        "max_allowed": max_allowed,
        "unlocked": unlocked,
    }


def _worktree_map(main_wt: str) -> Dict[str, str]:
    """Return mapping of branch_name -> worktree_path from git worktree list --porcelain."""
    out = _git(main_wt, "worktree", "list", "--porcelain", check=False)
    mapping: Dict[str, str] = {}
    cur_wt = ""
    for line in out.splitlines() + [""]:
        if not line.strip():
            cur_wt = ""
            continue
        if line.startswith("worktree "):
            cur_wt = line[len("worktree ") :].strip()
        elif line.startswith("branch ") and cur_wt:
            ref = line[len("branch ") :].strip()
            if ref.startswith("refs/heads/"):
                mapping[ref[len("refs/heads/") :]] = cur_wt
    return mapping


def _branch_exists(main_wt: str, branch: str) -> bool:
    return bool(_git(main_wt, "for-each-ref", f"refs/heads/{branch}", check=False).strip())


def create(
    project_dir: str,
    name: str,
    branch: Optional[str] = None,
    base: Optional[str] = None,
    role: str = "dev",
    version: str = "",
) -> Dict[str, Any]:
    """Create a new release lane and its git worktree."""
    if not isinstance(name, str) or not _LANE_NAME_RE.match(name):
        raise ValueError(f"Invalid lane name '{name}'. Use 1-40 chars matching [A-Za-z0-9._-].")
    if name == "main":
        raise ValueError("Lane name 'main' is reserved.")
    if role not in ("ship", "dev"):
        raise ValueError(f"Invalid role '{role}'. Must be 'ship' or 'dev'.")

    data = load(project_dir)
    if any(l.get("name") == name for l in data["lanes"]):
        raise ValueError(f"Lane '{name}' already exists.")

    non_main = [l for l in data["lanes"] if l.get("role") != "main" and l.get("name") != "main"]
    lim = limit()
    if len(non_main) >= lim["limit"]:
        raise ValueError(
            f"Lane limit reached ({len(non_main)} of {lim['limit']}). "
            f"Recommended for this device: {lim['recommended']}. "
            f"Raise lanes_max in Settings → Git."
        )

    main_wt = _main_worktree(project_dir)
    branch_name = branch or f"release/{name}"

    wt_by_branch = _worktree_map(main_wt)
    if branch_name in wt_by_branch:
        wt_path = wt_by_branch[branch_name]
    else:
        wt_path = os.path.join(os.path.dirname(main_wt), f"{os.path.basename(main_wt)}-{name}")
        if os.name == "nt":
            _git(main_wt, "config", "core.longpaths", "true")
        if _branch_exists(main_wt, branch_name):
            _git(main_wt, "worktree", "add", wt_path, branch_name)
        else:
            _git(main_wt, "worktree", "add", "-b", branch_name, wt_path, base or "HEAD")

    lane_obj: Dict[str, Any] = {
        "name": name,
        "branch": branch_name,
        "base": base or "",
        "worktree": os.path.realpath(wt_path),
        "role": role,
        "version": version or "",
        "created_at": time.time(),
        "frozen": role == "ship",
    }
    data["lanes"].append(lane_obj)
    save(project_dir, data)
    _invalidate_status(project_dir)
    return lane_obj


def remove(project_dir: str, name: str, force: bool = False) -> Dict[str, Any]:
    """Remove a lane and its worktree (never deletes the branch)."""
    if name == "main":
        raise ValueError("Cannot remove the 'main' lane.")
    data = load(project_dir)
    target = next((l for l in data["lanes"] if l.get("name") == name), None)
    if target is None:
        raise ValueError(f"No lane '{name}'.")

    main_lane = next((l for l in data["lanes"] if l.get("role") == "main"), None)
    main_wt = main_lane["worktree"] if main_lane else _main_worktree(project_dir)
    main_branch = (main_lane or {}).get("branch") or "HEAD"
    wt = target.get("worktree", "")
    branch = target.get("branch", "")

    if not force:
        if wt and os.path.isdir(wt):
            st = _git(wt, "status", "--porcelain", check=False)
            dirty_lines = [ln for ln in st.splitlines() if ln and not ln.startswith("??")]
            if dirty_lines:
                raise ValueError(f"Lane '{name}' has uncommitted changes in {wt}.")
        if branch and main_branch and _branch_exists(main_wt, branch):
            ahead_raw = _git(main_wt, "rev-list", "--count", f"{main_branch}..{branch}", check=False).strip()
            ahead = int(ahead_raw) if ahead_raw.isdigit() else 0
            if ahead > 0:
                raise ValueError(f"Lane '{name}' has {ahead} commits not in {main_branch}.")

    if wt and os.path.isdir(wt):
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(wt)
        _git(main_wt, *args)

    data["lanes"] = [l for l in data["lanes"] if l.get("name") != name]
    if data.get("default") == name:
        data["default"] = ""
    data["threads"] = {k: v for k, v in data.get("threads", {}).items() if v != name}
    save(project_dir, data)
    _invalidate_status(project_dir)
    return target


def get(project_dir: str, name: str) -> Optional[Dict[str, Any]]:
    """Return lane dict by name or None."""
    data = load(project_dir)
    return next((l for l in data["lanes"] if l.get("name") == name), None)


def set_default(project_dir: str, name: str) -> str:
    """Set project default lane (empty string clears)."""
    data = load(project_dir)
    if name != "" and not any(l.get("name") == name for l in data["lanes"]):
        avail = ", ".join(l["name"] for l in data["lanes"])
        raise ValueError(f"No lane '{name}'. Lanes: {avail}")
    data["default"] = name
    save(project_dir, data)
    return name


def set_frozen(project_dir: str, name: str, frozen: bool) -> Dict[str, Any]:
    """Set frozen flag on a non-main lane."""
    if name == "main":
        raise ValueError("Cannot freeze the 'main' lane.")
    data = load(project_dir)
    target = next((l for l in data["lanes"] if l.get("name") == name), None)
    if target is None:
        raise ValueError(f"No lane '{name}'.")
    target["frozen"] = bool(frozen)
    save(project_dir, data)
    _invalidate_status(project_dir)
    return target


def set_thread_lane(project_dir: str, thread: str, name: str) -> None:
    """Associate a thread with a lane (empty string clears)."""
    data = load(project_dir)
    if not name:
        data["threads"].pop(thread, None)
    else:
        if not any(l.get("name") == name for l in data["lanes"]):
            avail = ", ".join(l["name"] for l in data["lanes"])
            raise ValueError(f"No lane '{name}'. Lanes: {avail}")
        data["threads"][thread] = name
    save(project_dir, data)


def resolve(
    project_dir: str,
    lane: Optional[str] = None,
    thread: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve lane: explicit lane > thread lane > project default or cfg lanes_default > None."""
    import server as _s

    data = load(project_dir)
    by_name = {l["name"]: l for l in data["lanes"]}

    if lane:
        if lane not in by_name:
            avail = ", ".join(l["name"] for l in data["lanes"])
            raise ValueError(f"No lane '{lane}'. Lanes: {avail}")
        return by_name[lane]

    if thread:
        t_lane = data.get("threads", {}).get(thread)
        if t_lane and t_lane in by_name:
            return by_name[t_lane]

    default_name = data.get("default")
    if not default_name:
        cfg = _s._load_json("config.json") or {}
        default_name = cfg.get("lanes_default") or ""
    if default_name and default_name in by_name:
        return by_name[default_name]

    return None


def lane_for_path(path: str) -> Optional[Dict[str, Any]]:
    """Return the lane whose worktree contains path, or None."""
    if not path:
        return None
    real = os.path.realpath(path)
    probe = real if os.path.isdir(real) else os.path.dirname(real)
    if not os.path.isdir(probe):
        return None
    raw = _git(probe, "rev-parse", "--git-common-dir", check=False).strip()
    if not raw:
        return None
    data = load(probe)
    best: Optional[Dict[str, Any]] = None
    best_len = -1
    for l in data["lanes"]:
        wt = l.get("worktree")
        if not wt:
            continue
        wt_real = os.path.realpath(wt)
        if real == wt_real or real.startswith(wt_real + os.sep):
            if len(wt_real) > best_len:
                best = l
                best_len = len(wt_real)
    return best


def reconcile(project_dir: str) -> Dict[str, Any]:
    """Mark missing worktrees and drop lanes whose branch no longer exists."""
    data = load(project_dir)
    main_wt = _main_worktree(project_dir)
    kept: List[Dict[str, Any]] = []
    changed = False
    for l in data["lanes"]:
        if l.get("role") == "main" or l.get("name") == "main":
            kept.append(l)
            continue
        branch = l.get("branch", "")
        if not branch or not _branch_exists(main_wt, branch):
            changed = True
            continue
        wt = l.get("worktree", "")
        is_missing = not wt or not os.path.isdir(wt)
        if is_missing and not l.get("missing"):
            l["missing"] = True
            changed = True
        elif not is_missing and l.get("missing"):
            l.pop("missing", None)
            changed = True
        kept.append(l)
    if changed:
        data["lanes"] = kept
        valid_names = {l["name"] for l in kept}
        if data.get("default") and data["default"] not in valid_names:
            data["default"] = ""
        data["threads"] = {k: v for k, v in data.get("threads", {}).items() if v in valid_names}
        save(project_dir, data)
        _invalidate_status(project_dir)
    else:
        data["lanes"] = kept
    return data


def ensure_worktree(project_dir: str, name: str) -> Dict[str, Any]:
    """Recreate a missing worktree from its branch."""
    if name == "main":
        raise ValueError("Cannot recreate worktree for 'main' lane.")
    data = load(project_dir)
    target = next((l for l in data["lanes"] if l.get("name") == name), None)
    if target is None:
        raise ValueError(f"No lane '{name}'.")
    wt = target.get("worktree", "")
    if wt and os.path.isdir(wt):
        if target.pop("missing", None) is not None:
            save(project_dir, data)
        return target
    main_wt = _main_worktree(project_dir)
    branch = target.get("branch", "")
    if not branch or not _branch_exists(main_wt, branch):
        raise ValueError(f"Branch '{branch}' for lane '{name}' no longer exists.")
    if os.name == "nt":
        _git(main_wt, "config", "core.longpaths", "true")
    _git(main_wt, "worktree", "prune", check=False)
    _git(main_wt, "worktree", "add", wt, branch)
    target.pop("missing", None)
    save(project_dir, data)
    _invalidate_status(project_dir)
    return target


def status(project_dir: str) -> List[Dict[str, Any]]:
    """Return lanes enriched with ahead_main and dirty flags (cached 10s per project)."""
    pid = project_id(project_dir)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _STATUS_CACHE.get(pid)
        if cached and (now - cached[0]) < _STATUS_TTL:
            return [dict(item) for item in cached[1]]

    data = reconcile(project_dir)
    main_lane = next((l for l in data["lanes"] if l.get("role") == "main"), None)
    main_wt = main_lane["worktree"] if main_lane else _main_worktree(project_dir)
    main_branch = (main_lane or {}).get("branch") or "HEAD"

    result: List[Dict[str, Any]] = []
    for l in data["lanes"]:
        item = dict(l)
        wt = item.get("worktree", "")
        branch = item.get("branch", "")
        if item.get("role") == "main":
            item["ahead_main"] = 0
        elif branch and main_branch and _branch_exists(main_wt, branch):
            ahead_raw = _git(main_wt, "rev-list", "--count", f"{main_branch}..{branch}", check=False).strip()
            item["ahead_main"] = int(ahead_raw) if ahead_raw.isdigit() else 0
        else:
            item["ahead_main"] = 0

        if wt and os.path.isdir(wt):
            st = _git(wt, "status", "--porcelain", check=False)
            item["dirty"] = bool(st.strip())
        else:
            item["dirty"] = False
        result.append(item)

    with _CACHE_LOCK:
        _STATUS_CACHE[pid] = (now, [dict(item) for item in result])
    return result
