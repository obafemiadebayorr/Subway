"""Composer actions backend (PKG-B2).

Handlers for:
- principal(payload) -> Dict[str, Any]
- rewind(payload) -> Dict[str, Any]
- compact(payload) -> Dict[str, Any]

Treats thread_state.py and forks.py as read-only.
Imports nothing from handlers.py.
"""

from __future__ import annotations

import json
import subway_home
import os
import time
from typing import Any, Dict, List, Optional

import forks
import thread_state
import threadlog
import threads


def stop_worker(worker_id: str, reason: str = "") -> Any:
    """Stop a running worker's process."""
    try:
        import server
        if hasattr(server, "t_stop_worker"):
            return server.t_stop_worker(worker_id, reason=reason)
    except Exception:
        pass
    try:
        import workers.spawn as wspawn
        if hasattr(wspawn, "t_stop_worker"):
            return wspawn.t_stop_worker(worker_id, reason=reason)
    except Exception:
        pass
    return None


def principal(payload: Dict[str, Any], base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Transfer principal baton for thread.

    Req: {"thread": str, "principal_id": str, "note": str, "auto_handoff": bool|null}
    Res: {"result": "[PRINCIPAL] <thread> -> <principal_id>"}
    Raises:
        ValueError: missing or empty thread/principal_id (400)
        KeyError: unknown thread (404)
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    thread = str(payload.get("thread") or "").strip()
    if not thread:
        raise ValueError("thread is required")

    principal_id = str(payload.get("principal_id") or "").strip()
    if not principal_id:
        raise ValueError("principal_id is required")

    note = str(payload.get("note") or "")
    auto_handoff = payload.get("auto_handoff")
    if auto_handoff is not None:
        auto_handoff = bool(auto_handoff)

    bdir = base_dir or payload.get("base_dir")

    res = thread_state.set_principal(
        thread_id=thread,
        principal_id=principal_id,
        note=note,
        auto_handoff=auto_handoff,
        base_dir=bdir,
    )

    if isinstance(res, dict) and res.get("error"):
        err = str(res["error"])
        if "unknown" in err.lower() or "not found" in err.lower():
            raise KeyError(err)
        raise ValueError(err)

    if isinstance(res, dict) and res.get("ok") is False:
        err = str(res.get("result") or res.get("message") or "set_principal failed")
        if "unknown" in err.lower() or "not found" in err.lower():
            raise KeyError(err)
        raise ValueError(err)

    out: Dict[str, Any] = {
        "result": f"[PRINCIPAL] {thread} -> {principal_id}",
        "thread": thread,
        "principal_id": principal_id,
    }
    if isinstance(res, dict):
        for k, v in res.items():
            if k != "result":
                out[k] = v

    return out


def initiator(payload: Dict[str, Any], base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Set the initiator (lineage root) for thread.

    Req: {"thread": str, "initiator_id": str, "note": str}
    Res: {"result": "[INITIATOR] <thread> -> <initiator_id>"}
    Raises:
        ValueError: missing or empty thread/initiator_id (400)
        KeyError: unknown thread (404)
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    thread = str(payload.get("thread") or "").strip()
    if not thread:
        raise ValueError("thread is required")

    initiator_id = str(payload.get("initiator_id") or "").strip()
    if not initiator_id:
        raise ValueError("initiator_id is required")

    note = str(payload.get("note") or "")
    bdir = base_dir or payload.get("base_dir")

    res = thread_state.set_initiator(
        thread_id=thread,
        initiator_id=initiator_id,
        note=note,
        base_dir=bdir,
    )

    if isinstance(res, dict) and res.get("error"):
        err = str(res["error"])
        if "unknown" in err.lower() or "not found" in err.lower():
            raise KeyError(err)
        raise ValueError(err)

    if isinstance(res, dict) and res.get("ok") is False:
        err = str(res.get("result") or res.get("message") or "set_initiator failed")
        if "unknown" in err.lower() or "not found" in err.lower():
            raise KeyError(err)
        raise ValueError(err)

    out: Dict[str, Any] = {
        "result": f"[INITIATOR] {thread} -> {initiator_id}",
        "thread": thread,
        "initiator_id": initiator_id,
    }
    if isinstance(res, dict):
        for k, v in res.items():
            if k != "result":
                out[k] = v

    return out


def rewind(payload: Dict[str, Any], base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Rewind thread to a checkpoint and stop any workers started after that point.

    Req: {"thread": str, "note": str}
    Res: {"result": "Rewound <thread> to <checkpoint sha7> (<n> workers stopped)"}
    Raises:
        ValueError: missing or empty thread (400)
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    thread = str(payload.get("thread") or "").strip()
    if not thread:
        raise ValueError("thread is required")

    note = str(payload.get("note") or "")
    bdir = base_dir or payload.get("base_dir")
    at = payload.get("at")

    res = forks.rewind_thread(thread_id=thread, at=at, note=note, base_dir=bdir)
    if isinstance(res, dict) and res.get("error"):
        err = str(res["error"])
        if "unknown" in err.lower() or "not found" in err.lower():
            raise KeyError(err)
        raise ValueError(err)

    ckpt_id = str(res.get("checkpoint_id") or "")
    sha7 = ckpt_id[:7]
    after_workers = res.get("workers_after_rewind") or []

    stopped_count = 0
    for w in after_workers:
        wid = w.get("worker_id") if isinstance(w, dict) else str(w)
        if wid:
            try:
                stop_worker(wid, reason=f"Rewound {thread}")
            except Exception:
                pass
            stopped_count += 1

    out: Dict[str, Any] = {
        "result": f"Rewound {thread} to {sha7} ({stopped_count} workers stopped)",
        "thread": thread,
        "checkpoint_id": ckpt_id,
        "checkpoint_sha": sha7,
        "stopped_workers": stopped_count,
        "workers_stopped": stopped_count,
    }
    if isinstance(res, dict):
        for k, v in res.items():
            if k != "result":
                out[k] = v

    return out


def fold_highlights(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fold threadlog entries into a highlights list."""
    if not entries:
        return []

    highlights: List[Dict[str, Any]] = []
    prev_key = None

    for idx, r in enumerate(entries):
        kind = str(r.get("kind") or "")
        node = str(r.get("node") or "")
        data = r.get("data") if isinstance(r.get("data"), dict) else {}
        note_text = str(data.get("note") or data.get("reason") or data.get("summary") or "")
        key = (kind, node, note_text)

        if key == prev_key and kind not in ("note", "forked", "rewound"):
            if highlights:
                highlights[-1]["count"] = highlights[-1].get("count", 1) + 1
            continue

        prev_key = key
        hl_id = f"hl_{len(highlights) + 1}"
        summary = note_text or f"{kind} on {node}" if node else kind

        highlights.append({
            "id": hl_id,
            "index": idx,
            "at": r.get("at"),
            "kind": kind,
            "node": node,
            "peer": r.get("peer") or "",
            "summary": summary,
            "data": data,
            "count": 1,
        })

    return highlights


def compact(payload: Dict[str, Any], base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Fold thread's threadlog entries into a highlights record.

    The raw log is never rewritten (compaction is additive, never destructive).
    Req: {"thread": str, "note": str}
    Res: {"result": "Compacted <thread>: <k> entries -> <m> highlights"}
    Raises:
        ValueError: missing or empty thread (400)
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    thread = str(payload.get("thread") or "").strip()
    if not thread:
        raise ValueError("thread is required")

    note = str(payload.get("note") or "")
    bdir = base_dir or payload.get("base_dir")

    entries = threadlog.thread_log_read(thread, base_dir=bdir)
    k = len(entries)

    if k == 0:
        highlights: List[Dict[str, Any]] = []
        m = 0
    else:
        highlights = fold_highlights(entries)
        m = len(highlights)

    now = time.time()

    # Store highlights under thread's state in thread_state
    try:
        with thread_state._locked(bdir):
            data = thread_state._read(bdir)
            entry = thread_state._copy(data.get(thread))
            entry["highlights"] = highlights
            entry["highlights_count"] = m
            entry["entries_count"] = k
            entry["compacted_at"] = now
            if note:
                entry["compaction_note"] = note
            data[thread] = entry
            thread_state._write(bdir, data)
    except Exception:
        pass

    # Also store dedicated highlights file under threads/
    try:
        target_dir = bdir
        if not target_dir:
            try:
                import server as _s
                target_dir = getattr(_s, "SUBWAY_DIR", None)
            except Exception:
                target_dir = None
        if not target_dir:
            target_dir = subway_home.subway_dir()

        th_dir = os.path.join(target_dir, "threads")
        os.makedirs(th_dir, exist_ok=True)
        s_name = threads.safe_name(thread)
        hl_file = os.path.join(th_dir, f"{s_name}_highlights.json")
        with open(hl_file, "w", encoding="utf-8") as f:
            json.dump({
                "thread": thread,
                "entries_count": k,
                "highlights_count": m,
                "compacted_at": now,
                "note": note,
                "highlights": highlights,
            }, f, indent=2)
    except Exception:
        pass

    return {
        "result": f"Compacted {thread}: {k} entries -> {m} highlights",
        "thread": thread,
        "entries_count": k,
        "highlights_count": m,
        "highlights": highlights,
    }
