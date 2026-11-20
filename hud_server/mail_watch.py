"""Event-driven mailbox dispatcher: fires queued mail items when their condition is met.

No fixed-interval polling. The loop sleeps until notified by an event (spool/mail/usage) or until the
next time-based/slow-recheck deadline. Claims are taken under the mail state lock, so a second HUD
cannot double-fire.
"""
import contextlib
import threading
import time
from typing import Any, Callable, Dict, Optional

OPEN_STATES = ("queued", "waiting")
_lock = threading.Lock()
_started = False
_wake = threading.Event()

# Spool event types that can satisfy mail conditions: mail changes (cross-process), worker completion,
# and ledger/track messages. Derived from sse_stream.publish/broadcast_event call sites (workers.*,
# track/ledger/fastmail/message channels) plus the "mail" event emitted by postmail._append_mail_event.
WAKE_TYPES = {"mail", "worker", "worker_media", "track", "ledger", "fastmail", "message", "chat"}


def notify(reason: str = "") -> None:
    _wake.set()


def slow_check() -> float:
    try:
        from hud_server import settings_api
        sec = int(settings_api.value("mail_slow_check_seconds") or 0)
    except Exception:
        sec = 30
    if sec < 5:
        return 5.0
    if sec > 600:
        return 600.0
    return float(sec)


def has_open_items() -> bool:
    import postmail
    items = (postmail._load_mail_store() or {}).get("items", {}) or {}
    return any((it or {}).get("state") in OPEN_STATES or _awaiting(it) for it in items.values())


def _awaiting(item: Any) -> bool:
    return (item or {}).get("state") == "running" and isinstance((item or {}).get("awaiting_reply"), dict)


def tick(log: Callable[[str], None]) -> int:
    """One pass. Reads only when idle (evaluate_mail_items writes the store, so skip it with nothing open)."""
    import postmail
    if not has_open_items():
        return 0
    fired = postmail.evaluate_mail_items() or []
    if fired:
        log(f"mail: dispatched {len(fired)} item(s)")
    return len(fired)


def _condition(item: Dict[str, Any]) -> Dict[str, Any]:
    return (item or {}).get("condition") or {}


def next_wait(items: Any, now: float) -> Optional[float]:
    """Earliest deadline among open items, or None to sleep until notified."""
    deadline: Optional[float] = None
    for item in (items or []):
        item = item or {}
        if _awaiting(item):
            # Bounded re-checks for the chat's reply (backoff set by postmail, ends at the deadline).
            try:
                w = max(0.0, float(item["awaiting_reply"].get("next_check") or now) - now)
            except Exception:
                w = slow_check()
            deadline = w if deadline is None else min(deadline, w)
            continue
        if item.get("state") not in OPEN_STATES:
            continue
        if item.get("retry_after") or item.get("held_for_allowance"):
            # Condition already met; waiting on allowance. Timer to the known reset; usage events cover the rest.
            cands = []
            for raw in (item.get("retry_after"), (item.get("held_for_allowance") or {}).get("resets_at")):
                with contextlib.suppress(Exception):
                    if raw and float(raw) > now:
                        cands.append(float(raw) - now + 2.0)
            if cands:
                w = min(cands)
                deadline = w if deadline is None else min(deadline, w)
            continue
        cond = _condition(item)
        ctype = str(cond.get("type") or "")
        if ctype == "time":
            try:
                run_at = float(cond.get("run_at") or 0)
            except Exception:
                run_at = 0.0
            w = max(0.0, run_at - now)
        elif ctype == "github":
            # gh/MCP check on the item's own interval (GitHub has no push to a laptop without a public relay).
            try:
                w = max(30.0, float(cond.get("interval_s") or 60))
            except Exception:
                w = 60.0
        elif ctype in ("file_change", "chat_idle"):
            # No event source yet: slow re-check only while such an item exists.
            w = slow_check()
        elif ctype in ("allowance_return", "token_warning"):
            # Live quota changes arrive via notify("usage"); timers cover the known reset time and the expiry.
            if ctype == "token_warning" and cond.get("action_on_warning", "wait_for_reset") != "wait_for_reset":
                continue
            cands = []
            for raw in (cond.get("baseline_resets_at"),):
                try:
                    t = float(raw or 0)
                except Exception:
                    t = 0.0
                if t > now:
                    cands.append(t - now)
            try:
                max_wait = float(cond.get("max_wait_seconds") or (14 * 86400))
                created = float(item.get("created_at") or 0)
                if created:
                    cands.append(max(0.0, created + max_wait - now + 1.0))
            except Exception:
                pass
            if not cands:
                continue
            w = min(cands)
        else:
            # worker_done, ledger_message (and any other event-driven type)
            continue
        deadline = w if deadline is None else min(deadline, w)
    return deadline


def _on_spool(event_type: Any, data: Any = None) -> None:
    etype = str(event_type or "")
    if etype in WAKE_TYPES:
        notify(etype)


def _loop(log: Callable[[str], None]) -> None:
    import postmail
    try:
        rec = postmail.recover_interrupted_mail() or []
        if rec:
            log(f"mail: recovered {len(rec)} interrupted item(s)")
    except Exception as e:
        log(f"mail recover: {e}")
    while True:
        try:
            tick(log)
        except Exception as e:
            log(f"mail dispatch: {e}")
        try:
            items = (postmail._load_mail_store() or {}).get("items", {}) or {}
            w = next_wait(items.values(), time.time())
        except Exception as e:
            log(f"mail wait: {e}")
            w = slow_check()
        _wake.wait(timeout=w)
        _wake.clear()


def start(log: Callable[[str], None]) -> bool:
    global _started
    with _lock:
        if _started:
            return False
        _started = True
    try:
        import sse_stream
        sse_stream.add_spool_listener(_on_spool)
    except Exception:
        pass
    threading.Thread(target=_loop, args=(log,), daemon=True, name="subway-mail-watch").start()
    return True
