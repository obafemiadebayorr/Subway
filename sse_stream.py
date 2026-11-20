import hashlib, json, os as _os, queue, select, threading, time, urllib.parse, uuid
from collections import deque
import subway_home
from typing import Any, Callable, Dict, Optional, Set

# --- Monotonic event ids -----------------------------------------------------
# iOS suspends an app and drops its SSE socket, so a reconnecting phone must be able to say
# "I already have up to 417" and receive only what it missed. Ids are therefore integers,
# persisted, and never reused across a restart. The uuid form stays as a fallback for
# callers that pass an explicit event_id.

SEQ_REPLAY = 200


def _seq_path() -> str:
    base = subway_home.subway_dir()
    return _os.path.join(base, "sse_seq")


def _load_seq() -> int:
    try:
        with open(_seq_path(), "r", encoding="utf-8") as fh:
            return max(0, int((fh.read() or "0").strip() or 0))
    except (OSError, ValueError):
        return 0


_seq_lock = threading.Lock()
_seq = _load_seq()


def next_seq() -> int:
    """Reserve the next event id. Persisted best-effort: a lost file costs a re-send, not a gap."""
    global _seq
    with _seq_lock:
        _seq += 1
        value = _seq
        try:
            path = _seq_path()
            _os.makedirs(_os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(str(value))
        except OSError:
            pass
        return value


def last_seq() -> int:
    with _seq_lock:
        return _seq


class SSEBroadcaster:
    """Thread-safe Server-Sent Events broadcaster supporting simultaneous events and multiple subscribers (Roadmap Item 38a2)."""
    def __init__(self, max_queue_size: int = 256):
        self._lock = threading.Lock()
        self._subscribers: Set[queue.Queue] = set()
        self._max_queue = max_queue_size
        self._recent: list = []  # [(seq, frame)] for replay, oldest first

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
        seq = next_seq()
        eid = event_id or str(seq)
        msg = f"id: {eid}\nevent: {event_type}\ndata: {json.dumps(data)}\n\n"
        with self._lock:
            self._recent.append((seq, msg))
            if len(self._recent) > SEQ_REPLAY:
                del self._recent[: len(self._recent) - SEQ_REPLAY]
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

    def replay_since(self, since: int) -> list:
        """Frames newer than `since`, oldest first. Empty when the client is up to date."""
        with self._lock:
            return [msg for seq, msg in self._recent if seq > since]

    def last_seq(self) -> int:
        with self._lock:
            return self._recent[-1][0] if self._recent else last_seq()

    def oldest_seq(self):
        """Seq of the oldest frame still kept for replay, or None when nothing is kept."""
        with self._lock:
            return self._recent[0][0] if self._recent else None


_broadcaster = SSEBroadcaster()

def get_broadcaster() -> SSEBroadcaster:
    return _broadcaster

def broadcast_event(event_type: str, data: Dict[str, Any]) -> int:
    result = _broadcaster.broadcast(event_type, data)
    try:
        import push
        push.on_event(event_type, data)
    except Exception:
        pass
    return result

# --- Cross-process delivery -------------------------------------------------
# The broadcaster above is in-process, so an event produced by the MCP server (a
# different process from the HUD) could never reach /api/events subscribers. Events are
# therefore spooled to a file that the HUD tails. This is the same mechanism the rest of
# Subway uses (wake spools, compact requests): files, watched.

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
    try:
        import push  # spooled events (MCP server, workers) push too, not only the HUD's own
        push.on_event(event_type, data)
    except Exception:
        pass


_tailer_lock = threading.Lock()
_tailer: Dict[str, Any] = {"thread": None, "stop": None}


def stop_spool_tailer(timeout: float = 2.0) -> None:
    """Stop the spool tailer if one is running (tests, HUD shutdown)."""
    with _tailer_lock:
        t, stop = _tailer["thread"], _tailer["stop"]
        _tailer["thread"] = _tailer["stop"] = None
    if stop is not None:
        stop.set()
    if t is not None and t is not threading.current_thread():
        t.join(timeout)


def _wait_spool_or_stop(path: str, stop: threading.Event, interval: float) -> bool:
    """Sleep up to `interval`, waking early when the spool file changes. Returns True if stop is set."""
    if hasattr(select, "kqueue") and hasattr(select, "KQ_FILTER_VNODE") and _os.path.exists(path):
        try:
            fd = _os.open(path, _os.O_RDONLY)
            kq = select.kqueue()
            try:
                kq.control(
                    [select.kevent(
                        fd,
                        filter=select.KQ_FILTER_VNODE,
                        flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                        fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_ATTRIB
                        | select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE,
                    )],
                    1,
                    interval,
                )
            finally:
                kq.close()
                _os.close(fd)
        except OSError:
            return stop.wait(interval)
        return stop.is_set()
    return stop.wait(interval)

def start_spool_tailer(interval: float = 0.5, max_bytes: int = 4 * 1024 * 1024) -> threading.Thread:
    """Watch the spool in the HUD process and broadcast new lines to subscribers. Idempotent: returns the running tailer if there is one."""
    # Start at the end: the backlog was delivered (or missed) before this process started, and
    # replaying it would resend every old event to browsers and phones at each HUD restart.
    try:
        start = _os.path.getsize(_spool_path())
    except OSError:
        start = 0
    state = {"offset": start}

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
        while not stop.is_set():
            if _wait_spool_or_stop(_spool_path(), stop, interval):
                break
            try:
                _tick()
            except Exception:
                pass

    with _tailer_lock:
        running = _tailer["thread"]
        if running is not None and running.is_alive():
            return running  # one tailer per process: a second would broadcast every event twice
        stop = threading.Event()
        t = threading.Thread(target=_loop, name="subway-sse-spool", daemon=True)
        _tailer["thread"], _tailer["stop"] = t, stop
        t.start()
    return t


def tail_remote_thread(
    thread_spec: str,
    emit: Callable[[str], None],
    stop_event: Optional[threading.Event] = None,
    poll_interval: float = 2.0,
    min_interval: float = 2.0,
    max_seen: int = 200,
    peer_call: Optional[Callable[..., Dict[str, Any]]] = None,
    split_fn: Optional[Callable[[str], Any]] = None,
    max_iterations: Optional[int] = None,
) -> None:
    """Poll a remote station's /api/peer/read_chat and emit 'thread_tail' SSE frames."""
    if split_fn is None:
        import peers as _peers
        split_fn = _peers.split_remote
    if peer_call is None:
        import peers as _peers
        peer_call = _peers.call

    resolved = split_fn(thread_spec)
    if not resolved:
        return
    head, peer = resolved
    peer_id = peer["peer_id"]

    interval = max(float(poll_interval), float(min_interval))
    seen_ids: deque = deque(maxlen=max_seen)
    iterations = 0

    while True:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            qs = urllib.parse.urlencode({"chat_id": head, "mode": "recent", "since": "last"})
            res = peer_call(peer_id, "GET", f"/api/peer/read_chat?{qs}")
            if isinstance(res, dict) and res.get("status") == 200:
                data = res.get("data") or {}
                messages = data.get("messages")
                if isinstance(messages, list):
                    for m in messages:
                        if isinstance(m, dict):
                            mid = str(m.get("id") or hashlib.sha256(json.dumps(m, sort_keys=True).encode("utf-8")).hexdigest()[:16])
                            payload = {"thread": thread_spec, "message": m}
                        else:
                            mid = hashlib.sha256(str(m).encode("utf-8")).hexdigest()[:16]
                            payload = {"thread": thread_spec, "text": str(m)}
                        if mid not in seen_ids:
                            seen_ids.append(mid)
                            seq = next_seq()
                            emit(f"id: {seq}\nevent: thread_tail\ndata: {json.dumps(payload)}\n\n")
                else:
                    text = str(data.get("text") or "")
                    if text and "No new stations since your last transit" not in text:
                        mid = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
                        if mid not in seen_ids:
                            seen_ids.append(mid)
                            seq = next_seq()
                            payload = {"thread": thread_spec, "text": text}
                            emit(f"id: {seq}\nevent: thread_tail\ndata: {json.dumps(payload)}\n\n")
        except Exception:
            pass

        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break
        if stop_event is not None:
            if stop_event.wait(interval):
                break
        else:
            time.sleep(interval)

