from __future__ import annotations

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

import queue

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

import sse_stream

import threads

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

import server as _s


def _config_has_subway(path: str, *keys: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            node: Any = json.load(f)
    except (OSError, ValueError):
        return False
    for k in keys:
        node = node.get(k) if isinstance(node, dict) else None
    return isinstance(node, dict) and ("subway" in node or "subway-context" in node)

def _subway_sessions() -> Dict[str, int]:
    """Subway MCP servers running right now, by the client that launched them."""
    counts = {"claude": 0, "antigravity": 0, "opencode": 0}
    if sys.platform == "win32":
        try:
            cmd = 'powershell -NoProfile -Command "Get-CimInstance Win32_Process | Select ProcessId,ParentProcessId,CommandLine | ConvertTo-Json"'
            raw = subprocess.check_output(cmd, shell=True, text=True, timeout=5)
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            procs: Dict[int, Tuple[int, str]] = {
                int(p["ProcessId"]): (int(p.get("ParentProcessId") or 0), str(p.get("CommandLine") or ""))
                for p in data if p.get("ProcessId")
            }
        except Exception:
            return counts
        for _pid, (ppid, args) in procs.items():
            if not re.search(r"(?i)subway(?:-context)?/server\.py", args.replace("\\", "/")) or re.search(r"server\.py\s+(hud|__supervise)", args):
                continue
            parent = procs.get(ppid, (0, ""))[1].strip().split(" ")[0].replace('"', '')
            name = os.path.basename(parent).lower()
            if name.startswith("opencode"):
                counts["opencode"] += 1
            elif name.startswith("agy") or "antigravity" in name:
                counts["antigravity"] += 1
            elif name.startswith("claude"):
                counts["claude"] += 1
        return counts
    try:
        out = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,args="], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return counts
    procs: Dict[int, Tuple[int, str]] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            with contextlib.suppress(ValueError):
                procs[int(parts[0])] = (int(parts[1]), parts[2])
    for _pid, (ppid, args) in procs.items():
        if not re.search(r"(?i)subway(?:-context)?/server\.py", args) or re.search(r"server\.py\s+(hud|__supervise)", args):
            continue
        parent = procs.get(ppid, (0, ""))[1].split(" ")[0]
        name = os.path.basename(parent).lower()
        if name.startswith("opencode"):
            counts["opencode"] += 1
        elif name.startswith("agy") or "antigravity" in name:
            counts["antigravity"] += 1
        elif name.startswith("claude"):
            counts["claude"] += 1
    return counts

def _app_installed(*names: str) -> Optional[str]:
    for folder in _s.APPLICATIONS_DIRS:
        for n in names:
            p = os.path.join(folder, n)
            if os.path.exists(p):
                return p
    return None

@_s.ttl_cached(30.0)
def detect_clients() -> Dict[str, Dict[str, Any]]:
    """What is provably on this machine. Each client lists the evidence for its claims."""
    sessions = _s._subway_sessions()
    out: Dict[str, Dict[str, Any]] = {}

    def add(key: str, evidence: List[str], cli: Optional[str] = None, signed_in: Optional[bool] = None,
            registered: bool = False) -> None:
        out[key] = {"installed": bool(evidence), "evidence": evidence, "signed_in": signed_in,
                    "dispatchable": bool(cli), "registered": registered, "sessions": sessions.get(key, 0)}

    cb = _s.find_claude_binary()
    add("claude", [f"CLI: {cb}"] if cb else ([f"data: {_s.CLAUDE_DIR}"] if os.path.isdir(os.path.join(_s.CLAUDE_DIR, "projects")) else []),
        cli=cb, signed_in=bool(_s._read_claude_json().get("oauthAccount")) or None,
        registered=_s._config_has_subway(_s.CLAUDE_JSON, "mcpServers"))

    ab = _s.find_agy_binary()
    app = _s._app_installed("Antigravity IDE.app", "Antigravity.app")
    ev = ([f"CLI: {ab}"] if ab else []) + ([f"app: {app}"] if app else [])
    if not ev and os.path.isdir(os.path.join(_s.AGY_DIR, "brain")):  # Bare ~/.gemini is also Gemini CLI's.
        ev = [f"data: {_s.AGY_DIR}"]
    add("antigravity", ev, cli=ab,
        signed_in=True if (_s._agy_hubs() or os.path.exists(_s._home(".gemini", "jetski-standalone-oauth-token"))) else None,
        registered=_s._config_has_subway(_s._home(".gemini", "config", "mcp_config.json"), "mcpServers"))

    ob = _s.find_opencode_binary()
    ev = [f"CLI: {ob}"] if ob else ([f"data: {_s.OPENCODE_DIR}"] if os.path.exists(os.path.join(_s.OPENCODE_DIR, "opencode.db")) else [])
    cfg = _s._home(".config", "opencode", "opencode.json")
    add("opencode", ev, cli=ob, registered=_s._config_has_subway(cfg, "mcp", "servers") or _s._config_has_subway(cfg, "mcp"))

    ollama = shutil.which("ollama") or next((p for p in ("/opt/homebrew/bin/ollama", "/usr/local/bin/ollama") if os.path.exists(p)), None)
    llama = shutil.which("llama-server") or next((p for p in ("/opt/homebrew/bin/llama-server",) if os.path.exists(p)), None)
    add("local", ([f"Ollama: {ollama}"] if ollama else []) + ([f"llama-server: {llama}"] if llama else []), cli=ob if ollama else None)

    kimi = shutil.which("kimi") or shutil.which("kimi-cli")
    ev = ([f"CLI: {kimi}"] if kimi else [])
    app = _s._app_installed("Kimi.app")
    if app:
        ev.append(f"app: {app}")
    ev += [f"VS Code extension: {os.path.basename(p)}" for p in
           sorted(glob.glob(_s._home(".vscode", "extensions", "moonshot-ai.kimi-code-*")))[-1:]]
    add("kimi", ev)

    # DeepAI Code (provider id "pi", station prefix @deep). Not a VS Code fork:
    # it is an Electron app whose sessions live under userdata, and its models are
    # reachable headlessly through the pi CLI (npx @earendil-works/pi-coding-agent).
    pb = _s.find_pi_binary()
    pi_sessions = []
    try:
        pi_sessions = _s.pi_files()
    except Exception:
        pi_sessions = []
    app = _s._app_installed("DeepAI Code.app", "DeepAI.app")
    ev = ([f"CLI: {pb}"] if pb else []) + ([f"app: {app}"] if app else [])
    if pi_sessions:
        ev.append(f"data: {len(pi_sessions)} session(s)")
    # Check where pi actually reads mcp.json (its resolved agent dir), then the OSS path:
    # registering only in ~/.pi/agent leaves the app unregistered while looking installed.
    pi_registered = any(
        _s._config_has_subway(os.path.join(d, "mcp.json"), "mcpServers")
        for d in {getattr(_s, "PI_DIR", ""), _s._home(".pi", "agent")} if d
    )
    add("pi", ev, cli=pb, registered=pi_registered)

    copilot = shutil.which("copilot")
    ev = [f"CLI: {copilot}"] if copilot else []
    ev += [f"VS Code extension: {os.path.basename(p)}" for p in
           sorted(glob.glob(_s._home(".vscode", "extensions", "github.copilot*")))[-1:]]
    if os.path.isdir(_s._home(".config", "github-copilot")):
        ev.append("config: ~/.config/github-copilot")
    add("copilot", ev, cli=copilot)
    if not ev and os.path.isdir(_s._home("Library", "Application Support", "Code", "User", "globalStorage", "github.copilot-chat")):
        out["copilot"].update({"installed": True, "evidence": ["VS Code data only, no extension or CLI found"]})

    cur_b = _s.find_cursor_binary()
    cur_app = _s._app_installed("Cursor.app")
    cur_ev = ([f"CLI: {cur_b}"] if cur_b else []) + ([f"app: {cur_app}"] if cur_app else [])
    cur_dir = os.environ.get("CURSOR_STORAGE_DIR") or _s._home("Library", "Application Support", "Cursor", "User")
    if os.path.isdir(cur_dir):
        cur_ev.append(f"data: {cur_dir}")
    cur_reg = _s._config_has_subway(_s._home(".cursor", "mcp.json"), "mcpServers")
    add("cursor", cur_ev, cli=None, registered=cur_reg)

    dev_b = _s.find_devin_binary()
    dev_app = _s._app_installed("Devin.app")
    dev_ev = ([f"CLI: {dev_b}"] if dev_b else []) + ([f"app: {dev_app}"] if dev_app else [])
    dev_dir = os.environ.get("DEVIN_STORAGE_DIR") or _s._home("Library", "Application Support", "Devin", "User")
    if os.path.isdir(dev_dir):
        dev_ev.append(f"data: {dev_dir}")
    dev_reg = _s._config_has_subway(_s._home(".codeium", "windsurf", "mcp_config.json"), "mcpServers")
    add("devin", dev_ev, cli=None, registered=dev_reg)

    for cp in _s.load_custom_providers():
        cid = cp["id"]
        cbin = shutil.which(cp.get("bin", "")) if cp.get("bin") else None
        if cbin:
            existing_ev = out.get(cid, {}).get("evidence", [])
            ev = [f"CLI: {cbin}"] + [e for e in existing_ev if not e.startswith("CLI:")]
            add(cid, ev, cli=cbin)
        elif cid not in out:
            add(cid, [])
    return out
