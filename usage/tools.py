"""Usage package: tools submodule (split from usage.py)."""

from __future__ import annotations

import base64
import concurrent.futures
import contextlib
import contextvars
import datetime as _dt
import difflib
import glob
import hashlib
import hmac
import http.server
import json
import math
import os
import re
import secrets
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
sys.modules.setdefault("server", sys.modules[__name__])  # one module object when run as a script
import tempfile
import threading
try:
    import ctypes
except ImportError:
    ctypes = None  # type: ignore[assignment]
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

import terminals
import native_terminals
import threads

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

import server as _s


def concurrency_nudge(prompt_text: str) -> str:
    """Return a concurrency notice when the prompt lists multiple independent tasks, else ""."""
    task_markers = re.findall(r"^\s*(?:\d+[\.)]|[-*]|&)\s+\S+", prompt_text, re.MULTILINE)
    has_sequential = bool(re.search(r"\b(?:then|after that|afterwards|first\s+.*?\s+then)\b", prompt_text, re.IGNORECASE))
    if len(task_markers) >= 2 and not has_sequential:
        return "[CONCURRENCY] Multiple independent tasks detected. You can run them concurrently using run_concurrent(tasks=[...])."
    return ""


def hook_prompt(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Claude Code UserPromptSubmit hook: warn or block when chat budget is reached or close, and report worker events."""
    try:
        import governor
        governor.turn("claude", event.get("session_id", ""))
    except Exception:
        pass
    if not os.environ.get("SUBWAY_WORKER"):
        prompt_text = str(event.get("prompt") or "")
        for line in prompt_text.splitlines():
            line_str = line.strip()
            m_comp = re.match(r"^!?\s*subway-context\s+compaction\s+(\S+)(?:\s+<?([\d.]+)\s*([kKmM]?)>?)?\s*$",
                              line_str, re.IGNORECASE)
            if m_comp:
                at = None
                if m_comp.group(2):
                    at = int(float(m_comp.group(2)) * {"k": 1000, "m": 1000000}.get(m_comp.group(3).lower(), 1))
                res = _s.set_compaction(m_comp.group(1).lower().strip(), at)
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": res
                    }
                }
            m = re.match(r"^!?\s*subway-context\s+budget\s+(\S+)\s+(?:<([^>]+)>|(\S+))\s*$", line_str, re.IGNORECASE)
            if m:
                scope = m.group(1).lower().strip()
                raw_tok = (m.group(2) or m.group(3)).strip().replace(",", "")
                multiplier = 1
                if raw_tok.lower().endswith("m"):
                    multiplier = 1_000_000
                    raw_num = raw_tok[:-1]
                elif raw_tok.lower().endswith("k"):
                    multiplier = 1_000
                    raw_num = raw_tok[:-1]
                else:
                    raw_num = raw_tok
                try:
                    num = int(float(raw_num) * multiplier)
                except ValueError:
                    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext":
                            f"[BUDGET] Not changed: '{raw_tok}' isn't a number of tokens. Tell the user to try, say, 3.5M."}}
                _s.set_budget(scope, num, session_id=str(event.get("session_id") or "") or None if scope == "chat" else None)
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": f"[BUDGET] Set {scope} to {num} tokens, as you asked."
                    }
                }

    sid = str(event.get("session_id") or "")
    cfg = _s._load_json("config.json")
    limit, scope = _s.chat_budget_for(sid, cfg)
    used = None
    if limit:
        used = _s.chat_tokens_used(sid)
        if used is not None and used >= limit:
            label = "This chat's budget" if scope == "chat" else "The universal chat budget"
            cmd = "subway-context budget chat <tokens>" + (f" --chat {sid}" if scope == "chat" else "")
            _s.notify_desktop_event(f"chat_budget_blocked|{sid or _s._self_id() or 'unknown-chat'}|{limit}", "Subway Context",
                                 f"{label} is reached: {used:,} of {limit:,} tokens. Add more with: {cmd}, or in the HUD.",
                                 route="overview")
            return {
                "decision": "block",
                "reason": f"{label} is reached: {used:,} of {limit:,} tokens. Add more with: {cmd}, or in the HUD."
            }

    # After the block check: a blocked prompt can't deliver pings, so they must stay pending.
    pings = _s.pending_worker_pings(sid) if sid else []
    if not pings:
        pings = _s.pending_worker_pings(_s._self_id())
    pings_text = "\n\n".join(pings) if pings else ""

    notices = []
    if limit and used is not None and limit > 0:
        pct = (used / limit) * 100
        crossed = 0
        for t in (50, 75, 90, 100):
            if pct >= t:
                crossed = t
        if crossed > 0:
            with _s._state_lock("prompt_banner"):
                b_state = _s._load_json("prompt_banner_state.json")
                if not isinstance(b_state, dict):
                    b_state = {}
                last_t = b_state.get(sid, 0)
                if crossed > last_t:
                    b_state[sid] = crossed
                    _s._save_json("prompt_banner_state.json", b_state)
                    label = "this chat's" if scope == "chat" else "the universal chat"
                    cmd = f"subway-context budget chat <tokens>" + (f" --chat {sid}" if scope == "chat" and sid else "")
                    notices.append(f"[BUDGET] Warning: {label} budget at {pct:.1f}% ({used:,} of {limit:,} tokens). Add more with: {cmd}, or in the HUD.")

    prompt_text = str(event.get("prompt") or "")
    c_notice = _s.compaction_notice(sid, prompt_text)
    if c_notice:
        notices.append(c_notice)

    # Concurrency nudge: detect multiple independent sub-tasks in prompt
    nudge = concurrency_nudge(prompt_text)
    if nudge:
        notices.append(nudge)

    if notices:
        ctx = f"{chr(10).join(notices)}\n\n{pings_text}".strip() if pings_text else "\n\n".join(notices)
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": ctx
            }
        }

    if pings_text:
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": pings_text
            }
        }
    return None


def run_hook_prompt() -> int:
    """Claude Code UserPromptSubmit hook entry. Fails open."""
    try:
        decision = _s.hook_prompt(json.load(sys.stdin))
    except Exception as e:
        _s.log("hook prompt error:", e)
        return 0
    if decision:
        print(json.dumps(decision))
    return 0
