"""Persistent ACP JSON-RPC session & event pump.

Keeps long-lived stdio ACP subprocesses pooled by key (provider, profile_id, cwd, session_id)
so repeated turns reuse the same subprocess without cold per-turn spawns.
Pumps streaming JSON-RPC 2.0 notifications/chunks to subscribers and callers.
"""

from __future__ import annotations

import collections
import contextlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("acp_pump")

SessionKey = Tuple[str, str, str, str]


def _make_key(provider: Any, profile_id: Any, cwd: Any, session_id: Any) -> SessionKey:
    """Canonicalize (provider, profile_id, cwd, session_id) tuple."""
    return (str(provider or ""), str(profile_id or ""), str(cwd or ""), str(session_id or ""))


def _normalize_key(key: Any) -> SessionKey:
    """Normalize a key argument into a canonical 4-tuple."""
    if isinstance(key, (tuple, list)) and len(key) == 4:
        return _make_key(key[0], key[1], key[2], key[3])
    raise ValueError(f"Invalid session key: {key!r}, expected 4-tuple (provider, profile_id, cwd, session_id)")


class PooledSession:
    """A long-lived stdio subprocess session with background stdout/stderr reader threads."""

    def __init__(
        self,
        pump: "ACPPump",
        key: SessionKey,
        provider: str,
        profile_id: str,
        cwd: str,
        session_id: str,
        cmd: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> None:
        self.pump = pump
        self.key = key
        self.provider = provider
        self.profile_id = profile_id
        self.cwd = cwd
        self.session_id = session_id
        self.cmd = cmd or self._default_cmd(provider)
        self.env = env

        self.last_active: float = time.time()
        self.is_busy: bool = False
        self.is_alive: bool = False

        self._lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.turn_lock = threading.Lock()

        self.req_counter: int = 0
        self.active_request_id: Optional[Any] = None
        self.active_on_chunk: Optional[Callable[[dict], None]] = None
        self.pending_requests: Dict[Any, Dict[str, Any]] = {}
        self.stderr_lines: Deque[str] = collections.deque(maxlen=100)

        # Subprocess and reader threads
        self.proc: subprocess.Popen
        self.stdout_thread: Optional[threading.Thread] = None
        self.stderr_thread: Optional[threading.Thread] = None

    @staticmethod
    def _default_cmd(provider: str) -> List[str]:
        if provider == "opencode":
            return ["opencode", "acp"]
        if provider == "claude":
            return ["claude-code-acp"]
        return [sys.executable, "-u", "-m", "acp"]

    def start(self) -> None:
        """Spawn the subprocess and background reader threads."""
        proc_env = os.environ.copy()
        if self.profile_id:
            try:
                import provider_profiles as _pp
                _bin, _penv = _pp.resolve_profile_env(self.profile_id)
                if _penv:
                    proc_env.update(_penv)
                if _bin and self.cmd:
                    self.cmd = [_bin] + list(self.cmd[1:])
            except Exception:
                pass
        if self.env:
            proc_env.update(self.env)

        run_cwd = self.cwd if (self.cwd and os.path.exists(self.cwd)) else None

        self.proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=run_cwd,
            env=proc_env,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.is_alive = True
        self.last_active = time.time()

        self.stdout_thread = threading.Thread(
            target=self._stdout_loop,
            name=f"acp-stdout-{self.session_id}",
            daemon=True,
        )
        self.stderr_thread = threading.Thread(
            target=self._stderr_loop,
            name=f"acp-stderr-{self.session_id}",
            daemon=True,
        )
        self.stdout_thread.start()
        self.stderr_thread.start()

    def _stdout_loop(self) -> None:
        try:
            assert self.proc.stdout is not None
            for line in iter(self.proc.stdout.readline, ""):
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue

                req_id = msg.get("id")
                if req_id is not None:
                    # JSON-RPC response
                    with self._lock:
                        pending = self.pending_requests.get(req_id)
                        if pending is None:
                            # Try int/str fallback
                            if isinstance(req_id, str) and req_id.isdigit():
                                pending = self.pending_requests.get(int(req_id))
                            elif isinstance(req_id, int):
                                pending = self.pending_requests.get(str(req_id))
                        if pending is not None:
                            pending["response"] = msg
                            pending["event"].set()
                else:
                    # JSON-RPC notification / streaming chunk
                    self.last_active = time.time()
                    cb = self.active_on_chunk
                    if cb is not None:
                        try:
                            cb(msg)
                        except Exception:
                            pass
                    self.pump._dispatch_notification(self.key, msg)
        except Exception:
            pass
        finally:
            self.is_alive = False
            # Unblock any pending requests if subprocess exited
            with self._lock:
                for pending in self.pending_requests.values():
                    if not pending["event"].is_set():
                        pending["response"] = {
                            "error": {
                                "code": -32000,
                                "message": "Subprocess terminated unexpectedly",
                            }
                        }
                        pending["event"].set()

    def _stderr_loop(self) -> None:
        try:
            assert self.proc.stderr is not None
            for line in iter(self.proc.stderr.readline, ""):
                line = line.strip()
                if line:
                    with self._lock:
                        self.stderr_lines.append(line)
        except Exception:
            pass

    def _write_msg(self, msg: dict) -> None:
        line = json.dumps(msg) + "\n"
        with self.write_lock:
            if not self.proc.stdin or self.proc.poll() is not None:
                raise RuntimeError("Subprocess is not running")
            self.proc.stdin.write(line)
            self.proc.stdin.flush()

    def send_prompt(
        self,
        session_id: str,
        prompt: Any,
        timeout: float = 30.0,
        on_chunk: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        """Execute a prompt turn on this session and return the response result."""
        with self.turn_lock:
            self.is_busy = True
            self.last_active = time.time()

            with self._lock:
                self.req_counter += 1
                req_id = self.req_counter
                resp_event = threading.Event()
                self.pending_requests[req_id] = {"event": resp_event, "response": None}
                self.active_request_id = req_id
                self.active_on_chunk = on_chunk

            req_msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "session/prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": prompt,
                },
            }

            try:
                self._write_msg(req_msg)
                if not resp_event.wait(timeout=timeout):
                    raise TimeoutError(f"ACP request {req_id} timed out after {timeout}s")

                with self._lock:
                    resp_item = self.pending_requests.pop(req_id, {})
                    resp_data = resp_item.get("response")

                if resp_data is None:
                    raise RuntimeError("Subprocess exited without response")

                if "result" in resp_data:
                    res = resp_data["result"]
                    if isinstance(res, dict):
                        return res
                    return {"result": res}
                return resp_data
            finally:
                with self._lock:
                    self.active_request_id = None
                    self.active_on_chunk = None
                    self.pending_requests.pop(req_id, None)
                    self.is_busy = False
                    self.last_active = time.time()

    def cancel(self, session_id: str, request_id: Optional[Any] = None) -> bool:
        """Send JSON-RPC session/cancel notification to the subprocess."""
        target_req_id = request_id
        if target_req_id is None:
            with self._lock:
                target_req_id = self.active_request_id

        params: Dict[str, Any] = {"sessionId": session_id}
        if target_req_id is not None:
            params["requestId"] = target_req_id

        cancel_msg = {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": params,
        }
        try:
            self._write_msg(cancel_msg)
            return True
        except Exception:
            return False

    def close(self) -> None:
        """Terminate the subprocess and close I/O streams."""
        self.is_alive = False
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=1.0)
        except Exception:
            pass

        with contextlib.suppress(Exception):
            if self.proc.stdin:
                self.proc.stdin.close()
        with contextlib.suppress(Exception):
            if self.proc.stdout:
                self.proc.stdout.close()
        with contextlib.suppress(Exception):
            if self.proc.stderr:
                self.proc.stderr.close()

        cur_thread = threading.current_thread()
        if self.stdout_thread and self.stdout_thread.is_alive() and cur_thread != self.stdout_thread:
            self.stdout_thread.join(timeout=1.0)
        if self.stderr_thread and self.stderr_thread.is_alive() and cur_thread != self.stderr_thread:
            self.stderr_thread.join(timeout=1.0)


class ACPPump:
    """Persistent ACP JSON-RPC session & event pump."""

    def __init__(self, idle_ttl: float = 300.0, reap_interval: Optional[float] = None) -> None:
        self._idle_ttl: float = float(idle_ttl)
        if reap_interval is not None:
            self._reap_interval: float = float(reap_interval)
        else:
            self._reap_interval = max(0.01, min(1.0, self._idle_ttl / 4.0)) if self._idle_ttl > 0 else 1.0

        self._lock = threading.Lock()
        self._sessions: Dict[SessionKey, PooledSession] = {}
        self._subscribers: Dict[SessionKey, List[Callable[[dict], None]]] = {}

        self._stop_event = threading.Event()
        self._reaper_thread: Optional[threading.Thread] = None

        if self._idle_ttl > 0:
            self._start_reaper()

    def _start_reaper(self) -> None:
        if self._reaper_thread is None or not self._reaper_thread.is_alive():
            self._stop_event.clear()
            self._reaper_thread = threading.Thread(
                target=self._reaper_loop,
                name="acp-idle-reaper",
                daemon=True,
            )
            self._reaper_thread.start()

    def _stop_reaper(self) -> None:
        self._stop_event.set()
        if self._reaper_thread and self._reaper_thread.is_alive():
            if threading.current_thread() != self._reaper_thread:
                self._reaper_thread.join(timeout=2.0)
        self._reaper_thread = None

    def _reaper_loop(self) -> None:
        while not self._stop_event.wait(self._reap_interval):
            now = time.time()
            to_reap: List[PooledSession] = []
            with self._lock:
                for key, session in list(self._sessions.items()):
                    if not session.is_busy and (now - session.last_active) >= self._idle_ttl:
                        to_reap.append(self._sessions.pop(key))
            for session in to_reap:
                session.close()

    def _dispatch_notification(self, key: SessionKey, msg: dict) -> None:
        with self._lock:
            subs = list(self._subscribers.get(key, []))
        for cb in subs:
            try:
                cb(msg)
            except Exception:
                pass

    def subscribe(self, key_or_provider: Any, *args: Any) -> Callable[[], None]:
        """Register a subscriber callback for streaming notifications/chunks.

        Usage:
            subscribe(key, callback)
            subscribe(provider, profile_id, cwd, session_id, callback)
        Returns:
            An unsubscribe callable.
        """
        if len(args) == 1 and callable(args[0]):
            norm_key = _normalize_key(key_or_provider)
            callback: Callable[[dict], None] = args[0]
        elif len(args) == 4 and callable(args[3]):
            norm_key = _make_key(key_or_provider, args[0], args[1], args[2])
            callback = args[3]
        else:
            raise ValueError(
                "Usage: subscribe(key, callback) or subscribe(provider, profile_id, cwd, session_id, callback)"
            )

        with self._lock:
            if norm_key not in self._subscribers:
                self._subscribers[norm_key] = []
            self._subscribers[norm_key].append(callback)

        def unsubscribe() -> None:
            with self._lock:
                subs = self._subscribers.get(norm_key, [])
                if callback in subs:
                    subs.remove(callback)
                if not subs and norm_key in self._subscribers:
                    del self._subscribers[norm_key]

        return unsubscribe

    def send_prompt(
        self,
        provider: str,
        profile_id: str,
        cwd: str,
        session_id: str,
        prompt: Any,
        *,
        cmd: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
        on_chunk: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        """Send a prompt turn to the pooled subprocess, spawning or reusing it as needed."""
        key = _make_key(provider, profile_id, cwd, session_id)

        with self._lock:
            session = self._sessions.get(key)
            if session is not None and (not session.is_alive or session.proc.poll() is not None):
                session.close()
                self._sessions.pop(key, None)
                session = None

            if session is None:
                if self._idle_ttl > 0:
                    self._start_reaper()
                session = PooledSession(
                    pump=self,
                    key=key,
                    provider=provider,
                    profile_id=profile_id,
                    cwd=cwd,
                    session_id=session_id,
                    cmd=cmd,
                    env=env,
                )
                session.start()
                self._sessions[key] = session

        return session.send_prompt(
            session_id=session_id,
            prompt=prompt,
            timeout=timeout,
            on_chunk=on_chunk,
        )

    def cancel(
        self,
        provider: str,
        profile_id: str,
        cwd: str,
        session_id: str,
        request_id: Optional[Any] = None,
    ) -> bool:
        """Send JSON-RPC session/cancel to the pooled subprocess."""
        key = _make_key(provider, profile_id, cwd, session_id)
        with self._lock:
            session = self._sessions.get(key)
        if not session or not session.is_alive or session.proc.poll() is not None:
            return False
        return session.cancel(session_id=session_id, request_id=request_id)

    def close(
        self,
        provider: Optional[str] = None,
        profile_id: Optional[str] = None,
        cwd: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Close pooled session(s).

        If all arguments are None, closes all sessions and stops the reaper cleanly.
        """
        if provider is None and profile_id is None and cwd is None and session_id is None:
            self._stop_reaper()
            with self._lock:
                sessions = list(self._sessions.values())
                self._sessions.clear()
                self._subscribers.clear()
            for session in sessions:
                session.close()
        elif provider is not None and profile_id is not None and cwd is not None and session_id is not None:
            key = _make_key(provider, profile_id, cwd, session_id)
            with self._lock:
                session = self._sessions.pop(key, None)
            if session:
                session.close()
        else:
            to_close: List[SessionKey] = []
            with self._lock:
                for k, s in list(self._sessions.items()):
                    match = True
                    if provider is not None and s.provider != provider:
                        match = False
                    if profile_id is not None and s.profile_id != profile_id:
                        match = False
                    if cwd is not None and s.cwd != cwd:
                        match = False
                    if session_id is not None and s.session_id != session_id:
                        match = False
                    if match:
                        to_close.append(k)
                sessions_to_close = [self._sessions.pop(k) for k in to_close]
            for session in sessions_to_close:
                session.close()

    def has_session(self, provider: str, profile_id: str, cwd: str, session_id: str) -> bool:
        """Check if an active session exists in the pool."""
        key = _make_key(provider, profile_id, cwd, session_id)
        with self._lock:
            session = self._sessions.get(key)
            return bool(session and session.is_alive and session.proc.poll() is None)

    def get_session(self, key: Any) -> Optional[PooledSession]:
        """Retrieve a session from the pool by key tuple."""
        norm_key = _normalize_key(key)
        with self._lock:
            return self._sessions.get(norm_key)

    def get_session_count(self) -> int:
        """Return the number of pooled sessions."""
        with self._lock:
            return len(self._sessions)


_global_pump: Optional[ACPPump] = None
_global_pump_lock = threading.Lock()


def get_pump(idle_ttl: float = 300.0, reap_interval: Optional[float] = None) -> ACPPump:
    """Thread-safe singleton accessor for ACPPump."""
    global _global_pump
    with _global_pump_lock:
        if _global_pump is None:
            _global_pump = ACPPump(idle_ttl=idle_ttl, reap_interval=reap_interval)
        return _global_pump


def reset_pump() -> None:
    """Reset and close the global singleton pump (for tests)."""
    global _global_pump
    with _global_pump_lock:
        if _global_pump is not None:
            _global_pump.close()
            _global_pump = None


__all__ = [
    "ACPPump",
    "PooledSession",
    "SessionKey",
    "get_pump",
    "reset_pump",
]
