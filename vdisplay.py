"""Virtual display abstraction for headless stations.

Provides a unified create/ensure/destroy/status/env interface across OS backends
(Linux Xvfb/wlroots, with macOS and Windows backends plugged in separately).
"""
from __future__ import annotations

import atexit
import contextlib
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Optional

DEFAULT_WIDTH = 1440
DEFAULT_HEIGHT = 900
DEFAULT_DEPTH = 24
MIN_WIDTH = 320
MAX_WIDTH = 7680
MIN_HEIGHT = 240
MAX_HEIGHT = 4320

HDMI_DUMMY_ADVICE = (
    "Plug in an HDMI dummy plug (~$8, zero software risk, works on every OS) "
    "as a hardware fallback."
)

MACOS_HELPER_NAME = "subway-vdisplay"
MACOS_SWIFT_SOURCE = os.path.join("native", "macos", "vdisplay.swift")


def _proc_stderr_snippet(proc: subprocess.Popen, limit: int = 400) -> str:
    """Read a short stderr snippet from an already-exited helper (best effort)."""
    if proc.poll() is None or proc.stderr is None:
        return ""
    try:
        data = proc.stderr.read(limit)
    except Exception:
        return ""
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    return (data or "").strip()


def validate_size(width: int, height: int) -> tuple[int, int]:
    """Validate and coerce virtual display dimensions."""
    if isinstance(width, bool) or isinstance(height, bool):
        raise ValueError("display dimensions must be integers")
    try:
        w = int(width)
        h = int(height)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid display dimensions: {width!r}x{height!r}") from exc
    if not (MIN_WIDTH <= w <= MAX_WIDTH) or not (MIN_HEIGHT <= h <= MAX_HEIGHT):
        raise ValueError(
            f"display dimensions {w}x{h} out of range "
            f"({MIN_WIDTH}..{MAX_WIDTH} x {MIN_HEIGHT}..{MAX_HEIGHT})"
        )
    return w, h


class VDisplayBackend:
    """Base interface for OS-specific virtual display backends."""

    name: str = "base"

    def is_available(self) -> bool:
        """Return True if the OS virtual display mechanism is available."""
        return False

    def has_physical_display(self) -> bool:
        """Return True if a physical or pre-existing display session is active."""
        return False

    def create(
        self,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        **kwargs,
    ) -> dict:
        """Create and start a virtual display at width x height."""
        raise NotImplementedError

    def destroy(self) -> dict:
        """Stop and clean up the active virtual display."""
        raise NotImplementedError

    def status(self) -> dict:
        """Return a structured status dictionary for the virtual display."""
        raise NotImplementedError

    def env(self) -> dict[str, str]:
        """Return environment variables needed to target this virtual display."""
        return {}


class UnsupportedBackend(VDisplayBackend):
    """Fallback backend when the current OS has no registered virtual display driver."""

    def __init__(self, platform_name: str) -> None:
        self.platform_name = platform_name
        self.name = f"unsupported:{platform_name}"

    def _base_status(self, *, ok: bool, state: str, error: str) -> dict:
        return {
            "ok": ok,
            "active": False,
            "available": False,
            "state": state,
            "hud_status": (
                "virtual display unavailable" if state == "unavailable" else "virtual display off"
            ),
            "backend": self.name,
            "width": 0,
            "height": 0,
            "display": None,
            "pid": None,
            "env": {},
            "physical_display": False,
            "error": error,
            "advice": HDMI_DUMMY_ADVICE if error else "",
        }

    def create(
        self,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        **kwargs,
    ) -> dict:
        return self._base_status(
            ok=False,
            state="unavailable",
            error=f"Virtual display is not supported on platform '{self.platform_name}'.",
        )

    def destroy(self) -> dict:
        return self._base_status(ok=True, state="off", error="")

    def status(self) -> dict:
        return self._base_status(
            ok=False,
            state="unavailable",
            error=f"Virtual display is not supported on platform '{self.platform_name}'.",
        )


class MacOSVDisplayBackend(VDisplayBackend):
    """Supervise the `subway-vdisplay` Swift helper that owns a CGVirtualDisplay.

    The helper runs out-of-process so a private-API crash cannot kill the station.
    We start it at the requested size, wait for its JSON ``ready`` line, and restart
    it with exponential backoff if it dies unexpectedly. Exiting the helper removes
    the display (the OS tears it down when the process goes away).
    """

    name = "macos"

    def __init__(
        self,
        *,
        helper_bin: Optional[str] = None,
        helper_source: Optional[str] = None,
        subway_dir: Optional[str] = None,
        which: Optional[Callable[[str], Optional[str]]] = None,
        popen: Optional[Callable[..., subprocess.Popen]] = None,
        swiftc: Optional[str] = None,
        sleeper: Optional[Callable[[float], None]] = None,
        ready_timeout: float = 2.0,
        poll_interval: float = 0.02,
        backoff_initial: float = 0.25,
        backoff_max: float = 8.0,
        stable_after: float = 5.0,
        max_restarts: int = 0,
    ) -> None:
        self._lock = threading.RLock()
        self._helper_bin_override = helper_bin
        self._source_override = helper_source
        self._subway_dir_override = subway_dir
        self._which = which if which is not None else shutil.which
        self._popen = popen if popen is not None else subprocess.Popen
        self._swiftc_override = swiftc
        self._sleeper = sleeper if sleeper is not None else time.sleep
        self._ready_timeout = max(0.05, float(ready_timeout))
        self._poll_interval = max(0.005, float(poll_interval))
        self._backoff_initial = max(0.001, float(backoff_initial))
        self._backoff_max = max(self._backoff_initial, float(backoff_max))
        self._stable_after = max(0.0, float(stable_after))
        self._max_restarts = max(0, int(max_restarts))

        self._helper: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._display_id: Optional[str] = None
        self._want_w = 0
        self._want_h = 0
        self._width = 0
        self._height = 0
        self._last_error = ""
        self._desired = False
        self._stop = threading.Event()
        self._monitor: Optional[threading.Thread] = None
        self._restarts = 0
        self._backoff = self._backoff_initial
        self._backoff_delays: list[float] = []
        self._started_at = 0.0

    def _subway_dir(self) -> str:
        if self._subway_dir_override is not None:
            return self._subway_dir_override
        return os.environ.get("SUBWAY_DIR", "").strip()

    def _default_helper_path(self) -> str:
        d = self._subway_dir()
        return os.path.join(d, "bin", MACOS_HELPER_NAME) if d else ""

    def _source_path(self) -> str:
        if self._source_override is not None:
            return self._source_override
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), MACOS_SWIFT_SOURCE)

    def _find_helper(self) -> Optional[str]:
        if self._helper_bin_override:
            cand = self._helper_bin_override
            return cand if os.path.isfile(cand) and os.access(cand, os.X_OK) else None
        try:
            found = self._which(MACOS_HELPER_NAME)
        except Exception:
            found = None
        if found and os.path.isfile(found) and os.access(found, os.X_OK):
            return found
        cand = self._default_helper_path()
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
        return None

    def _find_swiftc(self) -> Optional[str]:
        if self._swiftc_override:
            cand = self._swiftc_override
            return cand if os.path.isfile(cand) and os.access(cand, os.X_OK) else None
        try:
            return self._which("swiftc")
        except Exception:
            return None

    def is_available(self) -> bool:
        if self._find_helper() is not None:
            return True
        if self._find_swiftc() is None:
            return False
        src = self._source_path()
        return bool(src and os.path.isfile(src))

    def has_physical_display(self) -> bool:
        # Physical-display detection needs CoreGraphics queries owned by the native
        # helper; for unit 4 a headless station is treated as having no display.
        return False

    def _resolve_helper(self) -> tuple[Optional[str], str]:
        """Locate the built helper, building it with swiftc as a fallback."""
        helper = self._find_helper()
        if helper is not None:
            return helper, ""
        swiftc = self._find_swiftc()
        if swiftc is None:
            return None, (
                f"{MACOS_HELPER_NAME} helper not found and swiftc is unavailable: "
                f"build/install the helper from {MACOS_SWIFT_SOURCE} or install Swift."
            )
        src = self._source_path()
        if not src or not os.path.isfile(src):
            return None, f"Swift helper source not found at {src}."
        out = self._default_helper_path() or os.path.join(tempfile.gettempdir(), MACOS_HELPER_NAME)
        try:
            out_dir = os.path.dirname(out)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            built = subprocess.run(
                [swiftc, "-O", src, "-o", out],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=180,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"Failed to build {MACOS_HELPER_NAME} with swiftc: {exc}"
        if built.returncode != 0:
            detail = (built.stderr or built.stdout or "").strip()[:400]
            return None, f"Failed to build {MACOS_HELPER_NAME} with swiftc: {detail}"
        return out, ""

    def _spawn(self, helper: str, w: int, h: int) -> subprocess.Popen:
        return self._popen(
            [helper, "--width", str(w), "--height", str(h)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def _wait_ready(self, proc: subprocess.Popen) -> tuple[Optional[dict], str]:
        """Read the helper's JSON status line within a bounded startup window."""
        lines: queue.Queue = queue.Queue()

        def reader() -> None:
            try:
                if proc.stdout is None:
                    return
                for line in proc.stdout:
                    lines.put(line)
            except Exception:
                pass

        threading.Thread(target=reader, name="vdisplay-macos-reader", daemon=True).start()
        deadline = time.monotonic() + self._ready_timeout
        while True:
            rc = proc.poll()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, "vdisplay helper did not become ready (startup timed out)"
            try:
                line = lines.get(timeout=min(self._poll_interval, remaining))
            except queue.Empty:
                if rc is not None:
                    detail = _proc_stderr_snippet(proc)
                    tail = f": {detail}" if detail else ""
                    return None, f"vdisplay helper exited during startup (code {rc}){tail}"
                continue
            if not isinstance(line, str) or not line.strip():
                continue
            try:
                msg = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "error":
                return None, str(msg.get("error") or "vdisplay helper reported an error")
            if msg.get("type") == "ready":
                return msg, ""

    def _terminate(self, proc: subprocess.Popen) -> None:
        """Stop a helper and close its pipes (helper exit removes the display)."""
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    proc.wait(timeout=1.0)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()

    def _launch_and_wait(self, w: int, h: int) -> str:
        """Start one helper process; return an error string or '' on success."""
        proc = self._spawn(self._helper, w, h)  # type: ignore[arg-type]
        msg, err = self._wait_ready(proc)
        if err:
            self._terminate(proc)
            return err
        did = msg.get("display_id") if msg else None
        self._proc = proc
        self._display_id = str(did) if did not in (None, "") else None
        self._width = w
        self._height = h
        self._started_at = time.monotonic()
        return ""

    def _clear_proc_state(self) -> None:
        self._proc = None
        self._display_id = None
        self._width = 0
        self._height = 0

    def create(
        self,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        **kwargs,
    ) -> dict:
        try:
            w, h = validate_size(width, height)
        except ValueError as exc:
            return self._error_status(str(exc), state="error")
        with self._lock:
            if self._desired and self._proc is not None and self._proc.poll() is None:
                if (self._width, self._height) == (w, h):
                    return {**self.status(), "ok": True, "reused": True}
                self.destroy()

            helper, err = self._resolve_helper()
            if err:
                return self._error_status(err)
            self._helper = helper
            self._want_w, self._want_h = w, h
            self._desired = True
            self._last_error = ""

            err = self._launch_and_wait(w, h)
            if err:
                self._desired = False
                return self._error_status(err)

            self._restarts = 0
            self._backoff = self._backoff_initial
            self._backoff_delays.clear()
            self._stop = threading.Event()
            self._monitor = threading.Thread(
                target=self._monitor_loop,
                name="vdisplay-macos-supervisor",
                daemon=True,
            )
            self._monitor.start()
            return {**self.status(), "ok": True}

    def _monitor_loop(self) -> None:
        """Restart the helper on unexpected exit, with exponential backoff."""
        while True:
            if self._stop.wait(self._poll_interval):
                return
            with self._lock:
                if not self._desired:
                    return
                proc = self._proc
                if proc is not None and proc.poll() is None:
                    if (
                        self._backoff > self._backoff_initial
                        and self._stable_after
                        and time.monotonic() - self._started_at >= self._stable_after
                    ):
                        self._backoff = self._backoff_initial
                    continue
                if proc is not None:
                    rc = proc.poll()
                    detail = _proc_stderr_snippet(proc)
                    tail = f": {detail}" if detail else ""
                    self._last_error = f"vdisplay helper exited with code {rc}{tail}"
                    self._terminate(proc)
                    self._clear_proc_state()
                if self._max_restarts and self._restarts >= self._max_restarts:
                    self._desired = False
                    return
                delay = self._backoff
                self._backoff_delays.append(delay)
                self._restarts += 1
                self._backoff = min(delay * 2.0, self._backoff_max)
                w, h = self._want_w, self._want_h
            self._sleeper(delay)
            with self._lock:
                if not self._desired or self._stop.is_set():
                    return
                err = self._launch_and_wait(w, h)
                self._last_error = err

    def destroy(self) -> dict:
        with self._lock:
            self._desired = False
            self._stop.set()
            monitor = self._monitor
            proc = self._proc
            if proc is not None:
                self._terminate(proc)
            self._clear_proc_state()
            self._helper = None
            self._last_error = ""
            result = {**self.status(), "ok": True}
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=2.0)
        return result

    def env(self) -> dict[str, str]:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return {}
            res: dict[str, str] = {}
            if self._display_id:
                res["SUBWAY_DISPLAY_ID"] = self._display_id
            if self._width and self._height:
                res["SUBWAY_DISPLAY_BOUNDS"] = f"0,0,{self._width},{self._height}"
            return res

    def status(self) -> dict:
        with self._lock:
            proc = self._proc
            active = proc is not None and proc.poll() is None
            if active:
                state, hud, error, advice = "on", "virtual display on", "", ""
            elif self._last_error:
                state, hud = "unavailable", "virtual display unavailable"
                error, advice = self._last_error, HDMI_DUMMY_ADVICE
            else:
                state, hud, error, advice = "off", "virtual display off", "", ""
            return {
                "ok": active or not bool(error),
                "active": active,
                "available": self.is_available(),
                "state": state,
                "hud_status": hud,
                "backend": "macos",
                "width": self._width if active else 0,
                "height": self._height if active else 0,
                "display": self._display_id if active else None,
                "pid": proc.pid if active and proc is not None else None,
                "env": self.env(),
                "physical_display": self.has_physical_display(),
                "error": error,
                "advice": advice,
            }

    def _error_status(self, message: str, *, state: str = "unavailable") -> dict:
        self._last_error = message
        result = self.status()
        result.update(
            {
                "ok": False,
                "active": False,
                "state": state,
                "hud_status": "virtual display unavailable",
                "width": 0,
                "height": 0,
                "display": None,
                "pid": None,
                "env": {},
                "error": message,
                "advice": HDMI_DUMMY_ADVICE,
            }
        )
        return result


_lock = threading.RLock()
_backend: Optional[VDisplayBackend] = None
_backend_factories: dict[str, Callable[[], VDisplayBackend]] = {}


def register_backend(
    platform_prefix: str, factory: Callable[[], VDisplayBackend]
) -> None:
    """Register a backend factory for a sys.platform prefix (e.g. 'darwin', 'win32')."""
    with _lock:
        _backend_factories[platform_prefix.lower()] = factory
        if _backend is not None and sys.platform.lower().startswith(platform_prefix.lower()):
            with contextlib.suppress(Exception):
                _backend.destroy()
            _reset_cached_backend()


def _reset_cached_backend() -> None:
    global _backend
    _backend = None


class WindowsVDisplayBackend(VDisplayBackend):
    """Virtual display backend for Windows delegating to vdisplay_windows.vdd_status()."""

    name = "windows"

    def is_available(self) -> bool:
        from vdisplay_windows import vdd_status
        return bool(vdd_status().get("installed"))

    def status(self) -> dict:
        from vdisplay_windows import vdd_status
        st = vdd_status()
        installed = bool(st.get("installed"))
        supported = bool(st.get("supported", True))
        return {
            "ok": bool(st.get("ok")),
            "active": installed,
            "available": installed,
            "supported": supported,
            "installed": installed,
            "state": "active" if installed else ("off" if supported else "unavailable"),
            "hud_status": "virtual display on" if installed else "virtual display off",
            "backend": self.name,
            "width": 0,
            "height": 0,
            "display": None,
            "pid": None,
            "env": {},
            "physical_display": False,
            "error": st.get("error", ""),
            "version": st.get("version"),
        }

    def create(
        self,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        **kwargs,
    ) -> dict:
        st = self.status()
        if not st.get("installed"):
            return {**st, "ok": False, "error": st.get("error") or "VDD driver not installed"}
        return st

    def destroy(self) -> dict:
        st = self.status()
        return {**st, "ok": True}

    def env(self) -> dict[str, str]:
        return {}


def _build_backend(platform_name: str) -> VDisplayBackend:
    plat = platform_name.lower()
    if plat in _backend_factories:
        return _backend_factories[plat]()
    for prefix, factory in _backend_factories.items():
        if plat.startswith(prefix):
            return factory()
    if plat.startswith("linux"):
        from vdisplay_linux import LinuxVDisplayBackend
        return LinuxVDisplayBackend()
    if plat.startswith("darwin"):
        return MacOSVDisplayBackend()
    if plat.startswith("win"):
        return WindowsVDisplayBackend()
    return UnsupportedBackend(plat)


def get_backend(platform: Optional[str] = None) -> VDisplayBackend:
    """Return the active virtual display backend (or instantiate one for `platform`)."""
    with _lock:
        if platform is not None:
            return _build_backend(platform)
        global _backend
        if _backend is None:
            _backend = _build_backend(sys.platform)
        return _backend


def set_backend(backend: Optional[VDisplayBackend]) -> None:
    """Override or clear the cached backend instance (destroying any previous active display)."""
    global _backend
    with _lock:
        if _backend is not None and _backend is not backend:
            with contextlib.suppress(Exception):
                _backend.destroy()
        _backend = backend


def has_physical_display() -> bool:
    """Return True if a physical or pre-existing display is detected."""
    with _lock:
        return get_backend().has_physical_display()


def create(
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    **kwargs,
) -> dict:
    """Create a virtual display at the requested dimensions."""
    try:
        w, h = validate_size(width, height)
    except ValueError as exc:
        return {
            "ok": False,
            "active": False,
            "available": get_backend().is_available(),
            "state": "error",
            "hud_status": "virtual display unavailable",
            "backend": get_backend().name,
            "width": 0,
            "height": 0,
            "display": None,
            "pid": None,
            "env": {},
            "physical_display": get_backend().has_physical_display(),
            "error": str(exc),
            "advice": HDMI_DUMMY_ADVICE,
        }
    with _lock:
        return get_backend().create(w, h, **kwargs)


def ensure(
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    *,
    force: bool = False,
    **kwargs,
) -> dict:
    """Ensure a display exists: reuse active vdisplay or physical display, else create one."""
    try:
        w, h = validate_size(width, height)
    except ValueError:
        return create(width, height, **kwargs)

    with _lock:
        backend = get_backend()
        st = backend.status()
        if st.get("active"):
            return {**st, "ok": True, "reused": True}
        if not force and backend.has_physical_display():
            return {
                **st,
                "ok": True,
                "active": False,
                "physical_display": True,
                "reused": False,
                "state": "off",
                "hud_status": "virtual display off",
                "error": "",
                "advice": "",
            }
        return backend.create(w, h, **kwargs)


def destroy() -> dict:
    """Destroy any active virtual display and restore environment state."""
    with _lock:
        if _backend is None:
            return {
                "ok": True,
                "active": False,
                "available": False,
                "state": "off",
                "hud_status": "virtual display off",
                "backend": "none",
                "width": 0,
                "height": 0,
                "display": None,
                "pid": None,
                "env": {},
                "physical_display": False,
                "error": "",
                "advice": "",
            }
        return _backend.destroy()


def status() -> dict:
    """Return current virtual display status."""
    with _lock:
        return get_backend().status()


def env() -> dict[str, str]:
    """Return environment variables for targeting the active virtual display."""
    with _lock:
        return get_backend().env()


def cleanup() -> None:
    """Best-effort teardown on process exit."""
    with _lock:
        if _backend is not None:
            with contextlib.suppress(Exception):
                _backend.destroy()


atexit.register(cleanup)


# --- Lease refcounting + idle teardown -------------------------------------------------
IDLE_SECONDS = 300.0
_leases: set = set()
_idle_timer: Optional[threading.Timer] = None
_idle_seconds: float = IDLE_SECONDS
_teardown_done = threading.Event()  # set each time an idle teardown completes (tests wait on it)


def _cancel_idle_locked() -> None:
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
        _idle_timer = None


def _idle_fire(timer: threading.Timer) -> None:
    global _idle_timer
    with _lock:
        if _idle_timer is not timer or _leases:
            return  # cancelled or re-acquired meanwhile
        _idle_timer = None
        with contextlib.suppress(Exception):
            destroy()
    _teardown_done.set()


def acquire_lease(owner_id: str, width: int = 1440, height: int = 900, *, idle_seconds: Optional[float] = None) -> dict:
    """Hold a display for owner_id. Creates a vdisplay only when no physical display exists."""
    global _idle_seconds
    owner = str(owner_id)
    with _lock:
        if idle_seconds is not None:
            _idle_seconds = float(idle_seconds)
        _cancel_idle_locked()
        _leases.add(owner)
        if has_physical_display():
            return {**status(), "ok": True, "leased": False, "leases": len(_leases)}
        res = ensure(width, height)
        return {**res, "leased": True, "leases": len(_leases)}


def ensure_for_consumer(owner_id: str, width: int = 1440, height: int = 900) -> dict:
    return acquire_lease(owner_id, width, height)


def release_lease(owner_id: str, *, idle_seconds: Optional[float] = None) -> dict:
    """Drop owner_id's lease; when none remain, schedule teardown after idle_seconds."""
    global _idle_timer, _idle_seconds
    with _lock:
        if idle_seconds is not None:
            _idle_seconds = float(idle_seconds)
        _leases.discard(str(owner_id))
        if not _leases:
            _cancel_idle_locked()
            _teardown_done.clear()
            t = threading.Timer(_idle_seconds, lambda: _idle_fire(t))
            t.daemon = True
            _idle_timer = t
            t.start()
        return {"ok": True, "leases": len(_leases), "teardown_scheduled": _idle_timer is not None}


def lease_count() -> int:
    with _lock:
        return len(_leases)


def display_env() -> dict[str, str]:
    """DISPLAY / SUBWAY_DISPLAY_ID / SUBWAY_DISPLAY_BOUNDS for the active vdisplay ({} if none)."""
    with _lock:
        e = dict(get_backend().env() or {})
        st = get_backend().status() or {}
    if st.get("active") or e:
        if st.get("display") and "DISPLAY" not in e and sys.platform.startswith("linux"):
            e["DISPLAY"] = str(st["display"])
        if st.get("display") and "SUBWAY_DISPLAY_ID" not in e:
            e["SUBWAY_DISPLAY_ID"] = str(st["display"])
        if "SUBWAY_DISPLAY_BOUNDS" not in e:
            e["SUBWAY_DISPLAY_BOUNDS"] = f"0,0,{st.get('width') or 1440},{st.get('height') or 900}"
    return e
