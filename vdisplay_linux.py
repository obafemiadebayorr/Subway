"""Linux virtual display backend (Xvfb default, headless wlroots if detected)."""
from __future__ import annotations

import contextlib
import glob
import os
import shutil
import subprocess
import threading
import time
from typing import Callable, Optional

from vdisplay import (
    DEFAULT_DEPTH,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    HDMI_DUMMY_ADVICE,
    VDisplayBackend,
    validate_size,
)

_WLROOTS_DESKTOPS = frozenset(
    {"sway", "labwc", "wayfire", "river", "hyprland", "wlroots"}
)
_WLROOTS_ENV_MARKERS = (
    ("SWAYSOCK", "sway"),
    ("HYPRLAND_INSTANCE_SIGNATURE", "hyprland"),
    ("LABWC_PID", "labwc"),
    ("RIVER_PID", "river"),
    ("WAYFIRE_CONFIG_FILE", "wayfire"),
)
_VALID_DEPTHS = (8, 15, 16, 24)


def detect_wlroots(environ: Optional[dict[str, str]] = None) -> Optional[str]:
    """Return the detected wlroots compositor name if the user already runs wlroots, else None."""
    env_map = environ if environ is not None else os.environ
    for var, name in _WLROOTS_ENV_MARKERS:
        if env_map.get(var, "").strip():
            return name

    session_type = env_map.get("XDG_SESSION_TYPE", "").strip().lower()
    has_wayland = bool(env_map.get("WAYLAND_DISPLAY", "").strip()) or session_type == "wayland"
    if not has_wayland:
        return None

    saw_generic = False
    for key in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP"):
        raw = env_map.get(key, "").strip().lower()
        for token in raw.split(":"):
            tok = token.strip()
            if tok == "wlroots":
                saw_generic = True
            elif tok in _WLROOTS_DESKTOPS:
                return tok
    return "wlroots" if saw_generic else None


def _read_stderr_snippet(proc: subprocess.Popen, limit: int = 400) -> str:
    if proc.stderr is None:
        return ""
    try:
        data = proc.stderr.read(limit)
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace").strip()
        return str(data).strip()
    except Exception:
        return ""


class LinuxVDisplayBackend(VDisplayBackend):
    """Manage a virtual X11 display via Xvfb (default) or headless wlroots compositor."""

    name = "linux"

    def __init__(
        self,
        *,
        xvfb_bin: Optional[str] = None,
        wlroots_bins: tuple[str, ...] = ("labwc", "sway", "cage"),
        socket_dir: str = "/tmp/.X11-unix",
        lock_dir: str = "/tmp",
        runtime_dir: Optional[str] = None,
        ready_timeout: float = 1.5,
        poll_interval: float = 0.02,
        display_range: range = range(99, 200),
        popen: Optional[Callable[..., subprocess.Popen]] = None,
        which: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._xvfb_bin_override = xvfb_bin
        self._wlroots_bins = wlroots_bins
        self._socket_dir = socket_dir
        self._lock_dir = lock_dir
        self._runtime_dir_override = runtime_dir
        self._ready_timeout = max(0.02, float(ready_timeout))
        self._poll_interval = max(0.005, float(poll_interval))
        self._display_range = display_range
        self._popen_override = popen
        self._which_override = which

        self._proc: Optional[subprocess.Popen] = None
        self._backend_kind: Optional[str] = None
        self._display: Optional[str] = None
        self._wayland_display: Optional[str] = None
        self._width: int = 0
        self._height: int = 0
        self._depth: int = DEFAULT_DEPTH
        self._last_error: str = ""

        self._had_prev_display: bool = False
        self._prev_display: Optional[str] = None
        self._had_prev_wayland: bool = False
        self._prev_wayland_display: Optional[str] = None

    def _which(self, name: str) -> Optional[str]:
        fn = self._which_override if self._which_override is not None else shutil.which
        return fn(name)

    def _popen(self, cmd: list[str], **kwargs) -> subprocess.Popen:
        fn = self._popen_override if self._popen_override is not None else subprocess.Popen
        return fn(cmd, **kwargs)

    def _find_xvfb(self) -> Optional[str]:
        if self._xvfb_bin_override:
            return self._xvfb_bin_override if os.path.exists(self._xvfb_bin_override) else None
        return self._which("Xvfb")

    def _find_wlroots_compositor(self) -> Optional[tuple[str, str]]:
        for binary in self._wlroots_bins:
            path = self._which(binary)
            if path:
                return binary, path
        return None

    def _runtime_dir(self) -> str:
        if self._runtime_dir_override is not None:
            return self._runtime_dir_override
        return os.environ.get("XDG_RUNTIME_DIR", "").strip()

    def _find_free_display(self) -> Optional[int]:
        for num in self._display_range:
            lock_path = os.path.join(self._lock_dir, f".X{num}-lock")
            sock_path = os.path.join(self._socket_dir, f"X{num}")
            if not os.path.exists(lock_path) and not os.path.exists(sock_path):
                return num
        return None

    def _parse_display_num(self, display: object) -> Optional[int]:
        if isinstance(display, int) and not isinstance(display, bool) and display >= 0:
            return display
        if isinstance(display, str):
            text = display.strip()
            if text.startswith(":"):
                text = text[1:]
            text = text.split(".", 1)[0]
            if text.isdigit():
                return int(text)
        return None

    def is_available(self) -> bool:
        if self._find_xvfb() is not None:
            return True
        if detect_wlroots() is not None and self._find_wlroots_compositor() is not None:
            return True
        return False

    def has_physical_display(self) -> bool:
        with self._lock:
            disp = os.environ.get("DISPLAY", "").strip()
            wdisp = os.environ.get("WAYLAND_DISPLAY", "").strip()
            if disp and disp != self._display:
                return True
            if wdisp and wdisp != self._wayland_display:
                return True
            return False

    def _reap_if_exited(self) -> None:
        if self._proc is None:
            return
        rc = self._proc.poll()
        if rc is None:
            return
        err = _read_stderr_snippet(self._proc)
        kind = self._backend_kind or "vdisplay"
        self._restore_env()
        self._clear_state()
        self._last_error = f"{kind} exited with code {rc}" + (f": {err}" if err else "")

    def _restore_env(self) -> None:
        if self._display and os.environ.get("DISPLAY") == self._display:
            if self._had_prev_display and self._prev_display is not None:
                os.environ["DISPLAY"] = self._prev_display
            else:
                os.environ.pop("DISPLAY", None)
        if self._wayland_display and os.environ.get("WAYLAND_DISPLAY") == self._wayland_display:
            if self._had_prev_wayland and self._prev_wayland_display is not None:
                os.environ["WAYLAND_DISPLAY"] = self._prev_wayland_display
            else:
                os.environ.pop("WAYLAND_DISPLAY", None)

    def _clear_state(self) -> None:
        self._proc = None
        self._backend_kind = None
        self._display = None
        self._wayland_display = None
        self._width = 0
        self._height = 0
        self._had_prev_display = False
        self._prev_display = None
        self._had_prev_wayland = False
        self._prev_wayland_display = None

    def _wait_ready(
        self,
        proc: subprocess.Popen,
        probe: Callable[[], bool],
    ) -> tuple[bool, Optional[int], str]:
        """Wait until `probe()` is True or `proc` exits or ready_timeout elapses."""
        waiter = threading.Event()
        deadline = time.monotonic() + self._ready_timeout
        while True:
            rc = proc.poll()
            if rc is not None:
                return False, rc, _read_stderr_snippet(proc)
            if probe():
                return True, None, ""
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Process stayed alive through startup window (e.g. stub without socket).
                return proc.poll() is None, proc.poll(), ""
            waiter.wait(min(self._poll_interval, remaining))

    def _error_status(self, message: str, *, state: str = "unavailable") -> dict:
        self._last_error = message
        return {
            "ok": False,
            "active": False,
            "available": self.is_available(),
            "state": state,
            "hud_status": "virtual display unavailable",
            "backend": self._backend_kind or ("wlroots" if detect_wlroots() else "xvfb"),
            "width": 0,
            "height": 0,
            "display": None,
            "pid": None,
            "env": {},
            "physical_display": self.has_physical_display(),
            "error": message,
            "advice": HDMI_DUMMY_ADVICE,
        }

    def _start_xvfb(
        self,
        width: int,
        height: int,
        depth: int,
        display: Optional[object] = None,
    ) -> dict:
        xvfb = self._find_xvfb()
        if not xvfb:
            return self._error_status(
                "Xvfb is not installed (no virtual display backend found). "
                "Install xvfb (e.g. apt install xvfb) or use an HDMI dummy plug."
            )

        if display is not None:
            num = self._parse_display_num(display)
            if num is None:
                return self._error_status(
                    f"Invalid X11 display specification: {display!r}", state="error"
                )
        else:
            num = self._find_free_display()
            if num is None:
                return self._error_status("No free X11 display number available.")

        disp_str = f":{num}"
        cmd = [
            xvfb,
            disp_str,
            "-screen",
            "0",
            f"{width}x{height}x{depth}",
            "-nolisten",
            "tcp",
            "-ac",
        ]
        try:
            proc = self._popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            return self._error_status(f"Failed to start Xvfb: {exc}")

        sock_path = os.path.join(self._socket_dir, f"X{num}")
        ok, rc, err = self._wait_ready(proc, lambda: os.path.exists(sock_path))
        if not ok:
            with contextlib.suppress(Exception):
                proc.wait(timeout=0.5)
            detail = f": {err}" if err else ""
            return self._error_status(f"Xvfb exited during startup (code {rc}){detail}")

        self._had_prev_display = "DISPLAY" in os.environ
        self._prev_display = os.environ.get("DISPLAY")
        os.environ["DISPLAY"] = disp_str

        self._proc = proc
        self._backend_kind = "xvfb"
        self._display = disp_str
        self._wayland_display = None
        self._width = width
        self._height = height
        self._depth = depth
        self._last_error = ""
        return {**self.status(), "ok": True}

    def _wayland_sockets(self, runtime_dir: str) -> set[str]:
        if not runtime_dir or not os.path.isdir(runtime_dir):
            return set()
        return {
            os.path.basename(p)
            for p in glob.glob(os.path.join(runtime_dir, "wayland-*"))
            if not p.endswith(".lock")
        }

    def _start_wlroots(self, width: int, height: int) -> dict:
        found = self._find_wlroots_compositor()
        if not found:
            return self._error_status(
                "No headless wlroots compositor (labwc/sway/cage) found on PATH."
            )
        name, binary = found
        runtime_dir = self._runtime_dir()
        before = self._wayland_sockets(runtime_dir)

        child_env = os.environ.copy()
        child_env["WLR_BACKENDS"] = "headless"
        child_env["WLR_LIBINPUT_NO_DEVICES"] = "1"
        child_env["WLR_HEADLESS_OUTPUTS"] = "1"
        child_env["SUBWAY_WLROOTS_SIZE"] = f"{width}x{height}"

        cmd = [binary]
        if name == "sway":
            cmd.append("--unsupported-gpu")

        try:
            proc = self._popen(
                cmd,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            return self._error_status(f"Failed to start headless {name}: {exc}")

        discovered: list[str] = []

        def _probe_socket() -> bool:
            after = self._wayland_sockets(runtime_dir) - before
            if after:
                discovered.append(sorted(after)[0])
                return True
            return False

        ok, rc, err = self._wait_ready(proc, _probe_socket)
        if not ok:
            with contextlib.suppress(Exception):
                proc.wait(timeout=0.5)
            detail = f": {err}" if err else ""
            return self._error_status(
                f"Headless {name} exited during startup (code {rc}){detail}"
            )

        wdisp = discovered[0] if discovered else f"wayland-subway-{proc.pid}"
        self._had_prev_wayland = "WAYLAND_DISPLAY" in os.environ
        self._prev_wayland_display = os.environ.get("WAYLAND_DISPLAY")
        os.environ["WAYLAND_DISPLAY"] = wdisp

        self._proc = proc
        self._backend_kind = f"wlroots:{name}"
        self._display = None
        self._wayland_display = wdisp
        self._width = width
        self._height = height
        self._last_error = ""
        return {**self.status(), "ok": True}

    def create(
        self,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        *,
        depth: int = DEFAULT_DEPTH,
        backend: str = "auto",
        display: Optional[object] = None,
        **kwargs,
    ) -> dict:
        try:
            w, h = validate_size(width, height)
        except ValueError as exc:
            return self._error_status(str(exc), state="error")

        if isinstance(depth, bool) or depth not in _VALID_DEPTHS:
            return self._error_status(
                f"Unsupported color depth {depth!r}; expected one of {_VALID_DEPTHS}.",
                state="error",
            )

        mode = str(backend or "auto").strip().lower()
        if mode not in ("auto", "xvfb", "wlroots"):
            return self._error_status(f"Unknown Linux vdisplay backend {backend!r}.", state="error")

        with self._lock:
            self._reap_if_exited()
            if self._proc is not None:
                if self._width == w and self._height == h:
                    return {**self.status(), "ok": True, "reused": True}
                self.destroy()

            if mode == "wlroots":
                return self._start_wlroots(w, h)
            if mode == "auto" and detect_wlroots() is not None and self._find_wlroots_compositor() is not None:
                return self._start_wlroots(w, h)
            return self._start_xvfb(w, h, depth, display=display)

    def destroy(self) -> dict:
        with self._lock:
            proc = self._proc
            if proc is not None:
                if proc.poll() is None:
                    with contextlib.suppress(OSError):
                        proc.terminate()
                    try:
                        proc.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        with contextlib.suppress(OSError):
                            proc.kill()
                        with contextlib.suppress(Exception):
                            proc.wait(timeout=1.0)
                if proc.stderr is not None:
                    with contextlib.suppress(Exception):
                        proc.stderr.close()
            self._restore_env()
            self._clear_state()
            self._last_error = ""
            return {**self.status(), "ok": True}

    def env(self) -> dict[str, str]:
        with self._lock:
            self._reap_if_exited()
            if self._proc is None:
                return {}
            res: dict[str, str] = {}
            if self._display:
                res["DISPLAY"] = self._display
            if self._wayland_display:
                res["WAYLAND_DISPLAY"] = self._wayland_display
            if self._width and self._height:
                res["SUBWAY_DISPLAY_BOUNDS"] = f"0,0,{self._width},{self._height}"
            return res

    def status(self) -> dict:
        with self._lock:
            self._reap_if_exited()
            active = self._proc is not None
            avail = self.is_available()
            phys = self.has_physical_display()

            if active:
                state = "on"
                hud_status = "virtual display on"
                error = ""
                advice = ""
            elif self._last_error:
                state = "unavailable"
                hud_status = "virtual display unavailable"
                error = self._last_error
                advice = HDMI_DUMMY_ADVICE
            elif not avail:
                state = "unavailable"
                hud_status = "virtual display unavailable"
                error = "Xvfb is not installed."
                advice = HDMI_DUMMY_ADVICE
            else:
                state = "off"
                hud_status = "virtual display off"
                error = ""
                advice = ""

            default_kind = "wlroots" if detect_wlroots() else "xvfb"
            return {
                "ok": active or not bool(error),
                "active": active,
                "available": avail,
                "state": state,
                "hud_status": hud_status,
                "backend": self._backend_kind or default_kind,
                "width": self._width if active else 0,
                "height": self._height if active else 0,
                "display": (self._display or self._wayland_display) if active else None,
                "pid": self._proc.pid if active and self._proc is not None else None,
                "env": self.env(),
                "physical_display": phys,
                "error": error,
                "advice": advice,
            }


# --- Headless capture command builders (no real display needed to build) -----

def build_x11grab_cmd(display: str, width: int = 1440, height: int = 900, fps: int = 30,
                      quality: int = 5, ffmpeg: str = "ffmpeg") -> list:
    """ffmpeg x11grab against Xvfb :N, MJPEG frames on stdout (parse with JPEG SOI/EOI framing)."""
    if not display or not str(display).startswith(":"):
        raise ValueError("display must look like ':99'")
    fps = max(1, min(int(fps), 60))
    return [ffmpeg, "-loglevel", "error", "-f", "x11grab", "-framerate", str(fps),
            "-video_size", "%dx%d" % (int(width), int(height)), "-i", str(display),
            "-vf", "scale=%d:%d" % (int(width), int(height)), "-q:v", str(max(2, min(int(quality), 31))),
            "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]


def build_pipewire_cmd(node: str = "", width: int = 1440, height: int = 900, fps: int = 30,
                       gst: str = "gst-launch-1.0") -> list:
    """PipeWire screencast capture through GStreamer, JPEG frames to stdout."""
    src = ["pipewiresrc"] + (["path=%s" % node] if node else [])
    return [gst, "-q"] + src + ["!", "videorate", "!", "videoscale", "!",
            "video/x-raw,width=%d,height=%d,framerate=%d/1" % (int(width), int(height), max(1, min(int(fps), 60))),
            "!", "videoconvert", "!", "jpegenc", "!", "fdsink", "fd=1"]
