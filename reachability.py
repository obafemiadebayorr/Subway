"""Reachability: sleep inhibit while linked, Wake-on-LAN, and authenticated restart."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional


def _platform(platform: Optional[str] = None) -> str:
    p = (platform or sys.platform).lower()
    if p.startswith("darwin") or p == "mac":
        return "darwin"
    if p.startswith("win"):
        return "win32"
    return "linux"


class SleepInhibitor:
    """Holds an OS idle/sleep inhibitor only while >= 1 peer is linked.

    `backend` (optional) is an object with acquire() -> handle and release(handle);
    `popen` overrides subprocess.Popen for the process-based platforms.
    """

    def __init__(self, platform: Optional[str] = None, popen: Optional[Callable[..., Any]] = None,
                 backend: Any = None, pid: Optional[int] = None):
        self.platform = _platform(platform)
        self._popen = popen or subprocess.Popen
        self._backend = backend
        self._pid = pid or os.getpid()
        self._handle: Any = None
        self._held = False
        self._lock = threading.Lock()

    @property
    def held(self) -> bool:
        return self._held

    def command(self) -> Optional[List[str]]:
        if self.platform == "darwin":
            return ["caffeinate", "-i", "-w", str(self._pid)]
        if self.platform == "linux":
            return ["systemd-inhibit", "--what=idle:sleep", "--who=subway",
                    "--why=Linked peer active", "sleep", "infinity"]
        return None

    def acquire(self) -> bool:
        with self._lock:
            if self._held:
                return True
            try:
                if self._backend is not None:
                    self._handle = self._backend.acquire()
                elif self.platform == "win32":
                    import ctypes
                    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
                    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
                    self._handle = True
                else:
                    cmd = self.command()
                    self._handle = self._popen(cmd, stdin=subprocess.DEVNULL,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                self._handle = None
                return False
            self._held = True
            return True

    def release(self) -> None:
        with self._lock:
            if not self._held:
                return
            h, self._handle, self._held = self._handle, None, False
            try:
                if self._backend is not None:
                    self._backend.release(h)
                elif self.platform == "win32":
                    import ctypes
                    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
                elif h is not None:
                    h.terminate()
            except Exception:
                pass

    def sync(self, linked_count: int) -> bool:
        if linked_count >= 1:
            self.acquire()
        else:
            self.release()
        return self._held


_INHIBITOR: Optional[SleepInhibitor] = None


def sync_inhibitor(linked_count: int, inhibitor: Optional[SleepInhibitor] = None) -> bool:
    """Best-effort sync of the process-wide inhibitor with the linked-peer count."""
    global _INHIBITOR
    if inhibitor is None:
        if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("SUBWAY_NO_SLEEP_INHIBIT"):
            return False
        if _INHIBITOR is None:
            _INHIBITOR = SleepInhibitor()
        inhibitor = _INHIBITOR
    try:
        return inhibitor.sync(int(linked_count))
    except Exception:
        return False


def get_primary_mac_and_subnet() -> Dict[str, Optional[str]]:
    """Best-effort primary NIC MAC (aa:bb:cc:dd:ee:ff) and /24 subnet prefix; never raises."""
    out: Dict[str, Optional[str]] = {"mac": None, "subnet": None}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))  # no packets sent for UDP connect
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            out["subnet"] = ".".join(ip.split(".")[:3]) + ".0/24"
    except Exception:
        pass
    try:
        node = uuid.getnode()
        if not (node >> 40) & 1:  # multicast bit set means random, not a real NIC MAC
            out["mac"] = ":".join(f"{(node >> s) & 0xFF:02x}" for s in range(40, -1, -8))
    except Exception:
        pass
    return out


def _mac_bytes(mac: str) -> bytes:
    hexs = "".join(c for c in str(mac or "") if c not in ":-. ")
    if len(hexs) != 12:
        raise ValueError("invalid MAC address")
    return bytes.fromhex(hexs)


def build_magic_packet(mac: str) -> bytes:
    return b"\xff" * 6 + _mac_bytes(mac) * 16


def send_wol(mac: str, broadcast: str = "255.255.255.255", port: int = 9, sock=None) -> bool:
    pkt = build_magic_packet(mac)
    own = sock is None
    if own:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.sendto(pkt, (broadcast, port))
    finally:
        if own:
            sock.close()
    return True


def build_reboot_cmd(platform: Optional[str] = None) -> List[str]:
    p = _platform(platform)
    if p == "darwin":
        return ["sudo", "fdesetup", "authrestart"]
    if p == "win32":
        return ["powershell", "-Command",
                "Suspend-BitLocker -MountPoint 'C:' -RebootCount 1; Restart-Computer -Force"]
    return ["sudo", "systemctl", "reboot"]


def reboot_station(dry_run: bool = False, platform: Optional[str] = None,
                   runner: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    cmd = build_reboot_cmd(platform)
    if dry_run:
        return {"ok": True, "dry_run": True, "cmd": cmd}
    try:
        r = (runner or subprocess.run)(cmd, check=False)
        rc = getattr(r, "returncode", 0)
    except Exception as e:
        return {"ok": False, "dry_run": False, "cmd": cmd, "error": type(e).__name__}
    return {"ok": rc == 0, "dry_run": False, "cmd": cmd, "returncode": rc}
