"""Concurrency tool: run_concurrent for running independent tasks in parallel with isolated workers."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

import server as _s
from cli_tools import locks as _locks


def _extract_task_files(task: Dict[str, Any]) -> List[str]:
    """Extract and canonicalize target files declared for a task."""
    files_val = task.get("files") or task.get("target_files") or []
    if isinstance(files_val, str):
        files_val = [files_val]
    elif not isinstance(files_val, list):
        files_val = []
    
    canon_files = []
    for f in files_val:
        if isinstance(f, str) and f.strip():
            canon_files.append(_locks._canon_path(f.strip()))
    return canon_files


def run_concurrent(tasks: List[Dict[str, Any]], track: str = "main") -> Dict[str, Any]:
    """Execute multiple independent tasks concurrently using isolated workers.
    
    Validates that:
    1. tasks is a non-empty list of task dicts.
    2. No task declares dependencies (depends_on, after_worker, wait_for).
    3. No two tasks declare overlapping target files.
    4. None of the declared files are currently locked by another agent.
    
    If valid, acquires file leases and spawns each worker with isolate=True.
    """
    if not tasks or not isinstance(tasks, list):
        return {
            "error": "Error: tasks must be a non-empty list of task specifications.",
            "status": "refused",
            "workers": [],
            "tasks": tasks or []
        }

    # 1. Validate task structure and check for dependency specifications
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            return {
                "error": f"Error: dependent or conflicting tasks cannot use run_concurrent: task #{i+1} is not a valid task dict.",
                "status": "refused",
                "workers": [],
                "tasks": tasks
            }
        
        # Check explicit dependency keys
        dep_keys = ["depends_on", "after_worker", "wait_for", "after", "parent_task", "requires"]
        found_deps = [k for k in dep_keys if t.get(k)]
        if found_deps:
            return {
                "error": f"Error: dependent or conflicting tasks cannot use run_concurrent: task #{i+1} specifies dependencies ({', '.join(found_deps)}). Use sequential execution instead.",
                "status": "refused",
                "workers": [],
                "tasks": tasks
            }
        
        # Prompt must be specified
        if not t.get("prompt"):
            return {
                "error": f"Error: dependent or conflicting tasks cannot use run_concurrent: task #{i+1} missing required 'prompt'.",
                "status": "refused",
                "workers": [],
                "tasks": tasks
            }

    # 2. Check for overlapping target files across tasks in the batch
    claimed_in_batch: Dict[str, int] = {}
    task_files_map: List[List[str]] = []
    
    for i, t in enumerate(tasks):
        files = _extract_task_files(t)
        task_files_map.append(files)
        for f in files:
            if f in claimed_in_batch:
                first_idx = claimed_in_batch[f]
                return {
                    "error": f"Error: dependent or conflicting tasks cannot use run_concurrent: tasks #{first_idx+1} and #{i+1} declare overlapping target file '{f}'. Parallel execution would cause collisions.",
                    "status": "refused",
                    "workers": [],
                    "tasks": tasks
                }
            claimed_in_batch[f] = i

    # 3. Check for active existing locks held by another agent
    active_locks_data = _locks.load_locks()
    active_locks = active_locks_data.get("locks", {})
    
    # Identify self / caller
    caller_det = _s._detected_chat()
    caller_id = caller_det["id"] if caller_det else _s._self_id()
    
    for f in claimed_in_batch:
        if f in active_locks:
            lock_info = active_locks[f]
            holder = lock_info.get("holder", "unknown")
            # If held by someone else, refuse
            if not _s._id_matches(holder, caller_id):
                return {
                    "error": f"Error: dependent or conflicting tasks cannot use run_concurrent: target file '{f}' is already leased by '{holder}'.",
                    "status": "refused",
                    "workers": [],
                    "tasks": tasks
                }

    # 4. Acquire file leases for all declared files
    group_id = f"conc_{uuid.uuid4().hex[:8]}"
    for i, t in enumerate(tasks):
        files = task_files_map[i]
        role = t.get("role", "Worker")
        for f in files:
            # Lease each target file for the caller / group
            _locks.t_claim_file(
                filepath=f,
                purpose=f"run_concurrent [{group_id}] task {i+1}: {role}",
                duration_seconds=t.get("max_seconds", 600) + 120
            )

    # 5. Launch each task with isolate=True
    workers = []
    for i, t in enumerate(tasks):
        role = t.get("role", f"Worker #{i+1}")
        prompt = t.get("prompt", "")
        provider = t.get("provider", "auto")
        model = t.get("model", "")
        max_tokens = t.get("max_tokens")
        max_seconds = t.get("max_seconds")
        effort = t.get("effort")
        permission_mode = t.get("permission_mode", "ask")
        
        # Build kwargs for t_spin_worker
        spin_kwargs: Dict[str, Any] = {
            "role": role,
            "prompt": prompt,
            "provider": provider,
            "track": track,
            "isolate": True,  # Critical: isolated worktree copy
            "permission_mode": permission_mode,
        }
        if model:
            spin_kwargs["model"] = model
        if max_tokens is not None:
            spin_kwargs["max_tokens"] = max_tokens
        if max_seconds is not None:
            spin_kwargs["max_seconds"] = max_seconds
        if effort:
            spin_kwargs["effort"] = effort
        
        # Call t_spin_worker
        try:
            res_str = _s.t_spin_worker(**spin_kwargs)
        except Exception as e:
            res_str = f"Error: failed to spin worker: {e}"
        
        # Extract worker id from result string if possible
        worker_id = None
        if "worker_" in str(res_str):
            for word in str(res_str).replace("`", "").replace("'", "").replace('"', "").split():
                if word.startswith("worker_"):
                    worker_id = word.rstrip(".,;:)")
                    break
        
        workers.append({
            "task_index": i,
            "role": role,
            "worker_id": worker_id,
            "status": "spawned" if (worker_id or not res_str.startswith("Error")) else "error",
            "message": res_str,
            "target_files": task_files_map[i],
            "isolate": True
        })

    return {
        "result": f"Successfully launched {len(workers)} concurrent worker(s) in group {group_id}.",
        "group_id": group_id,
        "status": "launched",
        "workers": workers,
        "tasks": tasks
    }


def t_run_concurrent(tasks: List[Dict[str, Any]], track: str = "main") -> str:
    """Tool wrapper returning formatted string summary for run_concurrent."""
    out = run_concurrent(tasks=tasks, track=track)
    if out.get("error"):
        return str(out["error"])
    
    lines = [f"{out.get('result')}\nGroup ID: {out.get('group_id')}\nWorkers:"]
    for w in out.get("workers", []):
        wid = w.get("worker_id") or "pending"
        status = w.get("status")
        role = w.get("role")
        files = ", ".join(w.get("target_files", [])) or "none"
        lines.append(f"- [{status}] {wid} ({role}) | Target files: {files}")
        if w.get("message") and w.get("message") != wid:
            lines.append(f"  Details: {w.get('message')}")
    return "\n".join(lines)
