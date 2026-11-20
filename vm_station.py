"""Unified VM manager for Subway stations: lima, utm, qemu, devcontainer.

Command builders are pure; runtime operations take an injectable `runner`
(default subprocess.run) so tests never boot a VM. Tokens are never logged.
"""
from __future__ import annotations

import json
import re
import secrets
import shlex
import subprocess
from typing import Any, Callable, Dict, List, Optional

import peers

BACKENDS = ("lima", "utm", "qemu", "devcontainer")
RELATIONS = ("own", "friend", "peer")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class VMError(Exception):
    pass


def _backend(backend: Optional[str]) -> str:
    b = (backend or "").strip().lower()
    if b == "docker":
        b = "devcontainer"
    if b not in BACKENDS:
        raise VMError(f"unknown backend {backend!r}; expected one of {', '.join(BACKENDS)}")
    return b


def _name(name: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise VMError(f"invalid VM name {name!r}")
    return name


def build_list_cmd(backend: str) -> List[str]:
    b = _backend(backend)
    if b == "lima":
        return ["limactl", "list", "--json"]
    if b == "utm":
        return ["utmctl", "list"]
    if b == "qemu":
        return ["pgrep", "-af", "qemu-system"]
    return ["docker", "ps", "-a", "--format", "json"]


def build_start_cmd(name: str, backend: str) -> List[str]:
    b, n = _backend(backend), _name(name)
    if b == "lima":
        # --tty=false: without it limactl can block on an interactive prompt (first-boot
        # image selection/confirmation) and the HUD start button times out forever.
        return ["limactl", "start", "--tty=false", n]
    if b == "utm":
        return ["utmctl", "start", n]
    if b == "qemu":
        return ["qemu-system-x86_64", "-name", n, "-daemonize", "-display", "none"]
    return ["docker", "start", n]


def build_create_cmd(name: str, backend: str = "lima") -> List[str]:
    b, n = _backend(backend), _name(name)
    if b == "lima":
        return ["limactl", "start", f"--name={n}", "--tty=false", "template://default"]
    if b == "devcontainer":
        return ["docker", "run", "-d", "--name", n, "ubuntu:24.04", "sleep", "infinity"]
    if b == "utm":
        return ["utmctl", "start", n]
    return ["qemu-system-x86_64", "-name", n, "-daemonize"]


def build_stop_cmd(name: str, backend: str) -> List[str]:
    b, n = _backend(backend), _name(name)
    if b == "lima":
        return ["limactl", "stop", n]
    if b == "utm":
        return ["utmctl", "stop", n]
    if b == "qemu":
        return ["pkill", "-f", rf"qemu-system.* -name {re.escape(n)}( |$)"]
    return ["docker", "stop", n]


def build_exec_cmd(name: str, cmd: Any, backend: str) -> List[str]:
    b, n = _backend(backend), _name(name)
    if isinstance(cmd, (list, tuple)):
        if not cmd:
            raise VMError("empty command")
        line = " ".join(shlex.quote(str(c)) for c in cmd)
    else:
        line = str(cmd or "").strip()
    if not line:
        raise VMError("empty command")
    if b == "lima":
        return ["limactl", "shell", n, "--", "sh", "-c", line]
    if b == "utm":
        return ["utmctl", "exec", n, "--cmd", "sh", "-c", line]
    if b == "qemu":
        return ["ssh", n, line]
    return ["docker", "exec", n, "sh", "-c", line]


def build_link_cmd(name: str, backend: str, station_url: str, token: str,
                   relation: str = "own") -> List[str]:
    """Exec command that links the VM's station to `station_url` as a peer via tunnel."""
    if relation not in RELATIONS:
        raise VMError(f"invalid relation {relation!r}")
    if not station_url or not isinstance(station_url, str) or not station_url.startswith(("http://", "https://")):
        raise VMError("station_url must be an http(s) URL")
    if not token or not isinstance(token, str):
        raise VMError("token required")
    inner = ("SUBWAY_LINK_TOKEN=" + shlex.quote(token) + " subway link "
             + shlex.quote(station_url) + " --relation " + shlex.quote(relation) + " --tunnel")
    return build_exec_cmd(name, inner, backend)


def _run(cmd: List[str], runner: Optional[Callable]) -> Dict[str, Any]:
    run = runner or subprocess.run
    try:
        p = run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return {"ok": False, "code": 127, "stdout": "", "stderr": f"{cmd[0]} not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": 124, "stdout": "", "stderr": "timed out"}
    except OSError as e:
        return {"ok": False, "code": 1, "stdout": "", "stderr": str(e)}
    code = getattr(p, "returncode", 1)
    return {"ok": code == 0, "code": code,
            "stdout": getattr(p, "stdout", "") or "", "stderr": getattr(p, "stderr", "") or ""}


def _norm_status(s: str) -> str:
    s = (s or "").strip().lower()
    return "running" if s in ("running", "started", "up") or s.startswith("up") else (s or "stopped")


def _parse_lima(out: str) -> List[Dict[str, str]]:
    recs = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, list):
            items = d
        else:
            items = [d]
        for it in items:
            if isinstance(it, dict) and it.get("name"):
                recs.append({"name": str(it["name"]), "backend": "lima",
                             "status": _norm_status(str(it.get("status", "")))})
    return recs


def _parse_utm(out: str) -> List[Dict[str, str]]:
    recs = []
    for line in out.splitlines()[1:]:  # header: UUID Status Name
        parts = line.split(None, 2)
        if len(parts) == 3:
            recs.append({"name": parts[2].strip(), "backend": "utm", "status": _norm_status(parts[1])})
    return recs


def _parse_qemu(out: str) -> List[Dict[str, str]]:
    recs = []
    for line in out.splitlines():
        if "qemu-system" not in line:
            continue
        m = re.search(r"-name\s+(?:guest=)?([^\s,]+)", line)
        if m:
            recs.append({"name": m.group(1), "backend": "qemu", "status": "running"})
    return recs


def _parse_docker(out: str) -> List[Dict[str, str]]:
    recs = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        name = d.get("Names") or d.get("Name")
        if name:
            recs.append({"name": str(name).split(",")[0], "backend": "devcontainer",
                         "status": _norm_status(str(d.get("State") or d.get("Status") or ""))})
    return recs


_PARSERS = {"lima": _parse_lima, "utm": _parse_utm, "qemu": _parse_qemu, "devcontainer": _parse_docker}


def list_vms(backend: Optional[str] = None, runner: Optional[Callable] = None) -> List[Dict[str, str]]:
    """Structured VM records. With no backend, query all; missing CLIs are skipped."""
    if backend is not None:
        bs = [_backend(backend)]
    else:
        bs = list(BACKENDS)
    recs: List[Dict[str, str]] = []
    for b in bs:
        r = _run(build_list_cmd(b), runner)
        if not r["ok"]:
            if backend is not None and r["code"] != 1 or backend is not None and r["stderr"]:
                if not (b == "qemu" and r["code"] == 1 and not r["stderr"]):  # pgrep: no match
                    raise VMError(f"{b} list failed: {r['stderr'].strip() or r['code']}")
            continue
        recs.extend(_PARSERS[b](r["stdout"]))
    return recs


def _detail(r: Dict[str, Any]) -> str:
    """One-line detail from a _run result (stderr preferred, then code)."""
    return (r.get("stderr") or r.get("stdout") or "").strip().splitlines()[0][:160] if (r.get("stderr") or r.get("stdout")) else f"exit {r.get('code')}"


def _op(cmd: List[str], runner: Optional[Callable], what: str, check: bool = True) -> Dict[str, Any]:
    r = _run(cmd, runner)
    if check and not r["ok"]:
        raise VMError(f"{what} failed: {r['stderr'].strip() or r['code']}")
    return r


def start_vm(name: str, backend: str = "lima", runner: Optional[Callable] = None) -> Dict[str, Any]:
    return _op(build_start_cmd(name, backend), runner, f"start {name}")


def create_vm(name: str, backend: str = "lima", runner: Optional[Callable] = None) -> Dict[str, Any]:
    return _op(build_create_cmd(name, backend), runner, f"create {name}")


def stop_vm(name: str, backend: str = "lima", runner: Optional[Callable] = None) -> Dict[str, Any]:
    return _op(build_stop_cmd(name, backend), runner, f"stop {name}")


def build_bootstrap_vdisplay_cmd(name: str, backend: str = "lima") -> List[str]:
    inner = (
        "command -v Xvfb >/dev/null 2>&1 || (sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq "
        "&& sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xvfb xdotool scrot openbox); "
        "pgrep -f 'Xvfb :99' >/dev/null 2>&1 || (nohup Xvfb :99 -screen 0 1280x800x24 >/dev/null 2>&1 &)"
    )
    return build_exec_cmd(name, inner, backend)


def bootstrap_vdisplay_vm(name: str, backend: str = "lima", runner: Optional[Callable] = None) -> Dict[str, Any]:
    return _op(build_bootstrap_vdisplay_cmd(name, backend), runner, f"bootstrap_screen {name}", check=False)


def link_vm(
    name: str,
    backend: str = "lima",
    station_url: str = "http://127.0.0.1:7890",
    token: str = "",
    relation: str = "own",
    bootstrap_screen: bool = True,
    runner: Optional[Callable] = None,
) -> Dict[str, Any]:
    b, n = _backend(backend), _name(name)
    tok = token or secrets.token_urlsafe(24)
    peer = peers.add(
        name=n,
        station={"id": f"vm-{b}-{n}", "title": f"{n} ({b})", "os": "linux", "device_kind": "vm"},
        token=tok,
        device_id=f"vm:{b}:{n}",
        grants="personal",
        relation=relation if relation in ("own", "partner") else "own",
    )
    if bootstrap_screen:
        bootstrap_vdisplay_vm(n, b, runner=runner)
    r = _run(build_link_cmd(n, b, station_url, tok, relation if relation in RELATIONS else "own"), runner)
    guest_link_ok = bool(r.get("ok"))
    # All systems must be live: the link button reports the truth — a failed in-guest
    # link used to still return ok=True, leaving a peer record that looked linked but
    # had no live Subway inside the VM.
    return {
        "ok": guest_link_ok,
        "action": "link",
        "name": n,
        "backend": b,
        "peer_id": peer.get("peer_id", ""),
        "linked": guest_link_ok,
        "guest_link_ok": guest_link_ok,
        "detail": "" if guest_link_ok else _detail(r),
    }


def unlink_vm(name: str, backend: str = "lima") -> Dict[str, Any]:
    b, n = _backend(backend), _name(name)
    removed_ids: List[str] = []
    for p in list(peers.list_peers()):
        if p.get("device_id") == f"vm:{b}:{n}" or p.get("name") == n:
            pid = p.get("peer_id", "")
            if pid:
                peers.remove(pid)
                removed_ids.append(pid)
    return {"ok": True, "action": "unlink", "name": n, "backend": b, "linked": False, "removed": removed_ids}


def exec_vm(name: str, cmd: Any, backend: str = "lima", runner: Optional[Callable] = None) -> Dict[str, Any]:
    """Run a command in the VM; non-zero exit is returned (ok=False), not raised."""
    return _op(build_exec_cmd(name, cmd, backend), runner, f"exec in {name}", check=False)
