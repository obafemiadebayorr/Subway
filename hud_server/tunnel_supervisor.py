"""Keeps the Subway Tunnel connector (bin/subway-tunnel.mjs) running beside the HUD.

Starts once the tunnel is set up (station.json has a relay url) and node is on PATH. Restarts the
connector when it exits, backing off 5s doubling to 60s. If another connector for this Mac is
already running (say, `subway tunnel` in a terminal), it waits and takes over only when that one stops,
so two connectors never knock each other off the relay.

Off with config.json `"tunnel_autostart": false`, or SUBWAY_NO_TUNNEL=1. Never runs inside workers.
Output goes to <subway_dir>/tunnel/connector.log (the connector logs no payloads or headers).
"""
import json
import os
import shutil
import signal
import sys
import socket
import subprocess
import threading
import time
from typing import Callable, List, Optional

import subway_home

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(_REPO, "bin", "subway-tunnel.mjs")

_started = False
_lock = threading.Lock()
_proc: Optional[subprocess.Popen] = None


def _tunnel_dir() -> str:
    return os.path.join(subway_home.subway_dir(), "tunnel")


def relay_url() -> str:
    try:
        with open(os.path.join(_tunnel_dir(), "station.json"), "r", encoding="utf-8") as fh:
            return str(json.load(fh).get("url") or "").strip()
    except (OSError, ValueError, AttributeError):
        return ""


def enabled(cfg: dict) -> bool:
    if os.environ.get("SUBWAY_WORKER") or os.environ.get("SUBWAY_NO_TUNNEL"):
        return False
    v = (cfg or {}).get("tunnel_autostart", True)
    return not (v is False or str(v).lower() in ("false", "off", "0", "no"))


def _find_tunnel_pids() -> List[tuple]:
    """Cross-platform scan for running tunnel connectors: list of (pid, cmdline) pairs.
    Own connector is skipped. POSIX uses pgrep; Windows uses psutil (PowerShell fallback)."""
    mine = _proc.pid if _proc and _proc.poll() is None else None
    pairs: List[tuple] = []
    if sys.platform == "win32":
        try:
            import psutil  # type: ignore

            for p in psutil.process_iter(["pid", "cmdline"]):
                try:
                    cmdline = " ".join(p.info.get("cmdline") or [])
                    if "subway-tunnel.mjs" in cmdline and p.info["pid"] != mine:
                        pairs.append((int(p.info["pid"]), cmdline))
                except (psutil.Error, OSError, TypeError, ValueError):
                    continue
        except ImportError:
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-CimInstance Win32_Process | "
                     "Where-Object { $_.CommandLine -like '*subway-tunnel.mjs*' } | "
                     "Select-Object -ExpandProperty CommandLine"],
                    capture_output=True, text=True, timeout=10).stdout
            except (OSError, subprocess.SubprocessError):
                return []
            for line in out.splitlines():
                cmd = line.strip()
                if cmd and "subway-tunnel.mjs" in cmd:
                    pairs.append((-1, cmd))
            return pairs
    else:
        try:
            out = subprocess.run(["pgrep", "-fl", "subway-tunnel.mjs"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        for line in out.splitlines():
            pid, _, cmd = line.strip().partition(" ")
            if pid.isdigit() and int(pid) != mine:
                pairs.append((int(pid), cmd))
    return pairs


def other_connector_running(port: int = 7890) -> bool:
    """A connector for this HUD port that this supervisor didn't start (e.g. `subway tunnel` in a terminal).
    Connectors of other stations on this machine (another SUBWAY_DIR and port) don't count."""
    for _, cmd in _find_tunnel_pids():
        parts = cmd.split()
        their = int(parts[parts.index("--port") + 1]) if "--port" in parts[:-1] and parts[parts.index("--port") + 1].isdigit() else 7890
        if their == port:
            return True
    return False


def start(port: int, cfg: dict, log: Callable[[str], None]) -> bool:
    """Start the supervisor thread once per process. Returns True if it started."""
    global _started
    with _lock:
        if _started or not enabled(cfg):
            return False
        node = shutil.which("node")
        if not node or not os.path.exists(SCRIPT) or not relay_url():
            return False
        _started = True
    threading.Thread(target=_loop, args=(node, port, log), daemon=True, name="subway-tunnel").start()
    return True


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def reap_orphans(port: int = 7890) -> List[int]:
    """Stop connectors whose HUD port no longer listens: leftovers of HUDs that exited."""
    killed: List[int] = []
    for pid, cmd in _find_tunnel_pids():
        if pid < 0:
            continue
        parts = cmd.split()
        their = int(parts[parts.index("--port") + 1]) if "--port" in parts[:-1] and parts[parts.index("--port") + 1].isdigit() else 7890
        if their != port and not _port_open(their):
            try:
                os.kill(int(pid), signal.SIGTERM)
                killed.append(int(pid))
            except OSError:
                pass
    return killed


_wake = threading.Event()


def wake() -> None:
    _wake.set()


def _loop(node: str, port: int, log: Callable[[str], None]) -> None:
    global _proc
    delay = 5.0
    while True:
        url = relay_url()
        if not url:
            _wake.wait(timeout=30)
            _wake.clear()
            continue
        gone = reap_orphans(port)
        if gone:
            log(f"Subway Tunnel: stopped {len(gone)} orphaned connector(s): {gone}")
        if other_connector_running(port):
            _wake.wait(timeout=30)
            _wake.clear()
            continue
        os.makedirs(_tunnel_dir(), mode=0o700, exist_ok=True)
        began = time.time()
        try:
            with open(os.path.join(_tunnel_dir(), "connector.log"), "ab") as out:
                _proc = subprocess.Popen([node, SCRIPT, "--url", url, "--port", str(port), "--exit-with-parent"],
                                         stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                log(f"Subway Tunnel connector started (pid {_proc.pid})")
                code = _proc.wait()
        except OSError as e:
            code = f"error: {e}"
        log(f"Subway Tunnel connector exited ({code}); restarting")
        delay = 5.0 if time.time() - began > 60 else min(delay * 2, 60.0)
        _wake.wait(timeout=delay)
        _wake.clear()
