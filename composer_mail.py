"""PKG-B3: post-mail queue for the Composer plan.

Queues a batch of spin_worker steps as a single WakeEvent on the
wake_driver spool, so the batch fires atomically.
"""

from typing import Any, Dict, List, Optional

import wake_driver

VALID_CLOCK_KINDS = {"quota", "in1h", "at", "after"}

CHAT_ID = "composer_mail"


def _validate_clock(clock: Optional[Dict[str, Any]]) -> None:
    if not isinstance(clock, dict) or not clock.get("kind"):
        raise ValueError("clock.kind is required")
    kind = clock["kind"]
    if kind not in VALID_CLOCK_KINDS:
        raise ValueError("unknown clock.kind: %s" % kind)
    if kind == "at" and not clock.get("at"):
        raise ValueError('clock.kind "at" requires "at" (epoch ms)')
    if kind == "after" and not clock.get("id"):
        raise ValueError('clock.kind "after" requires "id"')


def _validate_steps(steps: List[Dict[str, Any]]) -> None:
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list")
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError("step %d must be an object" % i)
        if not step.get("prompt"):
            raise ValueError("step %d is missing prompt" % i)
        if not step.get("provider"):
            raise ValueError("step %d is missing provider" % i)


def _when(clock: Dict[str, Any]) -> str:
    kind = clock["kind"]
    if kind == "quota":
        return "on quota"
    if kind == "in1h":
        return "within 1 hour"
    if kind == "at":
        return "at %s" % clock.get("at")
    return "after %s" % clock.get("id")


def queue_mail(steps: List[Dict[str, Any]], clock: Dict[str, Any], thread_id: str = "") -> Dict[str, Any]:
    """Validate and enqueue one WakeEvent carrying the whole step list.

    Returns {"result": "Queued N worker(s), <when>"}.
    """
    _validate_steps(steps)
    _validate_clock(clock)
    target = str(thread_id or "").strip() or CHAT_ID
    event = wake_driver.WakeEvent(
        kind="composer_mail",
        chat_id=target,
        data={"steps": steps, "clock": clock, "thread": target},
    )
    ok = wake_driver.wake_spool_enqueue(target, event)
    if not ok:
        raise ValueError("failed to enqueue mail event")
    return {"result": "Queued %d worker(s), %s" % (len(steps), _when(clock))}
