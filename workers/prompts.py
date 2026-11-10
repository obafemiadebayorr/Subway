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

def _job_type(tier: str, role: str = "") -> str:
    r = (role or "").strip().lower()
    return f"{tier}:{r}" if r else tier

def _strip_standing_rules_and_code(prompt: str) -> str:
    text = prompt or ""
    for marker in ("\n\nSUBWAY STANDING RULES", "\n\n=== CODE", "\n\nNever read a file over 400 lines whole",
                   "\n\nSubway orchestrator rules", "\n\nMake the edits early"):
        if marker in text:
            text = text.split(marker)[0]
    lead_rule = "Leads: never run shell wait or poll loops; wait with await_worker, or one collect_reports(wait=True) or watch_workers call; never paste large code or logs into your own context."
    if lead_rule in text:
        text = text.replace(lead_rule, "")
    orch_rules = getattr(_s, "_ORCHESTRATOR_RULES", None)
    if orch_rules and orch_rules in text:
        text = text.replace(orch_rules, "")
    edit_rule = getattr(_s, "_EDIT_WORKER_RULE", None)
    if edit_rule and edit_rule in text:
        text = text.replace(edit_rule, "")
    rules = getattr(_s, "_WORKER_STANDING_RULES", None)
    if rules and rules in text:
        text = text.replace(rules, "")
    reading = getattr(_s, "_WORKER_READING_RULE", None)
    if reading and reading in text:
        text = text.replace(reading, "")
    return text.strip()

def _clip_prompt_summary(prompt: str, max_chars: int = 300) -> str:
    cleaned = _strip_standing_rules_and_code(prompt)
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[:max_chars]}… ({len(cleaned)} chars)"

def _is_read_only_prompt(prompt: str) -> bool:
    text = (prompt or "").lower()
    return any(p in text for p in ("read-only", "read only", "do not edit", "do not modify"))

def _worker_expected_to_edit(meta: Dict[str, Any]) -> bool:
    role = str(meta.get("role") or "").lower()
    raw_prompt = str(meta.get("prompt") or "")
    prompt = _strip_standing_rules_and_code(raw_prompt)
    if _s._is_read_only_prompt(prompt):
        return False
    capabilities = meta.get("capabilities") or []
    if "subagents" in capabilities:
        return False
    worker_id = meta.get("id")
    if worker_id and any(w.get("parent_id") == worker_id for w in _s._all_workers()):
        return False
    # If capabilities is non-empty and lacks files:write and terminal:full (and mode != god), return False
    if capabilities and "files:write" not in capabilities and "terminal:full" not in capabilities and meta.get("mode") != "god":
        return False
    if ("files:write" not in capabilities and "terminal:full" not in capabilities
            and meta.get("mode") != "god" and meta.get("permission_mode") != "auto_approve"):
        return False
    if re.search(r"review|reviewer|analyst|investigat|diag|audit|research|lead|certif|judge|probe|check|verif|inspect|plan", role):
        if not re.search(r"\b(?:edit|modify)\b", prompt, re.IGNORECASE):
            return False
    return bool(re.search(
        r"\b(?:add|change|create|implement|update|modify|edit|fix|refactor|remove|delete|rename|write|replace|introduce|extend|migrate|convert|build)\b",
        prompt,
        re.IGNORECASE,
    ))

