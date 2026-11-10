"""Wake-on-demand: knowing whether this Mac can be woken, and saying exactly how.

Item 46's done-when needs the laptop to do work while its lid is closed. The pieces that make
that possible are macOS settings, not code: `womp` (wake for network access), `powernap`, and
the absence of a hard sleep that ignores packets. This module reads them, reports them, and
hands back the exact commands to change them — it never runs sudo silently.

One honest limitation, recorded here because it decides the design: a phone on 5G cannot send
a Wake-on-LAN magic packet to a Mac on your home LAN. What it can do is keep knocking on the
station's port; with `womp` on, macOS answers an inbound connection and wakes the machine.
So the client's wake strategy is "retry with backoff", not "send a packet".
"""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
from typing import Any, Callable, Dict, List, Optional

Runner = Callable[[List[str]], str]


def _run(cmd: List[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_pmset(text: str) -> Dict[str, Any]:
    """Parse `pmset -g` output into the handful of keys this feature cares about."""
    wanted = {"womp", "powernap", "sleep", "standbysleep", "hibernatemode", "disksleep", "ttyskeepawake"}
    out: Dict[str, Any] = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\s*([a-z]+)\s+(.+?)\s*$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if key not in wanted:
            continue
        first = value.split()[0].rstrip("()")
        try:
            out[key] = int(first)
        except ValueError:
            out[key] = first
    return out


def tailscale_present(runner: Runner = _run) -> Dict[str, Any]:
    """Tailscale is optional; if it is here, report whether it is up without parsing secrets."""
    app = "/Applications/Tailscale.app"
    import os

    cli = shutil.which("tailscale") or (os.path.join(app, "Contents/MacOS/Tailscale") if os.path.exists(app) else "")
    if not cli:
        return {"installed": False, "cli": "", "status": ""}
    status = runner([cli, "status", "--peers=false"]) if cli else ""
    return {"installed": True, "cli": cli, "status": (status or "").splitlines()[0] if status else ""}


def status(runner: Runner = _run) -> Dict[str, Any]:
    if platform.system() != "Darwin":
        return {"supported": False, "reason": "Wake for network access is a macOS feature", "settings": {}}
    settings = parse_pmset(runner(["pmset", "-g"]))
    womp = bool(settings.get("womp"))
    powernap = bool(settings.get("powernap"))
    awake = {"womp": womp, "powernap": powernap}
    return {
        "supported": True,
        "settings": settings,
        "can_wake": womp,
        "power_nap": powernap,
        # Both must hold for a closed-lid Mac to answer an inbound connection.
        "ready": womp and powernap,
        "fix": [] if (womp and powernap) else [
            "sudo pmset -a womp 1",
            "sudo pmset -a powernap 1",
        ],
        "tailscale": tailscale_present(runner),
        "note": "The phone retries the station port with backoff; womp is what turns that knock into a wake.",
    }


def human_status(info: Dict[str, Any]) -> str:
    if not info.get("supported"):
        return f"Wake-on-demand: not supported here ({info.get('reason')})"
    s = info.get("settings", {})
    lines = [
        f"Wake-on-demand: {'ready' if info.get('ready') else 'not ready'}",
        f"  womp      {'on' if s.get('womp') else 'off'}   (wake for network access)",
        f"  powernap  {'on' if s.get('powernap') else 'off'}   (maintenance while asleep)",
        f"  sleep     {s.get('sleep', '?')} min   hibernate {s.get('hibernatemode', '?')}",
    ]
    ts = info.get("tailscale") or {}
    lines.append(f"  tailscale {'installed' if ts.get('installed') else 'not installed'}")
    if info.get("fix"):
        lines.append("  To enable:")
        lines += [f"    {c}" for c in info["fix"]]
    lines.append(f"  {info.get('note', '')}")
    return "\n".join(lines)
