#!/usr/bin/env python3
"""Ship flow state machine: preflight → push → ci → merge → release → mirror → done.

State is persisted per lane in lanes.json under the "ship" key (via lanes.load/save):
{"step", "status", "steps": {name: {status, started, ended, detail, log_tail}},
 "pr", "run_id", "updated"}.

Outward steps (push, ci poller, merge, release, mirror) only ever run inside
advance() — nothing fires on its own. The ship_auto_advance setting (default off)
lets a passing step advance to the next one automatically, except release and
mirror, which always need a click.
"""

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone

import lanes

STEPS = ("preflight", "push", "ci", "merge", "release", "mirror")
_ADVANCE_STEPS = ("push", "ci", "merge", "release", "mirror")
_NEXT = {"preflight": "push", "push": "ci", "ci": "merge", "merge": "release", "release": "mirror", "mirror": "done"}
# Auto-advance never fires for these targets; they always need a click.
_AUTO_BLOCK = ("release", "mirror")
_NETWORK_MARKERS = (
    "can't assign requested address", "could not resolve host", "connection refused",
    "i/o timeout", "dial tcp", "no such host", "temporary failure", "network is unreachable",
    "connection reset", "tls handshake", "timeout awaiting response",
)
_LOG_LINE = re.compile(r"FAIL|ERROR|error|Error|F[0-9]{3}")
_CI_BACKOFF_START = 30
_CI_BACKOFF_MAX = 120
_LOG_TAIL_LINES = 40

_lock = threading.Lock()
_sleep = time.sleep  # module-level so tests can patch it


def _iso():
    return datetime.now(timezone.utc).isoformat()


def _idle():
    return {"step": "preflight", "status": "idle", "steps": {}, "pr": None, "run_id": None, "updated": _iso()}


def _set_step(ship, name, status, detail="", log_tail=""):
    steps = ship.setdefault("steps", {})
    cur = steps.get(name) if isinstance(steps.get(name), dict) else {}
    now = _iso()
    if status == "running":
        started, ended, log_tail = now, None, ""
    else:
        started = cur.get("started") or now
        ended = now
        if not log_tail:
            log_tail = cur.get("log_tail") or ""
    steps[name] = {
        "status": status,
        "started": started,
        "ended": ended,
        "detail": str(detail)[:2000],
        "log_tail": str(log_tail)[:20000],
    }


def _tail_lines(text, limit=_LOG_TAIL_LINES):
    lines = [ln.rstrip() for ln in str(text or "").splitlines() if ln.strip()]
    return "\n".join(lines[-limit:])


def _extract_failed_lines(text, limit=_LOG_TAIL_LINES):
    lines = [ln.rstrip() for ln in str(text or "").splitlines() if _LOG_LINE.search(ln)]
    return "\n".join(lines[:limit])


def state(project_dir, lane):
    """Return the lane's ship state (an idle default when nothing has run yet)."""
    data = lanes.load(project_dir)
    entry = next((l for l in data["lanes"] if l.get("name") == lane), None)
    if entry is None:
        raise ValueError(f"No lane '{lane}'.")
    ship = entry.get("ship")
    if not isinstance(ship, dict):
        return _idle()
    merged = _idle()
    merged.update(ship)
    merged["steps"] = dict(ship.get("steps") or {})
    return merged


def _persist(project_dir, lane, ship):
    ship["updated"] = _iso()
    with _lock:
        data = lanes.load(project_dir)
        entry = next((l for l in data["lanes"] if l.get("name") == lane), None)
        if entry is None:
            raise ValueError(f"No lane '{lane}'.")
        entry["ship"] = ship
        lanes.save(project_dir, data)
    try:
        import sse_stream

        sse_stream.publish("ship", {
            "project": project_dir,
            "lane": lane,
            "step": ship.get("step"),
            "status": ship.get("status"),
            "ship": ship,
        })
    except Exception:
        pass
    return ship


def _worktree_and_branch(project_dir, lane):
    data = lanes.load(project_dir)
    entry = next((l for l in data["lanes"] if l.get("name") == lane), None)
    if entry is None:
        raise ValueError(f"No lane '{lane}'.")
    if lane == "main" or entry.get("role") == "main":
        raise ValueError("Ship flow runs on a release lane, not 'main'.")
    wt = entry.get("worktree") or project_dir
    branch = entry.get("branch") or f"release/{lane}"
    return wt, branch


def _run(cmd, cwd, timeout=600):
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError as e:
        return 127, "", f"{cmd[0]} not found: {e}"
    except Exception as e:
        return 1, "", repr(e)


def _is_network_error(text):
    t = str(text or "").lower()
    return any(marker in t for marker in _NETWORK_MARKERS)


def _auto_enabled():
    try:
        import server as _s

        cfg = _s._load_json("config.json") or {}
        return bool(cfg.get("ship_auto_advance", False))
    except Exception:
        return False


def _maybe_auto_advance(project_dir, lane, just_passed):
    nxt = _NEXT.get(just_passed)
    if not nxt or nxt in _AUTO_BLOCK:
        return
    if not _auto_enabled():
        return
    try:
        advance(project_dir, lane, nxt)
    except Exception:
        # Best effort: the click path always remains available.
        pass


def start(project_dir, lane):
    """Reset the lane's ship state and run preflight in a background thread."""
    wt, _branch = _worktree_and_branch(project_dir, lane)
    current = state(project_dir, lane)
    steps = current.get("steps", {})
    if current.get("status") == "running" and any(
        isinstance(s, dict) and s.get("status") == "running" for s in steps.values()
    ):
        return current  # already running; don't double-launch
    ship = _idle()
    ship["status"] = "running"
    _set_step(ship, "preflight", "running", "preflight started")
    _persist(project_dir, lane, ship)
    thread = threading.Thread(target=_preflight_worker, args=(project_dir, lane), daemon=True)
    thread.start()
    return state(project_dir, lane)


def _preflight_worker(project_dir, lane):
    try:
        wt, _branch = _worktree_and_branch(project_dir, lane)
        import ship_preflight

        res = ship_preflight.run(wt, run_tests=True, timeout=900)
        ok = bool(res.get("ok"))
        failed = [c for c in res.get("checks", []) if not c.get("ok")]
        if ok:
            detail, log_tail = "preflight clean", ""
        else:
            detail = "; ".join(f"{c.get('id')}: {c.get('detail')}" for c in failed[:3])[:500]
            log_tail = "\n".join(f"{c.get('id')}: {c.get('detail')}" for c in failed[:_LOG_TAIL_LINES])
        ship = state(project_dir, lane)
        _set_step(ship, "preflight", "passed" if ok else "failed", detail, log_tail)
        if ok:
            ship["step"] = "push"
            ship["status"] = "needs_you"
        else:
            ship["step"] = "preflight"
            ship["status"] = "failed"
        _persist(project_dir, lane, ship)
        if ok:
            _maybe_auto_advance(project_dir, lane, "preflight")
    except Exception as e:
        try:
            ship = state(project_dir, lane)
            _set_step(ship, "preflight", "failed", repr(e))
            ship["step"] = "preflight"
            ship["status"] = "failed"
            _persist(project_dir, lane, ship)
        except Exception:
            pass


def _check_verdict(raw):
    s = str(raw or "").strip().upper()
    if s in ("SUCCESS", "PASS", "SKIPPED", "NEUTRAL", "EXPECTED"):
        return "pass"
    if s in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"):
        return "fail"
    return "pending"


def _run_failed_log(wt, run_id):
    rc, out, err = _run(["gh", "run", "view", str(run_id), "--log-failed"], cwd=wt, timeout=180)
    if rc != 0:
        return _tail_lines(err or out)
    return _extract_failed_lines(out)


def _failed_check_run_id(wt, branch, failed_checks):
    for check in failed_checks:
        link = str(check.get("link") or "") if isinstance(check, dict) else ""
        m = re.search(r"/runs/(\d+)", link)
        if m:
            return int(m.group(1))
    rc, out, _err = _run(["gh", "run", "list", "--branch", branch, "--limit", "1", "--json", "databaseId"],
                         cwd=wt, timeout=60)
    try:
        arr = json.loads(out) if out.strip() else []
        if arr:
            return int(arr[0].get("databaseId"))
    except Exception:
        pass
    return None


def _poll_ci(project_dir, lane, pr, branch, wt):
    delay = _CI_BACKOFF_START
    net_errors = 0
    while True:
        rc, out, err = _run(["gh", "pr", "checks", str(pr), "--json", "name,state,link"], cwd=wt, timeout=60)
        parsed = None
        try:
            parsed = json.loads(out) if out and out.strip() else None
        except Exception:
            parsed = None
        if rc != 0 and parsed is None:
            text = err or out or ""
            if _is_network_error(text):
                net_errors += 1
                if net_errors > 3:
                    ship = state(project_dir, lane)
                    _set_step(ship, "ci", "needs_you",
                              "gh unreachable after 3 retries; click CI to check again")
                    ship["status"] = "needs_you"
                    _persist(project_dir, lane, ship)
                    return
                _sleep(delay)
                continue
            ship = state(project_dir, lane)
            _set_step(ship, "ci", "failed", f"gh pr checks failed: {text.strip()[:300]}", _extract_failed_lines(text))
            ship["status"] = "failed"
            _persist(project_dir, lane, ship)
            return
        net_errors = 0
        checks = parsed if isinstance(parsed, list) else []
        verdicts = [_check_verdict(c.get("state")) for c in checks if isinstance(c, dict)]
        failed_checks = [c for c in checks
                         if isinstance(c, dict) and _check_verdict(c.get("state")) == "fail"]
        if failed_checks:
            ship = state(project_dir, lane)
            run_id = _failed_check_run_id(wt, branch, failed_checks)
            if run_id:
                ship["run_id"] = run_id
            names = ", ".join(str(c.get("name") or "?") for c in failed_checks[:3])
            log_tail = _run_failed_log(wt, run_id) if run_id else "no run id found for failed checks"
            _set_step(ship, "ci", "failed", f"{len(failed_checks)} check(s) failed: {names}", log_tail)
            ship["status"] = "failed"
            _persist(project_dir, lane, ship)
            return
        if not verdicts or any(v == "pending" for v in verdicts):
            _sleep(delay)
            delay = min(delay * 2, _CI_BACKOFF_MAX)
            continue
        ship = state(project_dir, lane)
        _set_step(ship, "ci", "passed", f"{len(verdicts)} check(s) green")
        ship["step"] = "merge"
        ship["status"] = "needs_you"
        _persist(project_dir, lane, ship)
        _maybe_auto_advance(project_dir, lane, "ci")
        return


def _poll_release(project_dir, lane, wt):
    delay = _CI_BACKOFF_START
    net_errors = 0
    while True:
        rc, out, err = _run(["gh", "run", "list", "--workflow", "release.yml", "--limit", "1",
                             "--json", "databaseId,status,conclusion"], cwd=wt, timeout=60)
        parsed = None
        try:
            parsed = json.loads(out) if out and out.strip() else None
        except Exception:
            parsed = None
        if rc != 0 and parsed is None:
            text = err or out or ""
            if _is_network_error(text):
                net_errors += 1
                if net_errors > 3:
                    ship = state(project_dir, lane)
                    _set_step(ship, "release", "needs_you",
                              "gh unreachable after 3 retries; click Release to check again")
                    ship["status"] = "needs_you"
                    _persist(project_dir, lane, ship)
                    return
                _sleep(delay)
                continue
            ship = state(project_dir, lane)
            _set_step(ship, "release", "failed", f"gh run list failed: {text.strip()[:300]}", _extract_failed_lines(text))
            ship["status"] = "failed"
            _persist(project_dir, lane, ship)
            return
        net_errors = 0
        arr = parsed if isinstance(parsed, list) else []
        run = arr[0] if arr and isinstance(arr[0], dict) else {}
        run_id = run.get("databaseId")
        st = str(run.get("status") or "").lower()
        conclusion = str(run.get("conclusion") or "").lower()
        if run_id:
            ship = state(project_dir, lane)
            if ship.get("run_id") != int(run_id):
                ship["run_id"] = int(run_id)
                _persist(project_dir, lane, ship)
        if st == "completed":
            ship = state(project_dir, lane)
            if conclusion == "success":
                _set_step(ship, "release", "passed", f"release run {run_id} succeeded")
                ship["step"] = "mirror"
                ship["status"] = "needs_you"  # mirror always needs a click
            else:
                log_tail = _run_failed_log(wt, run_id) if run_id else "no run id recorded"
                _set_step(ship, "release", "failed", f"release run {run_id} {conclusion or 'failed'}", log_tail)
                ship["status"] = "failed"
            _persist(project_dir, lane, ship)
            return  # never auto-advance out of release
        _sleep(delay)
        delay = min(delay * 2, _CI_BACKOFF_MAX)


def _advance_push(project_dir, lane, wt, branch, ship, confirm=False):
    _set_step(ship, "push", "running", f"git push origin {branch}")
    ship["step"] = "push"
    ship["status"] = "running"
    _persist(project_dir, lane, ship)

    rc, out, err = _run(["git", "-C", wt, "push", "origin", branch], cwd=wt, timeout=300)
    if rc != 0:
        text = (err or out or "").strip()
        ship = state(project_dir, lane)
        _set_step(ship, "push", "failed", f"git push failed (exit {rc})", _tail_lines(text))
        ship["status"] = "failed"
        return _persist(project_dir, lane, ship)

    rc, out, err = _run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"],
                        cwd=wt, timeout=60)
    pr = None
    if rc == 0:
        try:
            arr = json.loads(out) if out.strip() else []
            if arr:
                pr = int(arr[0].get("number"))
        except Exception:
            pr = None
    if pr is None:
        rc, out, err = _run(["gh", "pr", "create", "--base", "main", "--head", branch, "--fill"],
                            cwd=wt, timeout=120)
        if rc != 0:
            text = (err or out or "").strip()
            detail = ("gh not available; install the GitHub CLI (https://cli.github.com) and run `gh auth login`"
                      if rc == 127 else f"gh pr create failed (exit {rc})")
            ship = state(project_dir, lane)
            _set_step(ship, "push", "failed", detail, _tail_lines(text))
            ship["status"] = "failed"
            return _persist(project_dir, lane, ship)
        m = re.search(r"/pull/(\d+)", out or "") or re.search(r"(\d+)\s*$", (out or "").strip())
        if m:
            pr = int(m.group(1))
    ship = state(project_dir, lane)
    ship["pr"] = pr
    _set_step(ship, "push", "passed", f"pushed {branch}; PR #{pr}" if pr else f"pushed {branch}")
    ship["step"] = "ci"
    ship["status"] = "needs_you"
    _persist(project_dir, lane, ship)
    _maybe_auto_advance(project_dir, lane, "push")
    return state(project_dir, lane)


def _advance_ci(project_dir, lane, wt, branch, ship, confirm=False):
    pr = ship.get("pr")
    if not pr:
        _set_step(ship, "ci", "failed", "no PR recorded; run the push step first")
        ship["status"] = "failed"
        return _persist(project_dir, lane, ship)
    _set_step(ship, "ci", "running", f"polling gh pr checks for PR #{pr}")
    ship["step"] = "ci"
    ship["status"] = "running"
    _persist(project_dir, lane, ship)
    thread = threading.Thread(target=_poll_ci, args=(project_dir, lane, int(pr), branch, wt), daemon=True)
    thread.start()
    return state(project_dir, lane)


def _advance_merge(project_dir, lane, wt, branch, ship, confirm=False):
    pr = ship.get("pr")
    if not pr:
        _set_step(ship, "merge", "failed", "no PR recorded; run the push step first")
        ship["status"] = "failed"
        return _persist(project_dir, lane, ship)
    _set_step(ship, "merge", "running", f"gh pr merge {pr} --merge")
    ship["step"] = "merge"
    ship["status"] = "running"
    _persist(project_dir, lane, ship)

    rc, out, err = _run(["gh", "pr", "merge", str(pr), "--merge"], cwd=wt, timeout=180)
    ship = state(project_dir, lane)
    if rc != 0:
        text = (err or out or "").strip()
        detail = ("gh not available; install the GitHub CLI and run `gh auth login`"
                  if rc == 127 else f"gh pr merge failed (exit {rc})")
        _set_step(ship, "merge", "failed", detail, _tail_lines(text))
        ship["status"] = "failed"
        return _persist(project_dir, lane, ship)
    _set_step(ship, "merge", "passed", f"PR #{pr} merged")
    ship["step"] = "release"
    ship["status"] = "needs_you"  # release always needs a click
    _persist(project_dir, lane, ship)
    _maybe_auto_advance(project_dir, lane, "merge")
    return state(project_dir, lane)


def _advance_release(project_dir, lane, wt, branch, ship, confirm=False):
    _set_step(ship, "release", "running", "gh workflow run release.yml --ref main -f channel=production")
    ship["step"] = "release"
    ship["status"] = "running"
    _persist(project_dir, lane, ship)

    rc, out, err = _run(["gh", "workflow", "run", "release.yml", "--ref", "main", "-f", "channel=production"],
                        cwd=wt, timeout=120)
    if rc != 0:
        text = (err or out or "").strip()
        detail = ("gh not available; install the GitHub CLI and run `gh auth login`"
                  if rc == 127 else f"gh workflow run failed (exit {rc})")
        ship = state(project_dir, lane)
        _set_step(ship, "release", "failed", detail, _tail_lines(text))
        ship["status"] = "failed"
        return _persist(project_dir, lane, ship)
    thread = threading.Thread(target=_poll_release, args=(project_dir, lane, wt), daemon=True)
    thread.start()
    return state(project_dir, lane)


def _advance_mirror(project_dir, lane, wt, branch, ship, confirm=False):
    dry = not confirm
    cmd = ["python3", "scripts/publish_mirror.py"] + ([] if dry else ["--push"])
    _set_step(ship, "mirror", "running", "mirror dry run" if dry else "mirror publish --push")
    ship["step"] = "mirror"
    ship["status"] = "running"
    _persist(project_dir, lane, ship)

    rc, out, err = _run(cmd, cwd=wt, timeout=600)
    text = ((out or "") + ("\n" + err if err else "")).strip()
    ship = state(project_dir, lane)
    if rc != 0:
        _set_step(ship, "mirror", "failed",
                  f"mirror {'dry run' if dry else 'publish'} failed (exit {rc})", _tail_lines(text))
        ship["status"] = "failed"
        return _persist(project_dir, lane, ship)
    if dry:
        _set_step(ship, "mirror", "needs_you",
                  "dry run OK; call advance with confirm=true to publish the mirror", _tail_lines(out))
        ship["status"] = "needs_you"
        return _persist(project_dir, lane, ship)
    _set_step(ship, "mirror", "passed", "mirror published", _tail_lines(out))
    ship["step"] = "done"
    ship["status"] = "passed"
    return _persist(project_dir, lane, ship)


_HANDLERS = {
    "push": _advance_push,
    "ci": _advance_ci,
    "merge": _advance_merge,
    "release": _advance_release,
    "mirror": _advance_mirror,
}


def advance(project_dir, lane, step, confirm=False):
    """Execute ONE outward step. Outward actions never run unless advance() is called."""
    step = str(step or "").strip()
    if step == "preflight":
        raise ValueError("preflight runs via start(), not advance().")
    if step not in _HANDLERS:
        raise ValueError(f"Unknown step '{step}'. Expected one of: {', '.join(_ADVANCE_STEPS)}.")
    wt, branch = _worktree_and_branch(project_dir, lane)
    ship = state(project_dir, lane)
    return _HANDLERS[step](project_dir, lane, wt, branch, ship, confirm=bool(confirm))
