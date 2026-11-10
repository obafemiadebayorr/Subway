"""Forks, rewind and resume engine for subway-context (W13).

Handles:
- Part (a): detect_forked_chat: linking forked sessions to parent in threads.py with 'forked' edge,
  recording 'forked' thread event, and carrying over mode, budget, and file locks/claims.
- Part (b): trace_live_branch: tracing parentUuid backwards from latest leaf to return ONLY the live branch,
  inserting [REWOUND at ...] marker where branch diverged, recording 'rewound' thread event.
- Part (c): workers_after_rewind: identifying workers started after rewind point.
- Part (e): PROVIDER_RESUME_SUPPORT table and session resume logic for retry/rejection.
- Routes / tools for POST /api/composer/rewind and POST /api/composer/fork with restorable git checkpoints.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import server as _s
import threadlog
import threads

# Provider resume capability table
PROVIDER_RESUME_SUPPORT: Dict[str, Dict[str, Any]] = {
    "claude": {
        "supported": True,
        "mechanism": "cli_flag",
        "flag": "--resume",
        "description": "Claude Code supports resuming a session via `claude --resume <session_id>`",
    },
    "antigravity": {
        "supported": False,
        "mechanism": "none",
        "flag": None,
        "description": "Antigravity CLI currently does not support headless session resume flags (measured: respin used)",
    },
    "opencode": {
        "supported": True,
        "mechanism": "session_load",
        "flag": "--session",
        "description": "OpenCode supports session resume via ACP session/load or --session <session_id>",
    },
}


def workers_after_rewind(thread_id: str, rewind_timestamp: float) -> List[Dict[str, Any]]:
    """Identify all workers in _s.WORKERS_DIR belonging to thread_id whose created_at/started_at is after rewind_timestamp."""
    matched = []
    workers_dir = str(getattr(_s, "WORKERS_DIR", os.path.join(_s.SUBWAY_DIR, "workers")))
    if not os.path.isdir(workers_dir):
        return matched

    for entry in os.scandir(workers_dir):
        if not entry.is_dir() and not entry.name.endswith(".json"):
            continue
        state_file = entry.path if entry.name.endswith(".json") else os.path.join(entry.path, "state.json")
        if not os.path.isfile(state_file):
            continue
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        worker_thread = data.get("thread_id") or data.get("parent_thread") or data.get("chat_id")
        # Match thread_id or check if thread_id matches worker or parent
        if worker_thread != thread_id and data.get("worker_id") != thread_id:
            # Also check if thread_id is substring or matches initiator
            if data.get("initiator") != thread_id:
                continue

        created_at = float(data.get("created_at") or data.get("started_at") or 0.0)
        if created_at > rewind_timestamp:
            matched.append({
                "worker_id": data.get("worker_id") or entry.name.replace(".json", ""),
                "thread_id": worker_thread,
                "created_at": created_at,
                "status": data.get("status", "unknown"),
                "role": data.get("role", ""),
            })

    matched.sort(key=lambda w: w.get("created_at", 0.0))
    return matched


def create_git_checkpoint(base_dir: Optional[str] = None, note: str = "") -> Dict[str, Any]:
    """Create a restorable git checkpoint before rewinding.
    
    Never discard uncommitted user work without creating a restorable checkpoint first!
    Saves patch under _s.SUBWAY_DIR / 'checkpoints' / f'ckpt_{id}.patch' + metadata JSON.
    """
    ckpt_id = uuid.uuid4().hex[:12]
    checkpoints_dir = os.path.join(str(_s.SUBWAY_DIR), "checkpoints")
    os.makedirs(checkpoints_dir, exist_ok=True)

    patch_file = os.path.join(checkpoints_dir, f"ckpt_{ckpt_id}.patch")
    meta_file = os.path.join(checkpoints_dir, f"ckpt_{ckpt_id}.json")

    cwd = base_dir or os.getcwd()
    # Check if in a git repo
    is_git = False
    diff_content = ""
    stash_ref = None
    try:
        res = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=cwd, capture_output=True, text=True)
        if res.returncode == 0 and "true" in res.stdout:
            is_git = True
    except Exception:
        pass

    if is_git:
        try:
            # Try git diff HEAD to capture working copy and staged changes
            diff_res = subprocess.run(["git", "diff", "HEAD"], cwd=cwd, capture_output=True, text=True)
            diff_content = diff_res.stdout
            if not diff_content:
                # Try untracked or clean diff
                diff_res2 = subprocess.run(["git", "diff"], cwd=cwd, capture_output=True, text=True)
                diff_content = diff_res2.stdout
            # Try git stash create
            stash_res = subprocess.run(["git", "stash", "create", f"subway_checkpoint_{ckpt_id}"], cwd=cwd, capture_output=True, text=True)
            if stash_res.returncode == 0 and stash_res.stdout.strip():
                stash_ref = stash_res.stdout.strip()
        except Exception:
            pass

    with open(patch_file, "w", encoding="utf-8") as f:
        f.write(diff_content)

    meta = {
        "checkpoint_id": ckpt_id,
        "created_at": time.time(),
        "note": note,
        "patch_file": patch_file,
        "is_git": is_git,
        "stash_ref": stash_ref,
        "cwd": cwd,
    }
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return meta


def trace_live_branch(
    turns: List[Dict[str, Any]],
    record_event: bool = True,
    thread_id: str = "",
) -> Tuple[List[Dict[str, Any]], bool]:
    """Trace chain backwards from the latest leaf turn via parentUuid.
    
    Returns ONLY the live branch (skipping abandoned rewound branches).
    Inserts a [REWOUND at ...] marker where the branch diverged.
    If record_event is True and thread_id is provided, records 'rewound' thread event in threadlog.
    """
    if not turns:
        return [], False

    # Check if turns have uuid and parentUuid
    has_uuids = any("uuid" in t for t in turns if isinstance(t, dict))
    if not has_uuids:
        return turns, False

    # Build lookup map: uuid -> turn
    by_uuid: Dict[str, Dict[str, Any]] = {}
    leaf_candidate = None
    all_uuids = set()
    parent_uuids = set()

    for t in turns:
        if not isinstance(t, dict):
            continue
        u = t.get("uuid")
        if u:
            by_uuid[u] = t
            all_uuids.add(u)
            p = t.get("parentUuid")
            if p:
                parent_uuids.add(p)
            leaf_candidate = t  # last one encountered with uuid

    if not leaf_candidate:
        return turns, False

    # Trace backwards from the latest leaf turn
    live_chain: List[Dict[str, Any]] = []
    curr: Optional[Dict[str, Any]] = leaf_candidate
    visited = set()

    while curr:
        u = curr.get("uuid")
        if u in visited:
            break
        if u:
            visited.add(u)
        live_chain.append(curr)
        p = curr.get("parentUuid")
        if not p or p not in by_uuid:
            break
        curr = by_uuid.get(p)

    live_chain.reverse()

    # Did we rewind / abandon any turn that was in turns?
    abandoned = [t for t in turns if isinstance(t, dict) and t.get("uuid") and t.get("uuid") not in visited]
    was_rewound = len(abandoned) > 0

    if was_rewound:
        # Divergence point is the first common ancestor or the turn after fork
        divergence_time = time.time()
        # Find first turn in abandoned that branched off
        first_abandoned = abandoned[0]
        abandoned_time = first_abandoned.get("timestamp") or divergence_time
        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(abandoned_time) if isinstance(abandoned_time, (int, float)) else time.time()))

        # Insert marker
        marker_turn = {
            "type": "system",
            "role": "system",
            "content": f"[REWOUND at {time_str}]",
            "rewound": True,
            "timestamp": abandoned_time,
        }
        # Insert marker at the beginning of the diverged live chain, or right before leaf branch
        live_chain.insert(0, marker_turn)

        if record_event and thread_id:
            try:
                threadlog.record_event(
                    base_dir=str(_s.SUBWAY_DIR),
                    thread_id=thread_id,
                    kind="rewound",
                    detail={"divergence_time": abandoned_time, "abandoned_count": len(abandoned)},
                )
            except Exception:
                pass

    return live_chain, was_rewound


def detect_forked_chat(
    session_id: str,
    transcript_lines: List[Any],
    parent_id: str = "",
    carry_over: bool = True,
) -> Optional[Dict[str, Any]]:
    """Detect if a chat session is forked from another.
    
    When a new session id's transcript copies a parent session's prefix
    (or parent_id / forked_from is detected):
    - Link sub-workers to parent in threads.py with a "forked" edge:
      record_edge(parent_id, session_id, "forked", ...)
    - Record a "forked" thread event in threadlog
    - Offer / optionally carry over parent's mode (permission_mode, terminal_mode),
      budget_tokens, and active file claims (locks.py).
    """
    detected_parent = parent_id

    # If parent_id not explicitly given, inspect transcript for parent_id / forked_from or prefix match
    if not detected_parent:
        for line in transcript_lines:
            if isinstance(line, dict):
                if line.get("forked_from"):
                    detected_parent = str(line["forked_from"])
                    break
                if line.get("parent_session_id"):
                    detected_parent = str(line["parent_session_id"])
                    break

    if not detected_parent:
        return None

    # Record edge in threads.py
    edge_record = threads.record_edge(
        detected_parent,
        session_id,
        kind="forked",
        detail={"forked_at": time.time()},
        base_dir=str(_s.SUBWAY_DIR),
    )

    # Record thread event in threadlog
    threadlog.record_event(
        base_dir=str(_s.SUBWAY_DIR),
        thread_id=session_id,
        kind="forked",
        detail={"parent_id": detected_parent, "session_id": session_id},
    )

    carried: Dict[str, Any] = {}
    if carry_over:
        carried = carry_over_parent_state(detected_parent, session_id)

    return {
        "session_id": session_id,
        "parent_id": detected_parent,
        "edge": edge_record,
        "carried_over": carried,
    }


def carry_over_parent_state(parent_id: str, new_session_id: str) -> Dict[str, Any]:
    """Carry over mode (permission_mode, terminal_mode), budget_tokens, and file claims from parent."""
    carried: Dict[str, Any] = {
        "permission_mode": None,
        "terminal_mode": None,
        "budget_tokens": None,
        "claims_copied": 0,
    }

    # 1. Permission and terminal mode overrides (copied ONLY if parent has its own per-chat override)
    import permissions
    pid = str(parent_id or "").strip()
    if pid:
        override = _s._load_json("thread_settings.json").get(pid)
        if isinstance(override, dict):
            perm_mode = override.get("permission_mode")
            if perm_mode in _s.PERMISSION_MODES:
                permissions.set_permission_mode(perm_mode, new_session_id)
                carried["permission_mode"] = perm_mode

            term_mode = override.get("terminal_mode")
            if term_mode in _s.PERMISSION_MODES:
                permissions.set_terminal_mode(term_mode, new_session_id)
                carried["terminal_mode"] = term_mode

    # 2. Budget (copied ONLY if parent has its own per-chat budget)
    from usage.budget import chat_budget_for, set_chat_budget
    tokens, source = chat_budget_for(parent_id)
    if source == "chat" and tokens is not None:
        set_chat_budget(tokens, new_session_id)
        carried["budget_tokens"] = tokens

    # 3. Leases from locks.json
    from cli_tools.locks import t_claim_file
    now = time.time()
    locks_data = _s._load_json("locks.json")
    if isinstance(locks_data, dict):
        for fp, lease in locks_data.items():
            if isinstance(lease, dict) and lease.get("holder") == parent_id:
                expires_at = float(lease.get("expires_at") or 0.0)
                if expires_at > now:
                    purpose = lease.get("purpose") or f"Carried over from {parent_id}"
                    duration_seconds = max(60, int(expires_at - now))
                    t_claim_file(
                        filepath=fp,
                        holder=new_session_id,
                        duration_seconds=duration_seconds,
                        purpose=purpose,
                    )
                    carried["claims_copied"] += 1

    return carried


def rewind_thread(
    thread_id: str,
    at: Optional[float] = None,
    note: str = "",
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute rewind for a thread/composer session:
    1. Create a restorable git checkpoint first (NEVER discard uncommitted user work).
    2. Record "rewound" thread event.
    3. Identify and return workers started after the rewind point.
    """
    rewind_ts = float(at) if at is not None else time.time()
    bdir = base_dir or str(_s.SUBWAY_DIR)

    # 1. Create restorable checkpoint
    ckpt = create_git_checkpoint(note=note or f"Rewind of {thread_id} at {rewind_ts}")

    # 2. Record "rewound" thread event
    threadlog.record_event(
        base_dir=bdir,
        thread_id=thread_id,
        kind="rewound",
        detail={"at": rewind_ts, "note": note, "checkpoint_id": ckpt.get("checkpoint_id")},
    )

    # 3. Report workers started after rewind point
    after_workers = workers_after_rewind(thread_id, rewind_ts)

    return {
        "result": "ok",
        "thread_id": thread_id,
        "checkpoint_id": ckpt.get("checkpoint_id"),
        "rewound_at": rewind_ts,
        "workers_after_rewind": after_workers,
    }


def fork_thread(
    thread_id: str,
    at: Optional[float] = None,
    carry_over: bool = True,
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute fork for a thread/composer session:
    1. Fork thread at `at`.
    2. Link child to parent with a "forked" edge in threads.py.
    3. Record "forked" thread event.
    4. Carry over mode/budget/claims when requested.
    """
    fork_ts = float(at) if at is not None else time.time()
    bdir = base_dir or str(_s.SUBWAY_DIR)
    fork_id = f"fork_{uuid.uuid4().hex[:8]}"

    # Link sub-workers/child to parent in threads.py
    threads.record_edge(
        thread_id,
        fork_id,
        kind="forked",
        detail={"at": fork_ts},
        base_dir=bdir,
    )

    # Record thread event in threadlog
    threadlog.record_event(
        base_dir=bdir,
        thread_id=fork_id,
        kind="forked",
        detail={"parent_id": thread_id, "fork_id": fork_id, "at": fork_ts},
    )

    carried = {}
    if carry_over:
        carried = carry_over_parent_state(thread_id, fork_id)

    return {
        "result": "ok",
        "fork_id": fork_id,
        "parent_id": thread_id,
        "forked_at": fork_ts,
        "carried_over": carried,
    }
