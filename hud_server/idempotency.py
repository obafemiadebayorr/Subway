"""Request keys for POSTs a phone may retry (roadmap 53, layer 1).

A phone that times out cannot tell whether its approve or stop landed, so it retries with the same
`X-Idempotency-Key`. The first request runs; a retry while it is still running gets 409, and a retry
after it finished gets the stored answer back without running the action again.
In memory only: a restart forgets the keys, which is safe because the actions check their own state.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Optional, Tuple

TTL_S = 600.0
MAX_ENTRIES = 2000
MAX_KEY_LEN = 128
PATHS = frozenset({
    "/api/approve_worker", "/api/reject_worker", "/api/stop_worker", "/api/answer_permission",
    "/api/pair/approve", "/api/pair/deny", "/api/pair/revoke", "/api/pair/rename",
    "/api/broadcast", "/api/release",
    "/api/spawn",
})

_lock = threading.Lock()
_entries: "OrderedDict[Tuple[str, str], Tuple[float, Optional[Tuple[int, bytes]]]]" = OrderedDict()


def _prune(now: float) -> None:
    for k in [k for k, (exp, _) in _entries.items() if exp <= now]:
        del _entries[k]
    while len(_entries) > MAX_ENTRIES:
        _entries.popitem(last=False)


def begin(owner: str, key: str):
    """("new", None), ("inflight", None) or ("done", (status, body_bytes))."""
    now = time.time()
    k = (str(owner), str(key))
    with _lock:
        _prune(now)
        hit = _entries.get(k)
        if hit is not None:
            return ("inflight", None) if hit[1] is None else ("done", hit[1])
        _entries[k] = (now + TTL_S, None)
        _prune(now)
        return ("new", None)


def finish(owner: str, key: str, status: int, body: bytes) -> None:
    with _lock:
        _entries[(str(owner), str(key))] = (time.time() + TTL_S, (int(status), bytes(body)))
        _prune(time.time())


def abandon(owner: str, key: str) -> None:
    """The handler raised before answering: let the client try again."""
    with _lock:
        hit = _entries.get((str(owner), str(key)))
        if hit is not None and hit[1] is None:
            del _entries[(str(owner), str(key))]


def _reset() -> None:
    with _lock:
        _entries.clear()
