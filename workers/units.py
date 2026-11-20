"""Unit-chained workers: one manifest (.subway-units.json), one short fresh worker per unit.

Each unit runs in an isolated worktree and lands by merge before the next unit starts, so a
half-finished unit never leaves the main tree broken. A unit is done only when its worker
completed, its branch merged (HEAD moved) and its verify command exited 0.
The chain itself rides on the existing `then` machinery: each later unit is a nested follow-up step.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple

MANIFEST_NAME = ".subway-units.json"
VERIFY_TIMEOUT = 600
MAX_UNITS = 12
SUMMARY_CHARS = 600  # about 150 tokens
TODO, DONE, FAILED = "[ ]", "[x]", "[!]"
_UNIT_FIELDS = {"title", "prompt", "code", "verify", "after"}


def _git(cwd: str, *args: str) -> str:
    try:
        proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def head_sha(cwd: str) -> str:
    return _git(cwd, "rev-parse", "HEAD")


def _is_ancestor(cwd: str, sha: str) -> bool:
    if not sha:
        return False
    try:
        proc = subprocess.run(["git", "-C", cwd, "merge-base", "--is-ancestor", sha, "HEAD"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


@contextlib.contextmanager
def _lock():
    try:
        import server as _s
        ctx = _s._state_lock("units")
    except Exception:
        ctx = contextlib.nullcontext()
    with ctx:
        yield


def manifest_path(cwd: str) -> str:
    return os.path.join(cwd, MANIFEST_NAME)


def load_manifest(cwd: str) -> Optional[Dict[str, Any]]:
    try:
        with open(manifest_path(cwd), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("units"), list) or not data["units"]:
        return None
    for entry in data["units"]:
        if not isinstance(entry, dict) or entry.get("status") not in (TODO, DONE, FAILED):
            return None
    return data


def save_manifest(cwd: str, manifest: Dict[str, Any]) -> None:
    path = manifest_path(cwd)
    try:
        import store
        store._write_json_atomic(path, manifest)
        return
    except ImportError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    os.replace(tmp, path)


def validate_units(units: Any) -> Tuple[List[Dict[str, Any]], str]:
    """(clean units, error). Each unit: title, prompt, optional code (list of str), optional verify (str)."""
    if not isinstance(units, list) or not units:
        return [], "units must be a non-empty list of {title, prompt, code, verify}."
    if len(units) > MAX_UNITS:
        return [], f"units has {len(units)} entries; the limit is {MAX_UNITS}."
    clean: List[Dict[str, Any]] = []
    for i, u in enumerate(units):
        if not isinstance(u, dict):
            return [], f"units[{i}] must be an object."
        unknown = sorted(set(u) - _UNIT_FIELDS)
        if unknown:
            return [], f"unknown field(s) in units[{i}]: {', '.join(unknown)}."
        title = str(u.get("title") or "").strip()
        prompt = str(u.get("prompt") or "").strip()
        if not title or not prompt:
            return [], f"units[{i}] needs both title and prompt."
        code = u.get("code") or []
        if not isinstance(code, list) or any(not isinstance(c, str) or not c.strip() for c in code):
            return [], f"units[{i}].code must be a list of non-empty strings."
        verify = u.get("verify") or ""
        if not isinstance(verify, str):
            return [], f"units[{i}].verify must be a string."
        after = str(u.get("after") or "success").strip().lower()
        if after not in ("success", "any"):
            return [], f'units[{i}].after must be "success" or "any".'
        entry = {"title": title[:80], "prompt": prompt, "code": [c.strip() for c in code], "verify": verify.strip()}
        if after == "any":
            entry["after"] = "any"  # only stored when set, so existing manifests keep their hash
        clean.append(entry)
    return clean, ""


def units_hash(clean: List[Dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(clean, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def summary(manifest: Dict[str, Any]) -> str:
    lines = []
    for i, e in enumerate(manifest["units"]):
        sha = f" ({str(e.get('sha') or '')[:7]})" if e.get("sha") else ""
        lines.append(f"{e['status']} {i + 1}. {str(e.get('title') or '')[:60]}{sha}")
    text = "\n".join(lines)
    return text if len(text) <= SUMMARY_CHARS else text[:SUMMARY_CHARS - 1] + "…"


def unit_meta(clean: List[Dict[str, Any]], index: int, cwd: str) -> Dict[str, Any]:
    return {"index": index, "cwd": cwd, "title": clean[index]["title"], "verify": clean[index]["verify"],
            "brief": clean[index]["prompt"],
            "total": len(clean), "after": clean[index].get("after", "success")}


def unit_prompt(cwd: str, index: int, clean: Optional[List[Dict[str, Any]]] = None,
                pinned_verify: Optional[str] = None, pinned_brief: Optional[str] = None) -> str:
    """Manifest summary + this unit's brief. Built when the unit starts so the statuses are current.

    F6: verify/brief pinned at spin time (unit_meta/chain_steps) override the manifest file when
    provided — the manifest sits uncommitted in the tree and a tampered `verifies[i]` used to be
    executed with shell=True in the main checkout on the resume path."""
    manifest = load_manifest(cwd)
    if not manifest or index >= len(manifest["units"]):
        raise ValueError(f"no unit {index + 1} in {manifest_path(cwd)}")
    entry = manifest["units"][index]
    brief = (clean[index]["prompt"] if clean else
             (pinned_brief if pinned_brief is not None else (manifest.get("briefs") or [""] * (index + 1))[index]))
    verify = (clean[index]["verify"] if clean else
              (pinned_verify if pinned_verify is not None else (manifest.get("verifies") or [""] * (index + 1))[index]))
    n = len(manifest["units"])
    lines = [f"UNITS (you are unit {index + 1} of {n}; earlier units are finished and committed):",
             summary(manifest), "",
             f"Do ONLY unit {index + 1}: {entry['title']}", brief]
    if verify:
        lines += ["", f"Verify (must exit 0): {verify}"]
    lines += ["", "When done: run the verify command if there is one, then `git add <the files you changed>` "
              "(never git add -A; do not commit .subway-units.json) and `git commit -m \"<unit title>\"`. "
              "Stop right after committing."]
    return "\n".join(lines)


def chain_steps(clean: List[Dict[str, Any]], start: int, cwd: str) -> List[Dict[str, Any]]:
    """Nested follow-up steps for units start+1 .. end, for the `then` machinery."""
    nested: List[Dict[str, Any]] = []
    for i in range(len(clean) - 1, start, -1):
        nested = [{
            "role": f"Unit {i + 1}: {clean[i]['title']}"[:60],
            "prompt": "(built from .subway-units.json when this step fires)",
            "provider": "auto", "model": "", "include": "report",
            "when": "always" if clean[i].get("after") == "any" else "completed",
            "other_provider": False, "max_tokens": 0, "verdict": False,
            "unit": unit_meta(clean, i, cwd), "code": list(clean[i]["code"]), "then": nested,
        }]
    return nested


def plan(units: Any, cwd: str, max_tool_calls: int = 0) -> Tuple[Optional[Dict[str, Any]], str]:
    """Validate, write or resume the manifest. Returns (plan, error).
    plan = {"done_all": bool, "start": int, "role": str, "prompt": str, "code": [...], "steps": [...], "unit": {...}}"""
    clean, err = validate_units(units)
    if err:
        return None, err
    digest = units_hash(clean)
    with _lock():
        manifest = load_manifest(cwd)
        if not manifest or manifest.get("hash") != digest or len(manifest["units"]) != len(clean):
            base = head_sha(cwd)
            if not base:
                return None, "units need a git project with at least one commit (each unit must commit)."
            manifest = {"version": 1, "hash": digest, "base": base,
                        "call_pool": int(max_tool_calls or 0) * len(clean), "calls_used": 0,
                        "units": [{"title": u["title"], "status": TODO, "sha": ""} for u in clean]}
        else:
            for e in manifest["units"]:
                if e["status"] == DONE and not _is_ancestor(cwd, str(e.get("sha") or "")):
                    e["status"], e["sha"] = TODO, ""
                elif e["status"] == FAILED:
                    e["status"] = TODO
        manifest["briefs"] = [u["prompt"] for u in clean]
        manifest["verifies"] = [u["verify"] for u in clean]
        save_manifest(cwd, manifest)
    start = next((i for i, e in enumerate(manifest["units"]) if e["status"] != DONE), None)
    if start is None:
        return {"done_all": True, "start": len(clean), "summary": summary(manifest)}, ""
    return {"done_all": False, "start": start, "role": f"Unit {start + 1}: {clean[start]['title']}"[:60],
            "prompt": unit_prompt(cwd, start, clean), "code": list(clean[start]["code"]),
            "steps": chain_steps(clean, start, cwd), "unit": unit_meta(clean, start, cwd)}, ""


def call_pool_remaining(manifest: Optional[Dict[str, Any]]) -> int:
    """Calls left in a shared unit pool (floor 4). 0 when no pool is configured."""
    if not manifest:
        return 0
    pool = int(manifest.get("call_pool") or 0)
    if pool <= 0:
        return 0
    return max(4, pool - int(manifest.get("calls_used") or 0))


def _mark(cwd: str, index: int, status: str, sha: str = "", note: str = "") -> None:
    with _lock():
        manifest = load_manifest(cwd)
        if not manifest or index >= len(manifest["units"]):
            return
        entry = manifest["units"][index]
        entry["status"], entry["sha"] = status, sha
        if note:
            entry["note"] = note[:300]
        else:
            entry.pop("note", None)
        save_manifest(cwd, manifest)


def on_unit_done(worker_id: str, meta: Dict[str, Any], status: str,
                 run: Callable[..., Any] = subprocess.run) -> bool:
    """Called from _run_triggers before follow-ups fire. True only when the chain may continue."""
    unit = meta.get("unit") or {}
    cwd, index = str(unit.get("cwd") or ""), unit.get("index")
    if not cwd or not isinstance(index, int):
        return False
    manifest = load_manifest(cwd)
    if not manifest or index >= len(manifest["units"]):
        return False
    try:
        used = int(meta.get("tool_calls") or 0)
    except (TypeError, ValueError):
        used = 0
    if used:
        with _lock():
            fresh = load_manifest(cwd)
            if fresh:
                fresh["calls_used"] = int(fresh.get("calls_used") or 0) + used
                save_manifest(cwd, fresh)
                manifest = fresh
    if status != "completed":
        _mark(cwd, index, FAILED, note=f"worker {worker_id} ended {status}")
        return False
    if meta.get("isolate") and not meta.get("merged"):
        note = meta.get("land_note") or "clash or no changes"
        _mark(cwd, index, FAILED, note=f"worker {worker_id} branch not merged: {note}")
        return False
    sha = head_sha(cwd)
    # Compare against the last unit that actually landed: with after="any" the one just before may have failed.
    prior = next((str(e.get("sha") or "") for e in reversed(manifest["units"][:index])
                  if e.get("status") == DONE and e.get("sha")), manifest["base"])
    if not sha or sha == prior:
        _mark(cwd, index, FAILED, note=f"worker {worker_id} made no commit")
        return False
    verify = str(unit.get("verify") or "").strip()
    if verify:
        try:
            proc = run(verify, shell=True, cwd=cwd, capture_output=True, text=True, timeout=VERIFY_TIMEOUT)
            code = proc.returncode
            tail = ((getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or ""))[-200:]
        except (OSError, subprocess.SubprocessError) as exc:
            code, tail = 1, str(exc)
        if code != 0:
            _mark(cwd, index, FAILED, note=f"verify exited {code}: {tail}".strip())
            return False
    _mark(cwd, index, DONE, sha=sha)
    return True


def continue_steps(steps: List[Dict[str, Any]], ok: bool) -> List[Dict[str, Any]]:
    """Follow-up steps allowed after a unit ends: all when it landed, else only units marked after="any"."""
    if ok:
        return steps
    return [s for s in steps if (s.get("unit") or {}).get("after") == "any"]
