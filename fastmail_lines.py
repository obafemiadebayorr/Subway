import os
"""Mail-only fastmail routing: hop cap, wire text, queue results, deliver.

Per-provider deliverers, headless execution and send_to_chat live in
provider_lines.py; every moved symbol is re-exported here so existing callers
and tests importing from fastmail_lines keep working.
"""
import sys
import types
from typing import Any, Dict

import provider_lines
from provider_lines import *  # noqa: F401,F403
from provider_lines import (
    _DELIVERY_CAPABILITY,
    _HANDOFFS_FILE,
    _PendingRun,
    _agy_headless_settings,
    _build_headless_cmd,
    _continue_headless,
    _deliver_antigravity,
    _deliver_claude,
    _deliver_codex,
    _deliver_copilot,
    _deliver_cursor,
    _deliver_devin,
    _deliver_local,
    _deliver_no_channel,
    _deliver_opencode,
    _deliver_pi,
    _deliver_worker,
    _detected_chat_sources,
    _find_headless_bin,
    _get_chat_context_digest,
    _handoff_aliases,
    _headless_lock,
    _headless_meta,
    _headless_runs,
    _load_handoff_map,
    _opencode_cwd_pids,
    _opencode_live_pids,
    _opencode_model_ref,
    _opencode_session_directory,
    _record_headless_meta,
    _save_handoff_map,
    _s,
    _tee_wrap,
    CHAT_DELIVERERS,
    HEADLESS_CONTINUE,
    claude_headless_running,
    claude_waiter_alive,
    claude_window_closed,
    delivery_coverage,
    is_online,
    is_pending_launch,
    latest_run,
    live_from_run,
    loose_chat_dir,
    record_real_session,
    send_to_chat,
    _launch_handoff_session,
)

HOP_CAP = int(os.environ.get("SUBWAY_MAIL_MAX_HOPS", "3"))


def _wire_text(msg: Dict[str, Any]) -> str:
    mid = str(msg.get("id") or "")
    sender = str(msg.get("from") or msg.get("from_chat") or "unknown")
    body = str(msg.get("text") or "")
    if not mid:
        return body
    return (f"[fast mail {mid} from {sender}]\n{body}\n\n"
            f"Reply with reply_note(msg_id=\"{mid}\", text=...).")


def _queue_result(detail: str = "queued") -> Dict[str, Any]:
    return {"delivered": False, "method": "queued", "detail": detail}


def deliver(msg: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
    msg = msg or {}
    target = target or {}
    source = str(target.get("source") or target.get("provider") or msg.get("source") or "").lower()
    tid = str(target.get("chat_id") or target.get("id") or target.get("to") or msg.get("to") or "")
    text = _wire_text(msg)
    mode = str(msg.get("mode") or "fast").lower()
    hops = int(msg.get("hops") or 0)
    project_dir = str(target.get("project_dir") or target.get("project") or "")

    if hops >= HOP_CAP:
        return _queue_result(f"hop cap ({hops} >= {HOP_CAP})")
    if mode != "fast":
        return _queue_result("mode=normal")

    try:
        if source == "worker":
            ok, method = _deliver_worker(tid, text)
            return {"delivered": bool(ok), "method": method, "detail": "delivered" if ok else f"delivery failed via {method}"}
        deliverer = CHAT_DELIVERERS.get(source)
        if deliverer is None:
            # copilot/local/custom/unknown or anything not in chat delivery registry → queue only
            return _queue_result(f"source={source or 'unknown'}")
        if source == "antigravity":
            ok, method = deliverer(tid, text, project_dir)
        else:
            ok, method = deliverer(tid, text)
    except Exception as e:
        return {"delivered": False, "method": "error", "detail": str(e)}
    if not ok and source in HEADLESS_CONTINUE and _DELIVERY_CAPABILITY.get(source) == "headless":
        try:
            h_ok, h_method, _info = _continue_headless(source, tid, text, project_dir)
        except Exception as e:
            h_ok, h_method = False, f"launch failed: {e}"
        return {"delivered": bool(h_ok), "method": "headless", "detail": "continued headless" if h_ok else f"headless: {h_method}"}
    # For read-only sources with no input channel, queue rather than pretending delivery.
    if not ok and _DELIVERY_CAPABILITY.get(source) == "none":
        return _queue_result(f"source={source} has no input channel")
    return {"delivered": bool(ok), "method": method, "detail": "delivered" if ok else f"delivery failed via {method}"}


# provider_lines is standalone (it never imports this module), but tests and
# callers patch attributes on `fastmail_lines`. Route reads/writes of every
# provider symbol to provider_lines so those patches reach the functions that
# actually use them, while the four mail-only symbols stay local.
_PROVIDER_NAMES = frozenset(
    name for name in vars(provider_lines) if not name.startswith("__")
)


class _FastmailLinesModule(types.ModuleType):
    def __getattribute__(self, name: str) -> Any:
        if name in _PROVIDER_NAMES:
            return getattr(provider_lines, name)
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _PROVIDER_NAMES:
            setattr(provider_lines, name, value)
        else:
            object.__setattr__(self, name, value)


sys.modules[__name__].__class__ = _FastmailLinesModule
