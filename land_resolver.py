"""Resolver workers for landing overlaps (roadmap 62c).

When a worker's change overlaps HEAD, _merge_worker_branch records the overlap in land_conflicts.json
and keeps the branch. A resolver is an isolated worker that merges that branch into a worktree of HEAD,
resolves the conflicted files so both sides keep working, and commits. Its own branch then lands the
normal way. Deletions that already landed on HEAD are listed so the resolver never brings them back
by accident.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

import land_conflicts
import server as _s

OPEN = ("needs_resolution", "resolving")


def _state_dir() -> str:
    return _s.SUBWAY_DIR


def find(conflict_id: str) -> Dict[str, Any]:
    cid = str(conflict_id or "").strip()
    return next((e for e in land_conflicts.list_conflicts(_state_dir()) if e.get("id") == cid), {})


def resolver_brief(entry: Dict[str, Any]) -> str:
    files = [str(f) for f in entry.get("conflicts") or []]
    deleted = [str(f) for f in entry.get("deletes_landed") or []]
    branch = str(entry.get("branch") or "")
    lines = [
        f"Resolve a landing overlap ({entry.get('id')}). Worker {entry.get('worker_id')} ({entry.get('role') or 'worker'}) "
        f"changed the same lines as HEAD, so its branch `{branch}` was kept instead of landing.",
        "You are in an isolated worktree of HEAD. Do exactly this:",
        f"1. `git merge --no-ff --no-commit {branch}`. It stops with conflicts in: {', '.join(files) or '(none listed)'}.",
        "2. For each conflicted file, keep BOTH intents: HEAD's change and the worker's change must both work. "
        "Where they truly contradict, keep HEAD's behaviour and add the worker's as an option or a clearly named "
        "second path, never silently drop either side. Remove every conflict marker.",
    ]
    if deleted:
        lines.append(f"3. These files or lines were deleted on HEAD and that deletion already landed: {', '.join(deleted)}. "
                     "Do not bring them back unless the worker's change cannot work without them; if so, say why in your report.")
    lines += [
        f"{4 if deleted else 3}. Run the tests that cover the conflicted files (grep tests/ for their module names) and make them pass.",
        f"{5 if deleted else 4}. `git add` the resolved files by name and `git commit --no-edit`. Never use git add -A, reset, stash or checkout.",
        "Report: per file, one line on how you combined the two sides, plus the pytest summary line.",
    ]
    return "\n".join(lines)


def t_list_conflicts(project_dir: str = "", include_closed: bool = False) -> str:
    """Landing overlaps waiting for a resolver (or all, with include_closed)."""
    repo = os.path.abspath(project_dir) if project_dir else ""
    rows = land_conflicts.list_conflicts(_state_dir(), repo)
    if not include_closed:
        rows = [e for e in rows if e.get("status") in OPEN]
    if not rows:
        return "No landing overlaps." if not include_closed else "No landing overlaps recorded."
    out = [f"{len(rows)} landing overlap(s):"]
    for e in rows:
        extra = f", resolver {e['resolver']}" if e.get("resolver") else ""
        dels = f"; deletions landed: {', '.join(e.get('deletes_landed')[:3])}" if e.get("deletes_landed") else ""
        out.append(f"- {e.get('id')} [{e.get('status')}{extra}] worker {e.get('worker_id')} branch {e.get('branch')}: "
                   f"{', '.join((e.get('conflicts') or [])[:5])}{dels}")
    return "\n".join(out)


def t_resolve_conflict(conflict_id: str, provider: str = "pi", model: str = "", max_tokens: int = 200000,
                       max_seconds: int = 1200, max_tool_calls: int = 30) -> str:
    """Queue an isolated resolver worker for one recorded landing overlap."""
    entry = find(conflict_id)
    if not entry:
        return f"Error: no landing overlap `{conflict_id}`. Use list_conflicts."
    if entry.get("status") != "needs_resolution":
        return f"Error: `{conflict_id}` is {entry.get('status')}" + (f" (resolver {entry['resolver']})." if entry.get("resolver") else ".")
    if not str(provider or "").strip() or provider == "auto":
        return "Error: pick a provider for the resolver (not auto)."
    import workers.spawn as spawn
    files: List[str] = [str(f) for f in entry.get("conflicts") or []][:6]
    kwargs: Dict[str, Any] = dict(
        role=f"Resolve landing overlap {entry['id']}", prompt=resolver_brief(entry), provider=provider, model=model,
        project_dir=str(entry.get("repo") or ""), isolate=True, doctor="off",
        max_tokens=int(max_tokens), max_seconds=int(max_seconds), max_tool_calls=int(max_tool_calls))
    result = spawn.t_spin_worker(code=files or None, **kwargs)
    if isinstance(result, str) and "could not resolve code names" in result:
        # Large files: send their first lines; the worker reads the conflict hunks itself.
        result = spawn.t_spin_worker(code=[f"{f}:1-120" for f in files] or None, **kwargs)
    if not isinstance(result, str) or result.startswith("Error"):
        return str(result)
    import re
    m = re.search(r"worker_[0-9a-f]{6}", result)
    if m:
        land_conflicts.resolve(_state_dir(), entry["id"], "resolving", note=f"resolver {m.group(0)}")
        data = land_conflicts._load(_state_dir())
        for e in data:
            if e.get("id") == entry["id"]:
                e["resolver"] = m.group(0)
        land_conflicts._save(_state_dir(), data)
    return result
