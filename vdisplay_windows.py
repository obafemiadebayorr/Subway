"""Windows virtual display (IDD) guided installer.

Explicit, user-click only: nothing here runs on import or from ensure().
Installs a pinned, SHA256-verified signed Virtual Display Driver release via
``pnputil``. Fallback when the driver cannot be installed: an HDMI/DisplayPort
dummy plug (see ``vdisplay.HDMI_DUMMY_ADVICE``).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from typing import Callable, List, Optional

VDD_VERSION = "24.12.24"
VDD_URL = (
    "https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/download/"
    "24.12.24/VDD.Control.24.12.24.zip"
)
# Pin: must match the release archive above. Mismatch => install refused.
VDD_SHA256 = "0" * 64
VDD_HARDWARE_ID = "Root\\MttVDD"

MANUAL_INSTRUCTIONS = (
    "Administrator rights are required. Manual steps: 1) open an elevated "
    "terminal (Run as administrator); 2) download " + VDD_URL + " and verify "
    "its SHA256 is " + VDD_SHA256 + "; 3) extract it and run "
    "'pnputil /add-driver MttVDD.inf /install'. Fallback: plug in an HDMI/"
    "DisplayPort dummy plug."
)


def is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def verify_sha256(path: str, expected: str) -> bool:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return False
    return h.hexdigest().lower() == (expected or "").strip().lower()


def _run(args: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=120)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def _download(url: str, dest: str) -> None:
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:  # noqa: S310
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def vdd_status(run: Callable[[list[str]], tuple[int, str]] = _run,
               platform: Optional[str] = None) -> dict:
    """Report whether the VDD driver is present. Read-only."""
    if (platform or sys.platform) != "win32":
        return {"ok": False, "installed": False, "supported": False,
                "error": "not windows"}
    code, out = run(["pnputil", "/enum-devices", "/class", "Display"])
    installed = code == 0 and ("MttVDD" in out or "Virtual Display Driver" in out)
    return {"ok": code == 0, "installed": installed, "supported": True,
            "version": VDD_VERSION}


def install_vdd(confirm: bool = False, archive_path: Optional[str] = None,
                expected_sha256: str = VDD_SHA256, *,
                is_admin: Callable[[], bool] = is_admin,
                download: Callable[[str, str], None] = _download,
                run: Callable[[list[str]], tuple[int, str]] = _run,
                platform: Optional[str] = None) -> dict:
    if not confirm:
        return {"ok": False, "error": "confirmation required; install is explicit"}
    if (platform or sys.platform) != "win32":
        return {"ok": False, "error": "not windows"}
    if not is_admin():
        return {"ok": False, "error": "administrator rights required",
                "instructions": MANUAL_INSTRUCTIONS}
    path = archive_path
    tmpdir = None
    try:
        if not path:
            tmpdir = tempfile.mkdtemp(prefix="vdd-")
            path = os.path.join(tmpdir, "vdd.zip")
            try:
                download(VDD_URL, path)
            except Exception as exc:
                return {"ok": False, "error": f"download failed: {exc}"}
        if not verify_sha256(path, expected_sha256):
            return {"ok": False,
                    "error": "hash mismatch: refusing to install unverified driver"}
        inf = path if path.lower().endswith(".inf") else None
        if inf is None:
            return _install_zip(path, run)
        code, out = run(["pnputil", "/add-driver", inf, "/install"])
        return {"ok": code == 0, "output": out} if code == 0 else \
            {"ok": False, "error": f"pnputil failed: {out.strip()}"}
    finally:
        if tmpdir:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


def _install_zip(path: str, run) -> dict:
    import zipfile
    dest = tempfile.mkdtemp(prefix="vdd-x-")
    try:
        with zipfile.ZipFile(path) as z:
            base = os.path.realpath(dest)
            for n in z.namelist():
                if not os.path.realpath(os.path.join(base, n)).startswith(base):
                    return {"ok": False, "error": "unsafe path in archive"}
            z.extractall(dest)
        infs = [os.path.join(r, f) for r, _, fs in os.walk(dest)
                for f in fs if f.lower().endswith(".inf")]
        if not infs:
            return {"ok": False, "error": "no .inf found in archive"}
        code, out = run(["pnputil", "/add-driver", infs[0], "/install"])
        if code != 0:
            return {"ok": False, "error": f"pnputil failed: {out.strip()}"}
        return {"ok": True, "output": out}
    except zipfile.BadZipFile:
        return {"ok": False, "error": "corrupt archive"}
    finally:
        import shutil
        shutil.rmtree(dest, ignore_errors=True)


def uninstall_vdd(confirm: bool = False, *,
                  is_admin: Callable[[], bool] = is_admin,
                  run: Callable[[list[str]], tuple[int, str]] = _run,
                  platform: Optional[str] = None) -> dict:
    if not confirm:
        return {"ok": False, "error": "confirmation required; uninstall is explicit"}
    if (platform or sys.platform) != "win32":
        return {"ok": False, "error": "not windows"}
    if not is_admin():
        return {"ok": False, "error": "administrator rights required",
                "instructions": MANUAL_INSTRUCTIONS.replace(
                    "'pnputil /add-driver MttVDD.inf /install'",
                    "'pnputil /remove-device /deviceid " + VDD_HARDWARE_ID + "'")}
    code, out = run(["pnputil", "/remove-device", "/deviceid", VDD_HARDWARE_ID])
    if code != 0:
        return {"ok": False, "error": f"pnputil failed: {out.strip()}"}
    return {"ok": True, "output": out}


# --- H9: headless capture helpers (IDXGIOutputDuplication) ---
MAX_FRAME_BYTES = 256 * 1024
HELPER_ENV = "SUBWAY_DXGI_CAPTURE"
HELPER_NAME = "subway-dxgi-capture.exe"


def find_dxgi_helper(env: Optional[dict] = None) -> Optional[str]:
    env = os.environ if env is None else env
    explicit = env.get(HELPER_ENV)
    if explicit and os.path.isfile(explicit):
        return explicit
    return shutil.which(HELPER_NAME)


def build_dxgi_capture_cmd(helper: str, output_index: int = 0, width: int = 1440, height: int = 900,
                           fps: int = 30, quality: int = 60, dirty_rects: bool = False) -> List[str]:
    """Command line for the IDXGIOutputDuplication helper (never started here)."""
    width, height = max(160, int(width)), max(120, int(height))
    fps = max(1, min(int(fps), 60))
    quality = max(10, min(int(quality), 95))
    cmd = [helper, "--output", str(max(0, int(output_index))), "--width", str(width), "--height", str(height),
           "--fps", str(fps), "--quality", str(quality), "--max-bytes", str(MAX_FRAME_BYTES)]
    if dirty_rects:
        cmd.append("--dirty-rects")  # AcquireNextFrame + GetFrameDirtyRects: only changed regions re-encoded
    return cmd


class DxgiCapture:
    """Builds the helper command for one adapter output; the process is owned by HeadlessCaptureSession."""

    def __init__(self, helper: Optional[str] = None, output_index: int = 0):
        self.helper = helper or find_dxgi_helper()
        self.output_index = output_index

    @property
    def available(self) -> bool:
        return bool(self.helper)

    def command(self, **kw) -> Optional[List[str]]:
        if not self.helper:
            return None
        return build_dxgi_capture_cmd(self.helper, self.output_index, **kw)
