import json, queue, threading, time, uuid
import subway_home
from typing import Dict, Any, Optional, Set

class SSEBroadcaster:
    """Thread-safe Server-Sent Events broadcaster supporting simultaneous events and multiple subscribers (Roadmap Item 38a2)."""
    def __init__(self, max_queue_size: int = 256):
        self._lock = threading.Lock()
        self._subscribers: Set[queue.Queue] = set()
        self._max_queue = max_queue_size

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._max_queue)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def broadcast(self, event_type: str, data: Dict[str, Any], event_id: Optional[str] = None) -> int:
        """Broadcast an event to all simultaneous subscribers without blocking."""
        eid = event_id or uuid.uuid4().hex[:8]
        msg = f"id: {eid}\nevent: {event_type}\ndata: {json.dumps(data)}\n\n"
        with self._lock:
            subs = list(self._subscribers)
        delivered = 0
        for q in subs:
            try:
                q.put_nowait(msg)
                delivered += 1
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                    delivered += 1
                except Exception:
                    pass
        return delivered

_broadcaster = SSEBroadcaster()

def get_broadcaster() -> SSEBroadcaster:
    return _broadcaster

def broadcast_event(event_type: str, data: Dict[str, Any]) -> int:
    return _broadcaster.broadcast(event_type, data)

# --- Cross-process delivery -------------------------------------------------
# The broadcaster above is in-process, so an event produced by the MCP server (a
# different process from the HUD) could never reach /api/events subscribers. Events are
# therefore spooled to a file that the HUD tails. This is the same mechanism the rest of
# Subway uses (wake spools, compact requests): files, watched.

import os as _os

def _spool_path() -> str:
    base = subway_home.subway_dir()
    return _os.path.join(base, "sse_events.jsonl")


def publish(event_type: str, data: Dict[str, Any]) -> bool:
    """Queue an event for /api/events subscribers, from any process.

    Append-only JSONL so a writer crash cannot corrupt earlier events. The HUD tails and
    drains it; see start_spool_tailer().
    """
    try:
        path = _spool_path()
        _os.makedirs(_os.path.dirname(path), exist_ok=True)
        line = json.dumps({"event": event_type, "data": data, "at": time.time()}) + "\n"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
        return True
    except OSError:
        return False


_spool_listeners: list = []


def add_spool_listener(fn) -> None:
    """Register a callback invoked as fn(event_type, data) for each spool event, before broadcast."""
    if fn not in _spool_listeners:
        _spool_listeners.append(fn)


def _dispatch_line(rec: dict) -> None:
    """Run listeners then broadcast one parsed spool record."""
    event_type = str(rec.get("event") or "message")
    data = rec.get("data") or {}
    for fn in list(_spool_listeners):
        try:
            fn(event_type, data)
        except Exception:
            pass
    _broadcaster.broadcast(event_type, data)


def start_spool_tailer(interval: float = 0.5, max_bytes: int = 4 * 1024 * 1024) -> threading.Thread:
    """Watch the spool in the HUD process and broadcast new lines to subscribers."""
    state = {"offset": 0}

    def _tick() -> None:
        path = _spool_path()
        try:
            size = _os.path.getsize(path)
        except OSError:
            return
        if size < state["offset"]:
            state["offset"] = 0  # truncated or rotated underneath us
        if size == state["offset"]:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(state["offset"])
                chunk = fh.read()
                state["offset"] = fh.tell()
        except OSError:
            return
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            _dispatch_line(rec)
        if state["offset"] > max_bytes:
            # Nothing to keep: drain the file rather than grow without bound.
            try:
                with open(path, "w", encoding="utf-8"):
                    pass
                state["offset"] = 0
            except OSError:
                pass

    def _loop() -> None:
        while True:
            time.sleep(interval)
            try:
                _tick()
            except Exception:
                pass

    t = threading.Thread(target=_loop, name="subway-sse-spool", daemon=True)
    t.start()
    return t
