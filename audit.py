"""Who did what: one JSON line per action taken from a device, the Mac's HUD or an agent (roadmap 53, layer 2).

Stored at <subway_dir>/audit.jsonl and rotated to audit.jsonl.1 past 5 MB. It holds no prompt, message or file
contents: only the action, its target ids and the actor.
"""
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

import subway_home

MAX_BYTES = 5 * 1024 * 1024
TARGET_KEYS = ("worker_id", "request_id", "device_id", "msg_id", "thread_id", "chat_id", "filepath", "track",
               "decision", "verdict", "provider", "model", "role", "name", "key")
_lock = threading.Lock()
_seq = 0


def _path() -> str:
    return os.path.join(subway_home.subway_dir(), "audit.jsonl")


def target_of(payload: Any) -> Dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    out = {}
    for k in TARGET_KEYS:
        v = payload.get(k)
        if isinstance(v, (str, int, float, bool)) and str(v) != "":
            out[k] = str(v)[:120]
    return out


MAC = {"kind": "mac", "id": "mac", "name": "This Mac"}
SPAWN_ACTIONS = frozenset({"/api/spawn", "/api/run_concurrent", "spin_worker", "spin_batch", "spin_swarm",
                           "run_concurrent"})


def _who(d: Dict[str, str]) -> Dict[str, str]:
    return {k: str(d.get(k, ""))[:80] for k in ("kind", "id", "name")}


def record(actor: Dict[str, str], action: str, target: Optional[Dict[str, str]] = None, ok: bool = True,
           origin: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """origin is the machine the action came from: the device itself, or This Mac for local agents."""
    global _seq
    if origin is None:
        origin = MAC if actor.get("kind") == "agent" else actor
    entry = {"ts": round(time.time(), 3), "actor": _who(actor), "origin": _who(origin),
             "action": str(action)[:80], "target": target or {}, "ok": bool(ok)}
    with _lock:
        p = _path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            if os.path.getsize(p) > MAX_BYTES:
                os.replace(p, p + ".1")
        except OSError:
            pass
        _seq += 1
        entry["seq"] = _seq
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    try:
        import sse_stream
        sse_stream.publish("audit", entry)
    except Exception:
        pass
    return entry


def recent(limit: int = 100, worker_id: str = "", actor_id: str = "") -> List[Dict[str, Any]]:
    """Newest first."""
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-5000:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if worker_id and (e.get("target") or {}).get("worker_id") != worker_id:
            continue
        if actor_id and (e.get("actor") or {}).get("id") != actor_id:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def started_by(worker_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """{worker_id: the oldest spawn entry naming it}."""
    want = set(worker_ids)
    out: Dict[str, Dict[str, Any]] = {}
    for e in reversed(recent(5000)):
        w = (e.get("target") or {}).get("worker_id")
        if w in want and w not in out and e.get("action") in SPAWN_ACTIONS:
            out[w] = e
    return out


def worker_id_in(data: Any) -> str:
    """The new worker's id in a spawn answer: a dict with worker_id/workers, or the MCP result text."""
    if isinstance(data, dict):
        if isinstance(data.get("worker_id"), str):
            return data["worker_id"]
        ws = data.get("workers")
        if isinstance(ws, list) and ws and isinstance(ws[0], dict) and isinstance(ws[0].get("worker_id"), str):
            return ws[0]["worker_id"]
        data = " ".join(c.get("text", "") for c in (data.get("content") or []) if isinstance(c, dict))
    import re
    m = re.search(r"worker_[0-9a-f]{6}", data if isinstance(data, str) else "")
    return m.group(0) if m else ""
