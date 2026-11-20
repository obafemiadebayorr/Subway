import json
import os
import secrets
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Set


def _run_git(repo: str, args: List[str], **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo] + args,
        capture_output=True,
        **kwargs,
    )


def _changed_paths(repo: str, base: str, ref: str) -> Set[str]:
    p = _run_git(repo, ["diff", "--name-only", base, ref, "--"])
    if p.returncode != 0:
        return set()
    out = p.stdout.decode("utf-8", "replace")
    return {line for line in out.splitlines() if line}


def _added_by_head(repo: str, base: str, head: str, path: str) -> Set[str]:
    p = _run_git(repo, ["diff", "-U0", base, head, "--", path])
    if p.returncode != 0:
        return set()
    added: Set[str] = set()
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            added.add(line[1:])
    return added


def _file_lines(repo: str, spec: str) -> Optional[List[str]]:
    p = _run_git(repo, ["show", spec])
    if p.returncode != 0:
        return None
    return p.stdout.decode("utf-8", "replace").splitlines()


def _merged_result_lines(repo: str, tree: str, side: str, path: str, conflicted: bool) -> Optional[List[str]]:
    if conflicted:
        return _file_lines(repo, f"{side}:{path}")
    if tree:
        lines = _file_lines(repo, f"{tree}:{path}")
        if lines is not None:
            return lines
    return _file_lines(repo, f"{side}:{path}")


def _compute_deletes_landed(
    repo: str, base: str, side: str, head: str, tree: str, conflicts: List[str]
) -> List[str]:
    both = _changed_paths(repo, base, head) & _changed_paths(repo, base, side)
    if not both:
        return []
    conflict_set = set(conflicts)
    result: List[str] = []
    for path in sorted(both):
        # A file deleted by side that head modified counts too.
        side_exists = _run_git(repo, ["cat-file", "-e", f"{side}:{path}"]).returncode == 0
        if not side_exists:
            result.append(path)
            continue

        added = _added_by_head(repo, base, head, path)
        if not added:
            continue
        head_lines = _file_lines(repo, f"{head}:{path}")
        if head_lines is None:
            continue
        merged_lines = _merged_result_lines(repo, tree, side, path, path in conflict_set)
        merged_set = set(merged_lines) if merged_lines is not None else set()
        lost = (added & set(head_lines)) - merged_set
        if lost:
            result.append(path)
        elif merged_lines is None:
            # merged result missing: head-added lines not present there
            if added & set(head_lines):
                result.append(path)
    return result


def three_way(repo: str, base: str, side: str, head: str = "HEAD") -> dict:
    try:
        p = _run_git(
            repo,
            [
                "merge-tree",
                "--write-tree",
                f"--merge-base={base}",
                "--name-only",
                "--messages",
                "-z",
                head,
                side,
            ],
        )
        if p.returncode not in (0, 1):
            err_src = p.stderr or p.stdout or b""
            err_text = err_src.decode("utf-8", "replace").strip()
            err_line = err_text.splitlines()[0] if err_text else f"exit {p.returncode}"
            return {
                "clean": False,
                "tree": "",
                "conflicts": [],
                "deletes_landed": [],
                "error": err_line,
            }

        out = p.stdout.decode("utf-8", "replace")
        parts = out.split("\0")
        tree = parts[0] if parts else ""
        conflicts: List[str] = []
        i = 1
        # conflicted path names until an empty field; then messages
        while i < len(parts):
            if parts[i] == "":
                i += 1
                break
            conflicts.append(parts[i])
            i += 1

        clean = p.returncode == 0
        deletes_landed = _compute_deletes_landed(repo, base, side, head, tree, conflicts)
        return {
            "clean": clean,
            "tree": tree,
            "conflicts": conflicts,
            "deletes_landed": deletes_landed,
            "error": "",
        }
    except Exception as e:
        msg = str(e).strip().splitlines()[0] if str(e).strip() else "error"
        return {
            "clean": False,
            "tree": "",
            "conflicts": [],
            "deletes_landed": [],
            "error": msg,
        }


def _lc_path(state_dir: str) -> str:
    return os.path.join(state_dir, "land_conflicts.json")


def _load(state_dir: str) -> List[dict]:
    path = _lc_path(state_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def _save(state_dir: str, data: List[dict]) -> None:
    os.makedirs(state_dir, exist_ok=True)
    path = _lc_path(state_dir)
    fd, tmp = tempfile.mkstemp(dir=state_dir, prefix=".lc_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def record(state_dir: str, entry: dict) -> str:
    cid = "lc_" + secrets.token_hex(4)
    stored = {
        "id": cid,
        "repo": entry.get("repo", ""),
        "branch": entry.get("branch", ""),
        "worker_id": entry.get("worker_id", ""),
        "role": entry.get("role", ""),
        "base": entry.get("base", ""),
        "conflicts": list(entry.get("conflicts", [])),
        "deletes_landed": list(entry.get("deletes_landed", [])),
        "status": "needs_resolution",
        "created": int(time.time()),
        "note": entry.get("note", ""),
    }
    data = _load(state_dir)
    data.append(stored)
    _save(state_dir, data)
    return cid


_TERMINAL_RESOLVER_STATUSES = {"failed", "stopped", "budget_stopped", "rejected", "timed_out", "error"}


def list_conflicts(state_dir: str, repo: str = "") -> list:
    data = _load(state_dir)
    dirty = False
    for e in data:
        if e.get("status") == "resolving" and e.get("resolver"):
            wpath = os.path.join(state_dir, "workers", f"{e['resolver']}.json")
            wstatus = None
            if os.path.exists(wpath):
                try:
                    with open(wpath, encoding="utf-8") as fh:
                        wstatus = json.load(fh).get("status")
                except (OSError, ValueError):
                    wstatus = None
            if not os.path.exists(wpath) or wstatus in _TERMINAL_RESOLVER_STATUSES:
                e["status"] = "needs_resolution"
                e["last_failed_resolver"] = e.pop("resolver", "")
                dirty = True
    if dirty:
        _save(state_dir, data)
    if repo:
        return [e for e in data if e.get("repo") == repo]
    return list(data)


def resolve(state_dir: str, cid: str, status: str, note: str = "") -> bool:
    if status not in {"needs_resolution", "resolving", "resolved", "dropped"}:
        return False
    data = _load(state_dir)
    for e in data:
        if e.get("id") == cid:
            e["status"] = status
            e["note"] = note
            _save(state_dir, data)
            return True
    return False
