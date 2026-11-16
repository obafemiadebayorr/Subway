# composer_mail.py — Composer mailbox bridge for HUD scheduling
# Python 3.7 compatible.

from __future__ import annotations

import time
from typing import Any, Dict, List

import postmail
import wake_driver

CHAT_ID = "composer_mail"
POST_TO_MAILBOX = True

_VALID_KINDS = ("quota", "in1h", "at", "after")


def _clock_when(clock: Dict[str, Any]) -> str:
    kind = clock.get("kind")
    if kind == "quota":
        return "on quota"
    if kind == "in1h":
        return "within 1 hour"
    if kind == "at":
        return f"at {clock.get('at')}"
    if kind == "after":
        return f"after {clock.get('id')}"
    return ""


def _postmail_spec(clock: Dict[str, Any]):
    """Return (condition_type, condition) for postmail, or (None, None) if unsupported."""
    kind = clock.get("kind")
    if kind == "quota":
        # No matching postmail condition type (quota/reset); skip posting.
        return None, None
    if kind == "in1h":
        return "time", {"run_at": int((time.time() + 3600) * 1000)}
    if kind == "at":
        return "time", {"run_at": clock.get("at")}
    if kind == "after":
        return "worker_done", {"worker_id": clock.get("id")}
    return None, None


def queue_mail(steps, clock=None, thread_id: str = ""):
    # A single mail payload (a dict) is the Mailbox's own shape: create, or edit by mail_id.
    # It goes straight to postmail and returns (status, body), which composer_api passes through.
    if isinstance(steps, dict):
        payload = dict(steps)
        if thread_id and str(thread_id).strip() and "track" not in payload:
            payload["track"] = str(thread_id).strip()
        res = postmail.handle_composer_mail(payload)
        if isinstance(res, dict) and res.get("error"):
            return int(res.get("status", 400)), {"error": res.get("error")}
        return 200, res if isinstance(res, dict) else {"result": res}
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list")
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("each step must be a dict")
        prompt = step.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("each step must have a non-blank prompt")

    if clock is None:
        clock = {"kind": "quota"}
    if not isinstance(clock, dict):
        raise ValueError("clock must be a dict or None")
    kind = clock.get("kind")
    if kind not in _VALID_KINDS:
        raise ValueError(f"invalid clock kind: {kind!r}")
    if kind == "at" and "at" not in clock:
        raise ValueError("clock kind 'at' requires 'at'")
    if kind == "after" and "id" not in clock:
        raise ValueError("clock kind 'after' requires 'id'")

    thread = str(thread_id or "").strip() or CHAT_ID
    clock_norm = dict(clock)

    wake_driver.wake_spool_enqueue(
        thread,
        wake_driver.WakeEvent(
            kind="composer_mail",
            data={"steps": steps, "clock": clock_norm, "thread": thread},
        ),
    )

    mail_ids: List[str] = []
    failed: List[Dict[str, Any]] = []
    if POST_TO_MAILBOX:
        cond_type, condition = _postmail_spec(clock_norm)
        if cond_type is not None:
            for step in steps:
                try:
                    ok, res = postmail.create_mail_item(
                        role=step.get("role") or "composer",
                        prompt=step.get("prompt") or "",
                        condition_type=cond_type,
                        condition=dict(condition),
                        provider=step.get("provider") or "auto",
                        model=step.get("model") or "",
                        track=thread,
                        isolate=bool(step.get("isolate", True)) if "isolate" in step else True,
                        max_tokens=int(step.get("max_tokens") or 0),
                        max_seconds=int(step.get("max_seconds") or 0),
                        created_by={"surface": "hud", "launcher": "composer", "station_id": "hud"},
                    )
                    if ok and isinstance(res, dict) and res.get("id"):
                        mail_ids.append(res["id"])
                    else:
                        failed.append({"step": step.get("prompt"), "error": res})
                except Exception as e:
                    failed.append({"step": step.get("prompt"), "error": str(e)})

    result: Dict[str, Any] = {
        "result": f"Queued {len(steps)} worker(s), {_clock_when(clock_norm)}",
        "mail_ids": mail_ids,
    }
    if failed:
        result["failed"] = failed
    return result
