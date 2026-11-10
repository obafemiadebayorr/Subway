"""Runs queued while the Mac is asleep.

A closed lid must not lose work. The phone (or a chat) writes a spin request here instead of
dispatching it; the daemon drains the queue once the machine is awake again. Draining goes
through the normal `spin_worker` path, so the approval gate still applies: a queued run wakes
up as a pending approval the phone can accept, rather than a process that started itself.

Deliberately not a scheduler: no cron, no priorities, no retries beyond one attempt per drain.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

QUEUE_FILE = "wake_queue.json"


def _path(subway_dir: str) -> str:
    return os.path.join(subway_dir, QUEUE_FILE)


def _read(subway_dir: str) -> Dict[str, Any]:
    try:
        with open(_path(subway_dir), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"runs": []}
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        return {"runs": []}
    return data


def _write(subway_dir: str, data: Dict[str, Any]) -> None:
    os.makedirs(subway_dir, exist_ok=True)
    tmp = _path(subway_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, _path(subway_dir))


def enqueue(subway_dir: str, args: Dict[str, Any], note: str = "") -> Dict[str, Any]:
    """Record one spin request. `args` are the same fields `spin_worker` takes."""
    data = _read(subway_dir)
    run = {
        "id": "wq_" + uuid.uuid4().hex[:8],
        "created_at": time.time(),
        "note": note,
        "args": dict(args or {}),
        "status": "queued",
    }
    data["runs"].append(run)
    _write(subway_dir, data)
    return run


def listing(subway_dir: str, include_done: bool = False) -> List[Dict[str, Any]]:
    runs = _read(subway_dir)["runs"]
    if include_done:
        return runs
    return [r for r in runs if r.get("status") == "queued"]


def remove(subway_dir: str, run_id: str) -> bool:
    data = _read(subway_dir)
    before = len(data["runs"])
    data["runs"] = [r for r in data["runs"] if r.get("id") != run_id]
    if len(data["runs"]) == before:
        return False
    _write(subway_dir, data)
    return True


def drain(subway_dir: str, spin: Callable[..., str], limit: int = 5) -> List[Dict[str, Any]]:
    """Hand each queued run to `spin(**args)` once. A failure is recorded, never retried here."""
    data = _read(subway_dir)
    results: List[Dict[str, Any]] = []
    for run in data["runs"]:
        if run.get("status") != "queued":
            continue
        if len(results) >= limit:
            break
        args = run.get("args") or {}
        run["drained_at"] = time.time()
        try:
            text = spin(**args)
            run["status"] = "drained"
            run["result"] = str(text)[:500]
        except Exception as exc:  # A bad brief must not wedge the queue.
            run["status"] = "failed"
            run["result"] = f"{type(exc).__name__}: {exc}"
        results.append(run)
    if results:
        _write(subway_dir, data)
    return results
