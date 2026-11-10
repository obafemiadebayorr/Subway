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
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple, Union

import terminals
import native_terminals
import threads

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

import server as _s
def load_aliases() -> Dict[str, str]:
    return _s._load_json("aliases.json")


def _sync_opencode_agent_stubs(force: bool = False) -> None:
    """Keep OpenCode's agents folder in sync with all 4 Subway alias tiers:
    1. Pinned aliases from aliases.json
    2. Floating latest stubs (@agy-latest, @claude-latest, @opencode-latest)
    3. Workspace heads, only for agents that have a chat in that project
    4. Top recent topic slugs (@agy-<topic>)
    """
    now = time.time()
    if not force and now - _s._last_stub_sync < _s._STUB_SYNC_INTERVAL:
        return
    _s._last_stub_sync = now

    ag_dir = _s.OPENCODE_AGENTS_DIR
    try:
        os.makedirs(ag_dir, exist_ok=True)
        chats = _s.all_chats(include_subagents=False)
    except Exception as e:
        _s.log("stub sync skipped:", e)  # Never prune against a partial view of the world.
        return

    desired_stubs: Dict[str, str] = {}

    for alias in _s.load_aliases():
        clean = alias.strip().lower().lstrip("@")
        desired_stubs[clean] = f"Subway Context pinned transit connector to @{clean}."

    for a in ("claude-latest", "agy-latest", "opencode-latest"):
        src_name = "Claude Code" if "claude" in a else ("Antigravity" if "agy" in a else "OpenCode")
        desired_stubs[a] = f"Subway Context transit connector to latest {src_name} session."

    prefix_for = {"claude": ("claude", "Claude Code"), "antigravity": ("agy", "Antigravity"),
                  "opencode": ("opencode", "OpenCode"), "pi": ("deep", "DeepAI Code")}
    for c in chats:
        prefix, label = prefix_for.get(c.get("source", ""), ("", ""))
        if not prefix:
            continue
        for p in _s._project_names(c.get("project")):
            desired_stubs[f"{prefix}-{p}"] = f"Subway Context transit connector to latest {label} chat in {p}."

    agy_chats = [c for c in chats if c.get("source") == "antigravity"]
    agy_chats.sort(key=lambda c: _s.sort_key_ts(c.get("updated")), reverse=True)
    for c in agy_chats[:6]:
        title = c.get("title") or ""
        slug = _s.slugify_topic(title)
        if slug and slug != "chat":
            desired_stubs[f"agy-{slug}"] = f"Subway Context transit to @agy-{slug} ({_s.one_line(title, 60)})."

    # Every name becomes a filename, so anything unsafe is dropped rather than written.
    desired_stubs = {k: v for k, v in desired_stubs.items() if _s.ALIAS_RE.match(k)}

    for stub_name, desc in desired_stubs.items():
        content = (
            f"---\n"
            f"description: {json.dumps(desc, ensure_ascii=False)}\n"
            f"mode: subagent\n"
            f"---\n"
            f"{_s._STUB_MARKER}\n"
            f"You are the Subway Context transit connector for @{stub_name}.\n"
            f"When invoked, call MCP tool `read_chat(chat_id=\"@{stub_name}\", mode=\"decisions\", since=\"last\")` "
            f"to pull the latest context, decisions, and files touched into this session.\n"
        )
        with contextlib.suppress(OSError):
            _s._write_if_changed(os.path.join(ag_dir, f"{stub_name}.md"), content)

    # Prune outdated subway-auto-stubs (preserve manual stubs and current desired stubs)
    with contextlib.suppress(OSError):
        for fname in os.listdir(ag_dir):
            if fname.endswith(".md") and fname[:-3] not in desired_stubs:
                fpath = os.path.join(ag_dir, fname)
                with contextlib.suppress(OSError):
                    with open(fpath, "r", encoding="utf-8") as f:
                        hdr = f.read(500)
                    if _s._STUB_MARKER in hdr:
                        os.remove(fpath)


def save_alias(alias: str, target_id: str) -> None:
    clean_alias = alias.strip().lower().lstrip("@")
    if not _s.ALIAS_RE.match(clean_alias):
        raise ValueError(f"invalid alias '{alias}': use letters, digits, '.', '_' or '-' (max 64 characters)")
    with _s._state_lock("aliases"):
        aliases = _s.load_aliases()
        aliases[clean_alias] = target_id.strip()
        _s._save_json("aliases.json", aliases)
    _s._sync_opencode_agent_stubs(force=True)


def _checkpoint_key(c1: str, c2: str) -> str:
    a, b = sorted([c1.strip().lower(), c2.strip().lower()])
    return f"{a}<->{b}"


def get_checkpoints(c1: str, c2: str) -> List[Dict[str, Any]]:
    cps = _s._load_json("checkpoints.json")
    k = _s._checkpoint_key(c1, c2)
    return cps.get(k, [])


def record_checkpoint(c1: str, c2: str, c1_exchange: int, c2_exchange: int) -> None:
    with _s._state_lock("checkpoints"):
        cps = _s._load_json("checkpoints.json")
        k = _s._checkpoint_key(c1, c2)
        hist = cps.get(k, [])
        rec = {
            "ts": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            c1: c1_exchange,
            c2: c2_exchange,
        }
        hist.append(rec)
        cps[k] = hist[-50:]
        _s._save_json("checkpoints.json", cps)
