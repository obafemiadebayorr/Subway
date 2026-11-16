"""Wire efficiency and back-pressure for the HUD server (roadmap 53, layer 1).

- JSON answers are gzipped when the client accepts it and the body is worth compressing.
- GET answers carry an ETag; a matching If-None-Match gets 304 with no body.
- Request threads are capped, so a flood of connections answers 503 instead of exhausting the Mac.
"""
from __future__ import annotations

import gzip
import hashlib
import os
import threading
from typing import Optional, Tuple

GZIP_MIN_BYTES = 1024
DEFAULT_MAX_THREADS = 128

BUSY_BODY = b'{"error": "Station busy", "retry_after": 2}'
BUSY_RESPONSE = (
    b"HTTP/1.1 503 Service Unavailable\r\n"
    b"Content-Type: application/json\r\n"
    b"Retry-After: 2\r\n"
    b"Connection: close\r\n"
    b"Content-Length: " + str(len(BUSY_BODY)).encode() + b"\r\n\r\n" + BUSY_BODY
)


def accepts_gzip(accept_encoding: Optional[str]) -> bool:
    """True when the header lists gzip without q=0."""
    for part in (accept_encoding or "").lower().split(","):
        bits = [b.strip() for b in part.split(";")]
        if bits[0] not in ("gzip", "*"):
            continue
        q = 1.0
        for b in bits[1:]:
            if b.startswith("q="):
                try:
                    q = float(b[2:])
                except ValueError:
                    q = 0.0
        if q > 0:
            return True
    return False


def encode_body(payload: bytes, accept_encoding: Optional[str],
                min_bytes: int = GZIP_MIN_BYTES) -> Tuple[bytes, Optional[str]]:
    """(body, "gzip") when compressing pays off, else (payload, None)."""
    if len(payload) < min_bytes or not accepts_gzip(accept_encoding):
        return payload, None
    packed = gzip.compress(payload, compresslevel=5, mtime=0)
    if len(packed) >= len(payload):
        return payload, None
    return packed, "gzip"


def etag_for(payload: bytes) -> str:
    return '"' + hashlib.sha1(payload).hexdigest()[:20] + '"'


def etag_matches(if_none_match: Optional[str], etag: str) -> bool:
    """RFC 9110 weak comparison: W/ prefixes are ignored, `*` matches anything."""
    raw = (if_none_match or "").strip()
    if not raw:
        return False
    if raw == "*":
        return True
    want = etag[2:] if etag.startswith("W/") else etag
    for tag in raw.split(","):
        tag = tag.strip()
        if tag.startswith("W/"):
            tag = tag[2:]
        if tag == want:
            return True
    return False


def max_threads_from_env() -> int:
    try:
        n = int(os.environ.get("SUBWAY_HUD_MAX_THREADS", "") or DEFAULT_MAX_THREADS)
    except ValueError:
        n = DEFAULT_MAX_THREADS
    return max(0, n)


class ThreadCap:
    """A non-blocking counter: try_acquire fails when `limit` requests are already running."""

    def __init__(self, limit: int):
        self.limit = int(limit)
        self._n = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self._n >= self.limit:
                return False
            self._n += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._n > 0:
                self._n -= 1

    @property
    def in_use(self) -> int:
        with self._lock:
            return self._n
