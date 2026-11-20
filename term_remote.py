"""Remote terminal execution for own-station peer links (plan/headless-station.md unit 1)."""
import os
import secrets
import signal
import subprocess
import threading
import time
from typing import Any, Dict, Optional

import audit

MAX_BUFFER = 256 * 1024
MAX_TIMEOUT = 600.0

_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}


def _kill_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run(cmd: str, cwd: Optional[str] = None, timeout: float = 60.0,
        peer_id: str = "", peer_name: str = "",
        audit_action: str = "peer.remote_exec",
        sandbox_policy: Any = None) -> Dict[str, Any]:
    """Audit and start a sub-worker shell command, returning {id}."""
    text = str(cmd or "")
    trunc = text[:200]
    pid = str(peer_id or "")
    pname = str(peer_name or pid or "")
    action = str(audit_action or "").strip() or "peer.remote_exec"
    if action not in ("peer.remote_exec", "peer.remote_test", "peer.term.run"):
        action = "peer.remote_exec"

    try:
        limit = min(max(float(timeout if timeout is not None else 60.0), 0.1), MAX_TIMEOUT)
    except (TypeError, ValueError):
        limit = 60.0

    work_cwd = str(cwd) if cwd and os.path.isdir(str(cwd)) else None
    try:
        audit.record(
            {"kind": "station", "id": pid, "peer_id": pid, "name": pname},
            action,
            {"peer_id": pid, "cmd": trunc, "cwd": work_cwd or ""},
            peer_station=pname or None,
        )
    except Exception:
        pass
    if os.name == "nt":
        args = ["cmd.exe", "/c", text]
        popen_kw: Dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    else:
        args = ["/bin/sh", "-c", text]
        popen_kw = {"start_new_session": True}

    env: Optional[Dict[str, str]] = None
    if sandbox_policy is not None:
        import sandbox
        args, env_overrides = sandbox.wrap_command(args, sandbox_policy, work_cwd or os.getcwd())
        env = sandbox.scrub_env(dict(os.environ))
        env.update(env_overrides)

    proc = subprocess.Popen(
        args,
        shell=False,
        cwd=work_cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **popen_kw,
    )

    jid = f"term_{secrets.token_hex(8)}"
    done_ev = threading.Event()
    job: Dict[str, Any] = {
        "id": jid,
        "peer_id": pid,
        "cmd": trunc,
        "proc": proc,
        "buf": bytearray(),
        "total": 0,
        "done": False,
        "exit": None,
        "started_at": time.time(),
        "timeout": limit,
        "event": done_ev,
    }
    with _lock:
        _jobs[jid] = job

    def _reader() -> None:
        stream = proc.stdout
        try:
            if stream is not None:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    with _lock:
                        b = job["buf"]
                        b.extend(chunk)
                        job["total"] += len(chunk)
                        if len(b) > MAX_BUFFER:
                            del b[:-MAX_BUFFER]
        except Exception:
            pass
        finally:
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
            try:
                rc = proc.wait(timeout=1.0)
            except Exception:
                _kill_proc(proc)
                try:
                    rc = proc.wait(timeout=1.0)
                except Exception:
                    rc = -9
            with _lock:
                if job["exit"] is None:
                    job["exit"] = rc
                job["done"] = True
            done_ev.set()

    def _watchdog() -> None:
        if not done_ev.wait(timeout=limit):
            _kill_proc(proc)
            with _lock:
                if job["exit"] is None:
                    job["exit"] = -9
                job["done"] = True
            done_ev.set()

    threading.Thread(target=_reader, daemon=True).start()
    threading.Thread(target=_watchdog, daemon=True).start()
    done_ev.wait(timeout=0.05)
    return {"id": jid}


def read(job_id: str, offset: int = 0, peer_id: str = "") -> Dict[str, Any]:
    """Read buffered output from offset; returns {data, offset, done, exit}."""
    with _lock:
        job = _jobs.get(str(job_id or ""))
    if not job or (peer_id and job.get("peer_id") and job["peer_id"] != peer_id):
        return {"error": "unknown terminal id", "data": "", "offset": 0, "done": True, "exit": None}

    if not job["done"]:
        job["event"].wait(timeout=0.05)

    with _lock:
        buf: bytearray = job["buf"]
        total: int = job["total"]
        base = total - len(buf)
        req_off = max(0, int(offset or 0))
        if req_off < base:
            req_off = base
        rel = min(len(buf), max(0, req_off - base))
        raw = bytes(buf[rel:])
        new_off = base + len(buf)
        done = bool(job["done"])
        code = job["exit"]

    return {
        "data": raw.decode("utf-8", errors="replace"),
        "offset": new_off,
        "done": done,
        "exit": code,
    }


def kill_all_for(peer_id: str) -> int:
    """Kill all running sub-workers started by peer_id."""
    pid = str(peer_id or "")
    if not pid:
        return 0
    with _lock:
        targets = [j for j in _jobs.values() if j.get("peer_id") == pid and not j.get("done")]
    killed = 0
    for job in targets:
        proc = job.get("proc")
        if proc is not None:
            _kill_proc(proc)
            try:
                proc.wait(timeout=2.0)
            except Exception:
                pass
        with _lock:
            if job["exit"] is None:
                job["exit"] = proc.returncode if (proc is not None and proc.returncode is not None) else -9
            job["done"] = True
        job["event"].set()
        killed += 1
    return killed


def station_term(station: str, cmd: str, cwd: str = "", timeout: float = 60.0) -> Dict[str, Any]:
    """Run a command on an 'own' linked station through its peer terminal API.

    Starts the job with POST /api/peer/term/run, then polls
    GET /api/peer/term/read?id=...&offset=... until ``done`` is true. Returns
    ``{"ok", "output", "exit", "id"}``. Transport/permission errors (for
    example a 403 on a non-owned station) return ``ok=False`` with a readable
    ``output`` message instead of raising.
    """
    import urllib.parse

    import peers

    result: Dict[str, Any] = {"ok": False, "output": "", "exit": None, "id": ""}

    def _fail(message: str) -> Dict[str, Any]:
        result["output"] = message
        return result

    def _err_text(res: Any) -> str:
        if not isinstance(res, dict):
            return ""
        for key in ("error", "detail", "msg", "message", "reason"):
            if res.get(key):
                return str(res[key])
        return ""

    try:
        start = peers.call(station, "POST", "/api/peer/term/run", {
            "cmd": cmd, "cwd": cwd, "timeout": timeout,
        })
    except Exception as exc:
        code = getattr(exc, "status", None)
        suffix = f" (status {code})" if code else ""
        return _fail(f"terminal request failed{suffix}: {exc}")

    if not isinstance(start, dict):
        return _fail("terminal request failed: invalid response")

    status = start.get("status")
    if start.get("ok") is False or (isinstance(status, int) and status >= 400):
        return _fail(f"terminal request failed (status {status}): {_err_text(start) or 'forbidden'}")

    jid = start.get("id") or start.get("job_id")
    if not jid:
        return _fail("terminal request failed: missing job id")
    jid = str(jid)
    result["id"] = jid

    output: list = []
    offset = 0
    try:
        budget = float(timeout) if timeout else 60.0
    except (TypeError, ValueError):
        budget = 60.0
    deadline = time.monotonic() + max(1.0, budget) + 5.0

    while True:
        try:
            chunk = peers.call(
                station, "GET",
                "/api/peer/term/read?id=%s&offset=%d" % (urllib.parse.quote(jid), offset),
            )
        except Exception as exc:
            code = getattr(exc, "status", None)
            suffix = f" (status {code})" if code else ""
            tail = f"[terminal read failed{suffix}: {exc}]"
            result["output"] = "".join(output) + ("\n" if output else "") + tail
            return result

        if not isinstance(chunk, dict):
            result["output"] = "".join(output) + "\n[terminal read failed: invalid response]"
            return result

        cstatus = chunk.get("status")
        if chunk.get("ok") is False or (isinstance(cstatus, int) and cstatus >= 400):
            tail = f"[terminal read failed (status {cstatus}): {_err_text(chunk) or 'forbidden'}]"
            result["output"] = "".join(output) + ("\n" if output else "") + tail
            return result

        data = chunk.get("data", "")
        if data:
            output.append(str(data))
        try:
            new_off = int(chunk.get("offset", offset))
        except (TypeError, ValueError):
            new_off = offset
        if new_off > offset:
            offset = new_off

        if chunk.get("done") is True or chunk.get("done") == 1:
            result["ok"] = True
            result["output"] = "".join(output)
            exit_code = chunk.get("exit")
            if exit_code is None:
                exit_code = chunk.get("exit_code")
            result["exit"] = exit_code
            return result

        if time.monotonic() >= deadline:
            result["output"] = "".join(output) + "\n[terminal timed out]"
            return result


def audited_count(peer_id: str = "") -> int:
    """Count audited remote-terminal commands, optionally for one peer.

    Reads the shared audit log via :func:`audit.recent` and counts entries for
    remote exec/test/terminal actions. ``peer_id`` narrows the count to a
    single linked station. Returns 0 when auditing is unavailable.
    """
    try:
        entries = audit.recent(limit=10000) or []
    except Exception:
        return 0

    pid = str(peer_id or "").strip()
    id_keys = ("peer_id", "peer", "station", "station_id")

    def peer_values(entry: Dict[str, Any]) -> list:
        values = [str(entry[k]) for k in id_keys if entry.get(k)]
        for holder in ("target", "actor", "who"):
            section = entry.get(holder)
            if isinstance(section, dict):
                for key in id_keys + ("id",):
                    if section.get(key):
                        values.append(str(section[key]))
        return values

    count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        action = str(entry.get("action", "")).lower()
        if not any(key in action for key in ("remote_exec", "remote_test", "term")):
            continue
        if pid and pid not in peer_values(entry):
            continue
        count += 1
    return count
