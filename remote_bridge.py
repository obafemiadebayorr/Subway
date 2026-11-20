"""Client bridge for Personal Security (RTB-2): remote VFS, terminal, and test tools."""

import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple

import audit
import peers

MAX_OUTPUT = 32768
DEFAULT_TIMEOUT = 60
DEFAULT_TEST_TIMEOUT = 120


def _actor_id() -> str:
    try:
        import server as _s
        return _s._self_id()
    except Exception:
        return "mcp"


def _audit_local(action: str, peer: Dict[str, Any], target: Dict[str, Any], ok: bool = True) -> None:
    """Record a local audit entry for a remote bridge action without raising."""
    try:
        audit.record(
            {"kind": "agent", "id": _actor_id(), "name": "mcp"},
            action,
            target,
            ok=ok,
            peer_station=peers.slug(peer),
        )
    except Exception:
        pass


def _require_own_station(station: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Resolve station by slug and enforce relation == 'own'."""
    name = str(station or "").strip().lstrip("@")
    if not name:
        return (None, "Error: station name is required.")
    peer = peers.by_slug(name)
    if not peer:
        return (None, f"Error: no linked station called {name}.")
    relation = str(peer.get("relation") or "partner")
    if relation != "own":
        slug = peers.slug(peer)
        _audit_local(
            "peer.remote_refused",
            peer,
            {"station": slug, "relation": relation},
            ok=False,
        )
        return (
            None,
            f"[REFUSED] Station '@{slug}' is not an own station link (relation={peer.get('relation')}).",
        )
    return (peer, None)


def _format_call_error(res: Dict[str, Any], fallback: str = "request failed") -> str:
    """Extract a clean error message from a peers.call response."""
    status = res.get("status")
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    err = data.get("error") or res.get("error")
    if err:
        return f"Error ({status}): {err}" if status else f"Error: {err}"
    return f"Error ({status}): {fallback}" if status else f"Error: {fallback}"


def _clip_output(text: str, limit: int = MAX_OUTPUT, marker: str = "\n...[truncated]") -> str:
    """Bound text to at most `limit` characters, appending a truncation marker if clipped."""
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(marker))
    return text[:keep] + marker


def remote_read(station: str, path: str, start_line: int = 1, end_line: int = 0) -> str:
    """Read a file on an 'own' linked station via /api/peer/vfs_read."""
    peer, err = _require_own_station(station)
    if err or not peer:
        return err or "Error: invalid station."
    if not str(path or "").strip():
        return "Error: path is required."
    params: Dict[str, Any] = {
        "path": path,
        "start_line": max(1, int(start_line or 1)),
    }
    if end_line and int(end_line) > 0:
        params["end_line"] = int(end_line)
    res = peers.call(peer["peer_id"], "GET", f"/api/peer/vfs_read?{urllib.parse.urlencode(params)}")
    status = res.get("status")
    ok = status == 200
    _audit_local("peer.remote_read", peer, {"path": path, "status": status}, ok=ok)
    if not ok:
        return _format_call_error(res, "remote read failed")
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    if "lines" in data:
        lines_str = str(data.get("lines") or "")
    elif "start_line" in data or "end_line" in data:
        lines_str = f"{data.get('start_line', '')}-{data.get('end_line', '')}"
    else:
        lines_str = ""
    header = (
        f"path: {data.get('path', path)} | "
        f"sha256: {data.get('sha256', '')} | "
        f"lines: {lines_str} | "
        f"total_lines: {data.get('total_lines', '')} | "
        f"truncated: {bool(data.get('truncated', False))}"
    )
    content = str(data.get("content") or "")
    combined = f"{header}\n{content}" if content else header
    return _clip_output(combined, MAX_OUTPUT, "\n...[truncated]")


def remote_write(station: str, path: str, content: str, base_sha: Optional[str] = None) -> str:
    """Write a file on an 'own' linked station via /api/peer/vfs_write."""
    peer, err = _require_own_station(station)
    if err or not peer:
        return err or "Error: invalid station."
    if not str(path or "").strip():
        return "Error: path is required."
    payload: Dict[str, Any] = {"path": path, "content": content}
    if base_sha is not None:
        payload["base_sha"] = base_sha
    res = peers.call(peer["peer_id"], "POST", "/api/peer/vfs_write", payload)
    status = res.get("status")
    ok = status == 200
    _audit_local("peer.remote_write", peer, {"path": path, "status": status}, ok=ok)
    if not ok:
        return _format_call_error(res, "remote write failed")
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    return (
        f"Wrote {data.get('path', path)} "
        f"(sha256={data.get('sha256', '')}, size={data.get('size', len(content))})"
    )


def _run_remote_term(station: str, cmd: str, cwd: str, timeout: int, kind: str) -> str:
    """Start and poll a remote terminal command on an 'own' linked station."""
    peer, err = _require_own_station(station)
    if err or not peer:
        return err or "Error: invalid station."
    if not str(cmd or "").strip():
        return "Error: cmd must be non-empty."
    limit = max(1, min(600, int(timeout or DEFAULT_TIMEOUT)))
    action = "peer.remote_test" if kind == "test" else "peer.remote_exec"

    start_res = peers.call(
        peer["peer_id"],
        "POST",
        "/api/peer/term/run",
        {"cmd": cmd, "cwd": cwd, "timeout": limit, "kind": kind},
    )
    if start_res.get("status") != 200:
        _audit_local(action, peer, {"cmd": cmd, "cwd": cwd, "status": start_res.get("status")}, ok=False)
        return _format_call_error(start_res, "failed to start remote command")

    start_data = start_res.get("data") if isinstance(start_res.get("data"), dict) else {}
    jid = start_data.get("id")
    if not jid:
        _audit_local(action, peer, {"cmd": cmd, "cwd": cwd, "error": "missing job id"}, ok=False)
        return "Error: remote station did not return a job id."

    offset = 0
    chunks = []
    acc_len = 0
    truncated = False
    done = False
    exit_code: Optional[int] = None
    deadline = time.monotonic() + limit + 5

    while time.monotonic() < deadline:
        poll_res = peers.call(
            peer["peer_id"],
            "GET",
            f"/api/peer/term/read?id={urllib.parse.quote(str(jid))}&offset={offset}",
        )
        if poll_res.get("status") != 200:
            _audit_local(action, peer, {"cmd": cmd, "id": jid, "status": poll_res.get("status")}, ok=False)
            return _format_call_error(poll_res, "failed to read remote command output")
        pdata = poll_res.get("data") if isinstance(poll_res.get("data"), dict) else {}
        if "output" in pdata:
            chunk = str(pdata.get("output") or "")
        elif "chunk" in pdata:
            chunk = str(pdata.get("chunk") or "")
        else:
            chunk = str(pdata.get("data") or "")

        if "next_offset" in pdata:
            offset = int(pdata["next_offset"])
        elif "offset" in pdata:
            offset = int(pdata["offset"])
        else:
            offset += len(chunk)

        if chunk and not truncated:
            remaining = MAX_OUTPUT - acc_len
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                acc_len = MAX_OUTPUT
                truncated = True
            else:
                chunks.append(chunk)
                acc_len += len(chunk)

        if pdata.get("done"):
            done = True
            exit_code = pdata.get("exit") if "exit" in pdata else pdata.get("exit_code")
            break
        time.sleep(0.1)

    output = "".join(chunks)
    if truncated:
        marker = "\n...[truncated]"
        keep = max(0, MAX_OUTPUT - len(marker))
        output = output[:keep] + marker

    if not done:
        _audit_local(action, peer, {"cmd": cmd, "id": jid, "timed_out": True}, ok=False)
        return f"Error: remote command timed out waiting for completion.\n{output}".rstrip()

    _audit_local(action, peer, {"cmd": cmd, "id": jid, "exit_code": exit_code}, ok=(exit_code == 0 or exit_code is None))
    header = f"exit_code: {exit_code}"
    return f"{header}\n{output}" if output else header


def remote_exec(station: str, cmd: str, cwd: str = "", timeout: int = DEFAULT_TIMEOUT) -> str:
    """Execute a shell command on an 'own' linked station."""
    return _run_remote_term(station, cmd, cwd, timeout, kind="exec")


def remote_test(station: str, cmd: str = "pytest -q", cwd: str = "", timeout: int = DEFAULT_TEST_TIMEOUT) -> str:
    """Run a test command on an 'own' linked station."""
    return _run_remote_term(station, cmd, cwd, timeout, kind="test")
