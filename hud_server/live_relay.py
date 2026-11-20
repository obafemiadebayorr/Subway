"""Loopback UDP relay: stream live text deltas from provider processes to HUD in memory."""
from __future__ import annotations

import json
import os
import secrets
import socket
import threading
import time
from typing import Dict, Optional

try:
    import subway_globals as _s
except Exception:  # pragma: no cover
    try:
        import server as _s  # type: ignore
    except Exception:
        _s = None  # type: ignore

_SUBWAY_DIR_FALLBACK = os.path.join(os.path.expanduser("~"), ".subway")

_lock = threading.Lock()
_started = False
_sock: Optional[socket.socket] = None
_secret: str = ""
_runs: Dict[str, dict] = {}
_cond = threading.Condition(_lock)

_CHUNK = 8 * 1024
_CAP = 256 * 1024
_OOO_MAX = 64
_IDLE_EVICT = 30 * 60


def _subway_dir() -> str:
    d = getattr(_s, "SUBWAY_DIR", None) if _s else None
    return d or _SUBWAY_DIR_FALLBACK


def _port_path() -> str:
    return os.path.join(_subway_dir(), "live_relay.port")


def _key_path() -> str:
    return os.path.join(_subway_dir(), "live_relay.key")


def _write_atomic(path: str, data: str, mode: int = 0o600) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        raise


def _evict_locked(now: float) -> None:
    dead = [k for k, v in _runs.items() if now - v.get("updated_at", now) > _IDLE_EVICT]
    for k in dead:
        _runs.pop(k, None)


def _get_run_locked(key: str, now: float) -> dict:
    r = _runs.get(key)
    if r is None:
        r = {
            "text": "",
            "running": True,
            "version": 0,
            "updated_at": now,
            "next_seq": 0,
            "ooo": {},
        }
        _runs[key] = r
    return r


def _apply_locked(r: dict, seq: int, delta: str, done: bool, now: float) -> bool:
    if seq < r["next_seq"]:
        return False
    if seq > r["next_seq"]:
        ooo = r["ooo"]
        if seq not in ooo and len(ooo) < _OOO_MAX:
            ooo[seq] = (delta, done)
        elif seq in ooo:
            ooo[seq] = (delta, done)
        return False
    changed = False
    while True:
        if delta:
            r["text"] = (r["text"] + delta)[-_CAP:]
            changed = True
        if done and r.get("running", True):
            r["running"] = False
            changed = True
        r["next_seq"] = seq + 1
        nxt = r["ooo"].pop(r["next_seq"], None)
        if nxt is None:
            break
        seq, (delta, done) = r["next_seq"], nxt
    r["updated_at"] = now
    if changed:
        r["version"] = r.get("version", 0) + 1
    return changed


def _recv_loop() -> None:
    global _sock
    while True:
        try:
            data, _addr = _sock.recvfrom(65535)
        except Exception:
            with _lock:
                if not _started:
                    return
            time.sleep(0.05)
            continue
        try:
            msg = json.loads(data.decode("utf-8", "replace"))
        except Exception:
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("m") != _secret:
            continue
        key = msg.get("k")
        if not isinstance(key, str) or not key:
            continue
        try:
            seq = int(msg.get("s", 0))
        except Exception:
            continue
        delta = msg.get("d") or ""
        if not isinstance(delta, str):
            delta = ""
        done = bool(msg.get("e"))
        now = time.time()
        with _cond:
            _evict_locked(now)
            r = _get_run_locked(key, now)
            if _apply_locked(r, seq, delta, done, now):
                r["updated_at"] = now
                _cond.notify_all()
            else:
                r["updated_at"] = now


def start_receiver() -> None:
    """Bind loopback UDP receiver and publish port/secret files. Idempotent."""
    global _started, _sock, _secret
    with _lock:
        if _started:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        secret = secrets.token_hex(16)
        _write_atomic(_port_path(), str(port), 0o600)
        _write_atomic(_key_path(), secret, 0o600)
        _sock = s
        _secret = secret
        _started = True
    t = threading.Thread(target=_recv_loop, name="live-relay-recv", daemon=True)
    t.start()


def has(key: str) -> bool:
    with _lock:
        return key in _runs


def text(key: str) -> str:
    with _lock:
        r = _runs.get(key)
        return r.get("text", "") if r else ""


def running(key: str) -> Optional[bool]:
    with _lock:
        r = _runs.get(key)
        if r is None:
            return None
        return bool(r.get("running", False))


def version(key: str) -> int:
    with _lock:
        r = _runs.get(key)
        return int(r.get("version", 0)) if r else 0


def wait(key: str, since_version: int, timeout: float) -> int:
    deadline = time.monotonic() + max(0.0, float(timeout))
    with _cond:
        while True:
            r = _runs.get(key)
            v = int(r.get("version", 0)) if r else 0
            if v != since_version:
                return v
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return v
            _cond.wait(remaining)


class Sender:
    """Fire-and-forget UDP sender for stream_tee (runs in provider child wrapper process)."""

    def __init__(self, key: str):
        self.key = key
        self._seq = 0
        self._port: Optional[int] = None
        self._secret: Optional[str] = None
        self._last_read = 0.0
        self._sock: Optional[socket.socket] = None
        self._dir = _subway_dir()

    def _ensure(self, now: float) -> bool:
        if self._port is not None and self._secret is not None:
            return True
        if now - self._last_read < 5.0:
            return False
        self._last_read = now
        try:
            with open(os.path.join(self._dir, "live_relay.port"), "r", encoding="utf-8") as f:
                self._port = int(f.read().strip())
            with open(os.path.join(self._dir, "live_relay.key"), "r", encoding="utf-8") as f:
                self._secret = f.read().strip()
            if self._sock is None:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            return bool(self._secret)
        except Exception:
            self._port = None
            self._secret = None
            return False

    def send(self, delta: str, done: bool = False) -> None:
        try:
            now = time.monotonic()
            if not self._ensure(now):
                # retry read on failure case? ensure already rate-limited; if failed we no-op
                if self._port is None or self._secret is None:
                    # try once more if files might have appeared and we were rate-limited earlier
                    self._last_read = 0.0
                    if not self._ensure(time.monotonic()):
                        return
            chunks = []
            if delta:
                data = delta.encode("utf-8", "replace")
                for i in range(0, len(data), _CHUNK):
                    chunks.append(data[i:i + _CHUNK])
            else:
                chunks.append(b"")
            for idx, chunk in enumerate(chunks):
                is_last = idx == len(chunks) - 1
                msg = {
                    "k": self.key,
                    "s": self._seq,
                    "d": chunk.decode("utf-8", "replace"),
                    "e": bool(done and is_last),
                    "m": self._secret,
                }
                self._seq += 1
                payload = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                try:
                    self._sock.sendto(payload, ("127.0.0.1", int(self._port)))
                except Exception:
                    pass
        except Exception:
            return
