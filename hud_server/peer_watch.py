"""Follow workers this station started on linked stations (roadmap 66, P5b).

Every 10 s, for each remote worker that isn't finished, ask its station for its state. A change is saved on the
stub in peers.json and re-published as a local worker event named worker_x@station, so the HUD, the phone and
pushes treat it like a local worker. Finished workers keep their report on the stub.
"""
import threading
import time
from typing import Any, Callable, Dict

import peers

FINAL = {"completed", "failed", "stopped", "rejected", "budget_stopped", "timeout", "error", "cancelled"}
INTERVAL = 10.0
MAX_AGE = 48 * 3600
_started = False
_lock = threading.Lock()
_stop_event = threading.Event()
CLAIM_MISSES = 3  # consecutive unanswered checks before a linked station's claims are released
_misses: Dict[str, int] = {}


def claim_misses() -> int:
    """Missed checks (INTERVAL seconds each) before a linked station's claims are released. Setting: peer_claim_misses."""
    try:
        import server as _s
        v = int(_s._load_json("config.json").get("peer_claim_misses") or 6)
    except Exception:
        v = 6
    return max(2, min(60, v))


def check_claim_holders() -> int:
    """Heartbeat for claims made by linked stations: release them when the station is unlinked or stops answering."""
    from cli_tools import locks as _locks
    held = {str((v or {}).get("peer_id") or "") for v in ((_locks.load_locks() or {}).get("locks") or {}).values()}
    held.discard("")
    released = 0
    for pid in held:
        if not peers.get(pid):
            released += _locks.release_claims_for_peer(pid)
            _misses.pop(pid, None)
            continue
        res = peers.call(pid, "GET", "/api/peer/station", timeout=5)
        if res.get("status"):  # any HTTP answer, even 403, means the station is up
            _misses.pop(pid, None)
            continue
        _misses[pid] = _misses.get(pid, 0) + 1
        if _misses[pid] >= claim_misses():
            released += _locks.release_claims_for_peer(pid)
            _misses.pop(pid, None)
    return released


def _publish(stub: Dict[str, Any]) -> None:
    try:
        import sse_stream
        sse_stream.publish("worker", {"id": f"{stub['worker_id']}@{stub['station']}", "status": stub.get("status"),
                                      "role": stub.get("role"), "provider": stub.get("provider") or "",
                                      "station": stub["station"], "remote": True})
    except Exception:
        pass


def poll_once() -> int:
    """One pass. Returns how many stubs changed."""
    d = peers._data()
    stubs = d.get("remote_workers", {})
    changed = 0
    now = time.time()
    for wid, stub in list(stubs.items()):
        if stub.get("archived") or stub.get("status") in FINAL or now - float(stub.get("created_at") or 0) > MAX_AGE:
            continue
        peer = peers.get(stub.get("peer_id", ""))
        if not peer:
            continue
        res = peers.call(peer["peer_id"], "GET", "/api/peer/worker?id=" + wid)
        if res.get("status") != 200:
            if res.get("status") == 404:
                stub["status"] = "error"
                stub["report"] = "The linked station no longer has this worker."
                changed += 1
                _publish(stub)
            continue
        w = res.get("data") or {}
        new = {"status": w.get("status"), "provider": w.get("provider"), "model": w.get("model"),
               "spent": w.get("spent"), "started_at": w.get("started_at"), "finished_at": w.get("finished_at"),
               "has_patch": bool(w.get("has_patch"))}
        if w.get("status") in FINAL:
            new["report"] = str(w.get("report") or "")[:4000]
            try:
                new["media"] = peers.pull_media(stub) or stub.get("media") or []
            except Exception:
                pass
        if any(stub.get(k) != v for k, v in new.items()):
            status_changed = stub.get("status") != new["status"]
            stub.update(new)
            stub["checked_at"] = now
            changed += 1
            if status_changed:
                _publish(stub)
    if changed:
        fresh = peers._data()
        fresh.setdefault("remote_workers", {}).update({k: v for k, v in stubs.items() if k in fresh.get("remote_workers", {})})
        peers._save(fresh)
    return changed


def _loop(log: Callable[[str], None]) -> None:
    while not _stop_event.is_set():
        try:
            if peers._data().get("remote_workers"):
                poll_once()
        except Exception as e:  # never kill the HUD
            log(f"peer watch: {e}")
        try:
            check_claim_holders()
        except Exception as e:  # never kill the HUD
            log(f"peer watch claims: {e}")
        try:
            from hud_server import collab_api
            collab_api.maybe_handover()
        except Exception as e:  # never kill the HUD
            log(f"peer watch handover: {e}")
        if _stop_event.is_set():
            break
        time.sleep(INTERVAL)


def start(log: Callable[[str], None]) -> bool:
    global _started
    with _lock:
        if _started:
            return False
        _stop_event.clear()
        _started = True
    threading.Thread(target=_loop, args=(log,), daemon=True, name="subway-peer-watch").start()
    return True


def stop() -> bool:
    global _started
    with _lock:
        was_started = _started
        _started = False
        _stop_event.set()
    return was_started
