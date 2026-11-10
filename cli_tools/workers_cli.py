from __future__ import annotations

import time

import server as _s
_WATCH_REPORTED: dict[str, set[str]] = {}



def _format_ended_worker(w: dict) -> str:
    wid = w.get("id", "")
    role = w.get("role", "")
    status = w.get("status", "")
    exit_code = w.get("exit_code")

    v = w.get("verdict")
    v_str = v.strip().upper() if isinstance(v, str) else ""
    verdict_str = f" | verdict: {v_str}" if v_str in ("APPROVE", "REJECT") else ""

    report_text = w.get("report") or ""
    if isinstance(report_text, str) and report_text:
        # Strip standing rules if present
        if "SUBWAY STANDING RULES" in report_text:
            report_text = report_text.split("SUBWAY STANDING RULES")[0].rstrip()
        # Single-line snippet of first 400 chars, no raw line breaks
        snippet = " ".join(report_text[:400].split())
        report_str = f" | report: {snippet}"
    else:
        report_str = ""

    return f"Worker `{wid}` ({role}) ended: {status}, exit {exit_code}{verdict_str}{report_str}"


def t_watch_workers(wait_seconds: int = 50) -> str:
    """Wait for something that needs attention: a permission request, or a worker finishing."""
    caller_id = _s._self_id() if hasattr(_s, "_self_id") else ""
    reported = _WATCH_REPORTED.setdefault(caller_id, set())

    for w in _s._all_workers():
        if w.get("status") == "pending_approval" and not w.get("approved"):
            created = float(w.get("created_ts") or 0)
            if created and time.time() - created < 20:
                continue  # Just queued: give it the same grace as notify_if_still_waiting.
            _s.notify_desktop_event(f"worker_waiting|{w['id']}", "Subway",
                                 f"Worker {w['id']} waiting on your approval")

    deadline = time.time() + max(1, min(int(wait_seconds), 55))
    while True:
        pending = _s._pending_permissions()
        if pending:
            urgent_p = [p for p in pending if (_s._load_worker(p["worker_id"]) or {}).get("priority") == "urgent"]
            if urgent_p:
                running = [w["id"] for w in _s._all_workers() if w["id"] != urgent_p[0]["worker_id"] and w.get("status") in _s.ACTIVE_WORKER_STATES]
                extra = f"\nStill running: {', '.join(running)}." if running else ""
                return f"{_s.permission_text(pending)}{extra}"
            return _s.permission_text(pending)

        all_w = _s._all_workers()
        newly_ended = [
            w for w in all_w
            if w.get("status") not in _s.ACTIVE_WORKER_STATES
            and w.get("id")
            and w["id"] not in reported
        ]

        if newly_ended:
            lines = []
            for w in newly_ended:
                reported.add(w["id"])
                lines.append(_format_ended_worker(w))
            return "\n".join(lines)

        active = [w for w in all_w if w.get("status") in _s.ACTIVE_WORKER_STATES]
        if not active:
            return "No active workers."

        if time.time() >= deadline:
            break

        time.sleep(1.0)
    return "Nothing new. Workers are still running."


def t_await_worker(worker_id: str = "", timeout_seconds: int = 600) -> str:
    """Block until a worker finishes, then return its own report.

    The native subagent shape: one call that returns the result. Spawning plus waiting
    for a later wake is the fallback for clients that cannot hold a call open.
    """
    wid = str(worker_id or "").strip()
    if not _s.WORKER_ID_RE.match(wid):
        return f"Invalid worker ID `{wid}`. Expected worker_<6 hex>."
    meta = _s.wait_for_worker(wid, timeout_seconds=timeout_seconds)
    status = str(meta.get("status") or "unknown")
    report = str(meta.get("report") or "").strip()
    lines = [f"# Worker `{wid}`", f"Status: {status}"]
    if meta.get("exit_code") is not None:
        lines.append(f"Exit code: {meta['exit_code']}")
    if meta.get("reason"):
        lines.append(f"Reason: {meta['reason']}")
    if meta.get("wait_timed_out"):
        lines.append(f"Still running after {timeout_seconds}s. Call await_worker again, or worker_status.")
    lines.append("")
    lines.append(report if report else "No final report: the worker did not write one.")
    return "\n".join(lines)


def t_worker_status(worker_id: str = "", track: str = "", all: bool = False) -> str:
    """Check the execution status and output tail of worker subagents."""
    return _s.poll_guard_check("worker_status", worker_id or track, _t_worker_status_impl(worker_id, track, all=all))


def _t_worker_status_impl(worker_id: str = "", track: str = "", all: bool = False) -> str:
    if worker_id:
        match = [r for r in _s._all_workers(include_archive=True) if r.get("id") == worker_id or worker_id in r.get("id", "")]
        if match:
            return _s._worker_detail(match[0])
        log_file = _s._log_file(worker_id) if _s.WORKER_ID_RE.match(worker_id) else ""
        if log_file and __import__('os').path.exists(log_file):
            # Try to detect if it's a pi log
            tail = ""
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                # Heuristic: if it's mostly JSONL with message_end events, treat as pi
                lines = content.splitlines()
                is_pi = False
                for l in lines[:50]:  # check start
                    if '"type":"message_end"' in l or '"message_end"' in l:
                        is_pi = True
                        break
                if is_pi:
                    snippets = []
                    for line in reversed(lines):
                        line_s = line.strip()
                        if not line_s:
                            continue
                        if line_s.startswith("{"):
                            try:
                                import json as _json
                                ev = _json.loads(line_s)
                            except Exception:
                                continue
                            if ev.get("type") == "message_end":
                                msg = ev.get("message") or {}
                                if msg.get("role") == "assistant":
                                    text_parts = []
                                    c = msg.get("content")
                                    if isinstance(c, str):
                                        text_parts.append(c)
                                    elif isinstance(c, list):
                                        for item in c:
                                            if isinstance(item, dict):
                                                if item.get("type") == "text" and item.get("text"):
                                                    text_parts.append(str(item.get("text")))
                                                elif item.get("text"):
                                                    text_parts.append(str(item.get("text")))
                                    if text_parts:
                                        t = "\n".join([p for p in text_parts if p]).strip()
                                        if t:
                                            snippets.append(t)
                                            if len(snippets) >= 5:
                                                break
                                            continue
                            if ev.get("type") in ("tool_call", "tool_call_end") or ev.get("tool"):
                                tool_name = ev.get("tool") or ev.get("name") or ev.get("toolName")
                                if tool_name:
                                    snippets.append(f"[tool] {tool_name}")
                                    if len(snippets) >= 5:
                                        break
                                    continue
                        else:
                            snippets.append(line_s)
                            if len(snippets) >= 5:
                                break
                    if snippets:
                        tail = "\n\n".join(reversed(snippets))
                if not tail:
                    tail = _s._log_tail(log_file)
            except Exception:
                tail = _s._log_tail(log_file)
            if tail and len(tail) > 4000:
                tail = tail[-4000:]
            if tail:
                return f"# Worker `{worker_id}`\nMetadata was deleted, but its log survives:\n```text\n{tail}\n```"
        return f"Worker `{worker_id}` not found."
    return _s.worker_listing(track=track, all=all)
