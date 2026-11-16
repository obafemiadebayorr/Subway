"""Fastmail offline resume — resume an offline recipient chat headless to get a real answer.

Opt-in via `mail.wake_offline` in config.json: off | ask (default) | on.
Budgets: `mail.offline_budget_tokens` (default 20000, clamp 1000..200000),
`mail.offline_budget_seconds` (default 120).
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any, Dict, Optional

import server as _s
import fastmail

_SOURCE_TO_SPAWN = {
    "claude": "claude-code",
    "claude-code": "claude-code",
    "antigravity": "agy",
    "agy": "agy",
    "opencode": "opencode2",
    "opencode2": "opencode2",
    "copilot": "github-copilot",
    "github-copilot": "github-copilot",
    "local": "ollama",
    "ollama": "ollama",
    # pi has no spawn provider — pi recipients are never resumed headlessly.
    "pi": "",
}


def _provider_for_source(source: str) -> str:
    return _SOURCE_TO_SPAWN.get(str(source or "").strip().lower(), "")


def _settings() -> Dict[str, Any]:
    cfg = _s._load_json("config.json") or {}
    mail = cfg.get("mail") if isinstance(cfg.get("mail"), dict) else {}

    def _val(key: str, default: Any) -> Any:
        v = mail.get(key, default)
        try:
            from hud_server import settings_api as _settings_api
            if f"mail.{key}" in cfg:
                v = _settings_api.value(f"mail.{key}", cfg)
                if v is None:
                    v = mail.get(key, default)
        except Exception:
            pass
        return v

    wake = str(_val("wake_offline", "ask") or "ask").strip().lower()
    if wake not in ("off", "ask", "on"):
        wake = "ask"
    try:
        tokens = int(_val("offline_budget_tokens", 20000))
    except (TypeError, ValueError):
        tokens = 20000
    tokens = max(1000, min(200000, tokens))
    try:
        seconds = int(_val("offline_budget_seconds", 120))
    except (TypeError, ValueError):
        seconds = 120
    if seconds <= 0:
        seconds = 120
    return {"wake_offline": wake, "offline_budget_tokens": tokens, "offline_budget_seconds": seconds}


def _resume_prompt(msg: Dict[str, Any]) -> str:
    return (
        f"You are answering a fast mail message (id {msg.get('id')}) for an offline chat.\n"
        f"Message from {msg.get('from')}:\n"
        f"---\n{msg.get('text')}\n---\n"
        "Answer in <= 120 words. Do not edit files. Reply through Subway (your final message is the reply)."
    )


def request_approval(msg: Dict[str, Any], budget_tokens: int) -> str:
    rid = f"perm_{uuid.uuid4().hex[:8]}"
    preview = str(msg.get("text") or "").strip().replace("\n", " ")[:200]
    detail = (f"Resume offline chat `{msg.get('to')}` ({msg.get('source')}) to answer fast mail "
              f"`{msg.get('id')}` from {msg.get('from')}: {preview} "
              f"(budget: {int(budget_tokens)} tokens)")
    rec = {
        "id": rid,
        "worker_id": str(msg.get("id") or ""),
        "role": "mail responder",
        "provider": msg.get("source") or "",
        "tool": "fastmail_offline",
        "kind": "fastmail_offline",
        "detail": detail,
        "rules": [],
        "status": "pending",
        "launched_by": _s._self_id(),
        "created_at": time.time(),
        "message_id": msg.get("id"),
        "budget_tokens": int(budget_tokens),
        "peer": msg.get("to") or "",
        "preview": preview,
    }
    with _s._state_lock("permissions"):
        _s._write_json_atomic(_s._perm_file(rid), rec)
    return rid


def resume_offline(msg: Dict[str, Any]) -> Dict[str, Any]:
    settings = _settings()
    wake = settings["wake_offline"]
    if wake == "off":
        return {"resumed": False, "reason": "wake_offline=off"}
    if not _provider_for_source(msg.get("source") or ""):
        return {"resumed": False, "reason": "unsupported"}
    if wake == "ask":
        rid = request_approval(msg, settings["offline_budget_tokens"])
        return {"resumed": False, "reason": "awaiting approval", "request_id": rid}
    return _spawn(msg)


def _spawn(msg: Dict[str, Any]) -> Dict[str, Any]:
    settings = _settings()
    provider = _provider_for_source(msg.get("source") or "")
    if not provider:
        return {"resumed": False, "reason": "unsupported"}
    import workers.spawn as _spawn_mod
    track = msg.get("track") or "main"
    result = _spawn_mod.t_spin_worker(
        role="mail responder",
        prompt=_resume_prompt(msg),
        provider=provider,
        thread=str(msg.get("to") or ""),
        max_tokens=settings["offline_budget_tokens"],
        max_seconds=settings["offline_budget_seconds"],
        track=track,
        wait=False,
        doctor="off",
        max_tool_calls=8,
    )
    if isinstance(result, str) and result.startswith("Error:"):
        return {"resumed": False, "reason": result}
    m = re.search(r"worker_[0-9a-f]{6}", str(result))
    if not m:
        return {"resumed": False, "reason": f"could not parse worker id from: {str(result)[:200]}"}
    worker_id = m.group(0)
    try:
        meta = _s._load_worker(worker_id) or {}
        meta["fastmail_reply_to"] = msg.get("id")
        _s._save_worker(meta)
    except Exception:
        pass
    return {"resumed": True, "worker_id": worker_id}


def capture_reply(worker_id: str, msg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    meta = _s._load_worker(worker_id) or {}
    reply_to = meta.get("fastmail_reply_to") or (msg or {}).get("id")
    original = msg or (fastmail.load_message(reply_to) if reply_to else {}) or {}
    if not original:
        return {"replied": False, "reason": "original message not found"}
    report = str(meta.get("report") or "").strip()
    if not report:
        return {"replied": False, "reason": "worker has no report"}
    from cli_tools.notes import t_reply_note
    result = t_reply_note(msg_id=original["id"], text=report, from_chat=worker_id)
    return {"replied": not str(result).startswith("Error:"), "detail": result, "worker_id": worker_id}
