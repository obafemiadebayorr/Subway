"""Automatic worktree cleanup and worker-log rotation (roadmap R5).

sweep() removes finished workers' worktrees that are clean and whose work is safe in git; rotate_logs()
gzips old worker logs. sweep_if_enabled() is what the worker-finish hook calls: it does nothing unless the
`worktree_cleanup` setting is `on`. Nothing here ever forces a removal or raises into its caller.
"""
from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

import server as _s


def _git(cwd: str, *args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=60)


def _one_line(text: str, limit: int = 160) -> str:
    return " ".join(str(text or "").split())[:limit]


def _decide(worker_id: str, path: str, min_age_s: float, orphan_age_s: float, now: float) -> Tuple[bool, str, str]:
    """(removable, reason, repo). repo is only filled in when removable."""
    meta = _s._load_worker(worker_id) if _s.WORKER_ID_RE.match(worker_id) else None
    age = now - os.path.getmtime(path)
    if meta:
        if meta.get("status") in _s.ACTIVE_WORKER_STATES:
            return False, "worker still active", ""
        if age < min_age_s:
            return False, "too new", ""
    elif age < orphan_age_s:
        return False, "no worker record and younger than the orphan age", ""
    if not os.path.exists(os.path.join(path, ".git")):
        return False, "not a git worktree", ""
    status = _git(path, "status", "--porcelain", "--untracked-files=all")
    if status.returncode != 0:
        return False, "git status failed", ""
    dirty = [line for line in status.stdout.splitlines()
             if line.strip() and not line[3:].strip().startswith("node_modules")]
    if dirty:
        return False, f"{len(dirty)} uncommitted change(s)", ""
    common = _git(path, "rev-parse", "--git-common-dir")
    if common.returncode != 0 or not common.stdout.strip():
        return False, "cannot find the main repository", ""
    common_dir = os.path.abspath(os.path.join(path, common.stdout.strip()))
    repo = os.path.dirname(common_dir)
    if not os.path.isdir(repo):
        return False, "main repository is missing", ""
    if _git(path, "symbolic-ref", "-q", "HEAD").returncode != 0:  # detached HEAD
        head = _git(path, "rev-parse", "HEAD").stdout.strip()
        if not head or not _git(repo, "branch", "--contains", head).stdout.strip():
            return False, "commits not on any branch", ""
    return True, "clean", repo


def _drop_node_modules_link(path: str) -> None:
    """`git worktree remove` refuses on an untracked node_modules symlink. Remove the symlink itself
    (never the target directory) so the removal can proceed without --force."""
    link = os.path.join(path, "node_modules")
    try:
        if os.path.islink(link):
            os.unlink(link)
    except OSError:
        pass


def sweep(dry_run: bool = False, min_age_s: float = 60, orphan_age_s: float = 86400) -> Dict[str, Any]:
    """Remove safe worktrees. Returns {"removed": [ids], "kept": [{"id", "reason"}], "dry_run": bool}."""
    result: Dict[str, Any] = {"removed": [], "kept": [], "dry_run": bool(dry_run)}
    root = os.path.join(_s.SUBWAY_DIR, "worktrees")
    if not os.path.isdir(root):
        return result
    repos = set()
    now = time.time()
    for worker_id in sorted(os.listdir(root)):
        path = os.path.join(root, worker_id)
        if os.path.islink(path) or not os.path.isdir(path):
            continue
        try:
            ok, reason, repo = _decide(worker_id, path, min_age_s, orphan_age_s, now)
        except Exception as exc:
            ok, reason, repo = False, f"error: {_one_line(str(exc), 100)}", ""
        if not ok:
            result["kept"].append({"id": worker_id, "reason": reason})
            continue
        if dry_run:
            result["removed"].append(worker_id)
            continue
        _drop_node_modules_link(path)
        try:
            res = _git(repo, "worktree", "remove", path)
        except Exception as exc:
            result["kept"].append({"id": worker_id, "reason": f"error: {_one_line(str(exc), 100)}"})
            continue
        if res.returncode != 0:
            result["kept"].append({"id": worker_id, "reason": f"git refused: {_one_line(res.stderr)}"})
            continue
        repos.add(repo)
        result["removed"].append(worker_id)
    for repo in repos:
        try:
            _git(repo, "worktree", "prune")
        except Exception:
            pass
    return result


def rotate_logs(days: Optional[int] = None, dry_run: bool = False) -> Dict[str, Any]:
    """Gzip finished workers' logs older than `days` (default: the worker_log_days setting; 0 = off)."""
    out: Dict[str, Any] = {"compressed": [], "saved_bytes": 0, "dry_run": bool(dry_run)}
    if days is None:
        from hud_server import settings_api
        days = settings_api.value("worker_log_days")
    days = int(days or 0)
    folder = _s.WORKERS_DIR
    if days <= 0 or not os.path.isdir(folder):
        return out
    cutoff = time.time() - days * 86400
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".log"):
            continue
        worker_id = name[:-4]
        if not _s.WORKER_ID_RE.match(worker_id):
            continue
        path = os.path.join(folder, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_mtime > cutoff or st.st_size == 0 or os.path.exists(path + ".gz"):
            continue
        meta = _s._load_worker(worker_id)
        if meta and meta.get("status") in _s.ACTIVE_WORKER_STATES:
            continue
        if dry_run:
            out["compressed"].append(worker_id)
            continue
        tmp = path + ".gz.tmp"
        try:
            with open(path, "rb") as src, gzip.open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
            os.replace(tmp, path + ".gz")
            os.remove(path)
            out["saved_bytes"] += max(0, st.st_size - os.path.getsize(path + ".gz"))
            out["compressed"].append(worker_id)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return out


def sweep_if_enabled() -> Optional[Dict[str, Any]]:
    """The worker-finish hook: sweep and rotate when worktree_cleanup is `on`, else do nothing. Never raises."""
    try:
        from hud_server import settings_api
        if settings_api.value("worktree_cleanup") != "on":
            return None
        with _s._state_lock("worktree_cleanup", timeout=2):
            res = sweep()
            res["logs"] = rotate_logs()
            return res
    except Exception:
        return None


def run_cli(argv: List[str]) -> int:
    """subway cleanup [--dry-run] [--logs]"""
    dry = "--dry-run" in argv
    res = sweep(dry_run=dry)
    verb = "would remove" if dry else "removed"
    print(f"[CLEANUP] {verb} {len(res['removed'])} worktree(s), kept {len(res['kept'])}")
    if res["removed"]:
        print("worktrees: " + ", ".join(res["removed"][:20]))
    for k in res["kept"][:20]:
        print(f"kept {k['id']}: {k['reason']}")
    if "--logs" in argv:
        logs = rotate_logs(dry_run=dry)
        verb = "would compress" if dry else "compressed"
        print(f"[LOGS] {verb} {len(logs['compressed'])} log(s), saved {logs['saved_bytes']} bytes")
    return 0
