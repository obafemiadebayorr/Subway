"""Pull a thread from a peer station with token budget guard, origin metadata, and move semantics."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import peers
import thread_state
import threads
from transplant import engine


def estimate_tokens(ir_or_text: Any) -> int:
    """Estimate token count for an IR structure or raw text using engine.CHARS_PER_TOKEN."""
    if not ir_or_text:
        return 0
    if isinstance(ir_or_text, str):
        text = ir_or_text
    else:
        text = json.dumps(ir_or_text, sort_keys=True, ensure_ascii=False)
    chars_per_tok = getattr(engine, "CHARS_PER_TOKEN", 4) or 4
    return len(text) // chars_per_tok


def _compute_sha256(ir: Any) -> str:
    """Compute canonical SHA-256 hex digest for an IR payload or text."""
    if isinstance(ir, (dict, list)):
        raw = json.dumps(ir, sort_keys=True, ensure_ascii=False).encode("utf-8")
    else:
        raw = str(ir or "").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _register_origin(base_dir: str, local_id: str, station: str, thread_id: str, provider: str) -> None:
    """Persist origin metadata in thread_state and record in threads log."""
    origin = f"{station}/{thread_id}"
    with thread_state._locked(base_dir):
        data = thread_state._read(base_dir)
        entry = thread_state._copy(data.get(local_id))
        entry["origin"] = origin
        entry["origin_station"] = station
        entry["origin_id"] = thread_id
        entry["updated_at"] = time.time()
        data[local_id] = entry
        thread_state._write(base_dir, data)

    try:
        threads.record(
            base_dir,
            thread_id=local_id,
            kind="note",
            node=local_id,
            peer=station,
            data={
                "origin": origin,
                "origin_station": station,
                "origin_id": thread_id,
                "provider": provider,
            },
        )
    except ValueError:
        pass


def pull_thread(
    station: str,
    thread_id: str,
    *,
    max_tokens: int = 100000,
    archive: bool = False,
    target_provider: Optional[str] = None,
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Pull a thread from a peer station into destination state.

    F2-2: Checks estimate_tokens(ir) <= max_tokens before writing any destination files.
    F2-3: Imports IR via transplant.engine and registers origin metadata in thread_state/threads.
    F2-4: Archives remote thread only when archive=True and imported sha256 matches exported sha256.
    """
    peer_id = str(station or "").strip()
    tid = str(thread_id or "").strip()
    if not peer_id or not tid:
        raise ValueError("station and thread_id are required")

    bdir = base_dir or engine.state_dir()
    export_path = f"/api/peer/thread_export?id={urllib.parse.quote(tid, safe='')}"
    res = peers.call(peer_id, "GET", export_path)
    if not isinstance(res, dict) or res.get("error"):
        err = res.get("error") if isinstance(res, dict) else "peer call failed"
        raise RuntimeError(str(err))
    status = int(res.get("status") or 200)
    if status >= 400:
        raise RuntimeError(f"peer export failed ({status})")

    payload = res.get("data") if isinstance(res.get("data"), dict) else {}
    ir = payload.get("ir") if "ir" in payload else payload
    exported_sha256 = str(payload.get("sha256") or "").strip()
    attachments = payload.get("attachments") or []

    # F2-2: Enforce token budget BEFORE writing any local files.
    tokens = estimate_tokens(ir)
    if tokens > max_tokens:
        raise ValueError("thread exceeds max_tokens budget")

    local_id = f"pull_{peer_id}_{tid}"
    provider = str(
        target_provider
        or (ir.get("provider") if isinstance(ir, dict) else None)
        or ""
    ).strip()
    project_dir = str((ir.get("project_dir") if isinstance(ir, dict) else "") or "")

    # Fetch attachments after budget check passes.
    saved_attachments: List[str] = []
    if attachments:
        att_dir = os.path.join(bdir, "attachments", local_id)
        os.makedirs(att_dir, exist_ok=True)
        for item in attachments:
            att_path = item.get("path") if isinstance(item, dict) else str(item)
            if not att_path:
                continue
            f_res = peers.call(
                peer_id,
                "GET",
                f"/api/peer/file?path={urllib.parse.quote(att_path, safe='')}",
            )
            if isinstance(f_res, dict) and not f_res.get("error") and int(f_res.get("status") or 200) < 400:
                fname = os.path.basename(att_path) or "attachment"
                dest = os.path.join(att_dir, fname)
                content = (f_res.get("data") or {}).get("content", "") if isinstance(f_res.get("data"), dict) else str(f_res.get("data") or "")
                with open(dest, "w", encoding="utf-8") as fh:
                    fh.write(content)
                saved_attachments.append(dest)

    # F2-3: Import IR into transplant engine and register origin metadata.
    root, _rec = engine.start_conversation(local_id, provider, project_dir=project_dir)
    _register_origin(bdir, local_id, peer_id, tid, provider)

    # F2-4: Move semantics — archive remote only if hashes match.
    imported_sha256 = _compute_sha256(ir)
    hashes_match = bool(exported_sha256) and (imported_sha256 == exported_sha256)
    archived_remote = False
    if archive and hashes_match:
        arch_res = peers.call(peer_id, "POST", "/api/thread_action", {"id": tid, "action": "archive"})
        if isinstance(arch_res, dict) and not arch_res.get("error") and int(arch_res.get("status") or 200) < 400:
            archived_remote = True

    return {
        "ok": True,
        "thread_id": local_id,
        "root": root,
        "provider": provider,
        "tokens": tokens,
        "origin": f"{peer_id}/{tid}",
        "origin_station": peer_id,
        "origin_id": tid,
        "sha256": imported_sha256,
        "exported_sha256": exported_sha256,
        "hash_match": hashes_match,
        "archived_remote": archived_remote,
        "attachments": saved_attachments,
    }
