from __future__ import annotations

import contextlib
import datetime as _dt
import glob
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional

import server as _s

def _devin_dir() -> str:
    """Return the User directory for Devin."""
    custom = os.environ.get("DEVIN_STORAGE_DIR")
    if custom:
        return custom
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Devin/User")
    elif sys.platform == "win32":
        app_data = os.environ.get("APPDATA", "")
        return os.path.join(app_data, "Devin", "User")
    return os.path.expanduser("~/.config/Devin/User")


def devin_list(include_subagents: bool = False) -> List[Dict[str, Any]]:
    """List all Devin ACP sessions."""
    out: List[Dict[str, Any]] = []
    base = os.path.join(_devin_dir(), "acp-messages")
    if not os.path.isdir(base):
        return out

    files = glob.glob(os.path.join(base, "*.db"))
    files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0.0, reverse=True)

    for p in files:
        sid = os.path.splitext(os.path.basename(p))[0]
        mtime = os.path.getmtime(p)
        ts_str = ""
        try:
            ts_str = _dt.datetime.fromtimestamp(mtime, tz=_dt.timezone.utc).isoformat()
        except Exception:
            pass

        title = f"Devin Session {sid[:8]}"
        project = ""
        try:
            conn = sqlite3.connect(p, timeout=5)
            try:
                meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
                info_raw = meta.get("info", "{}")
                if info_raw:
                    info = json.loads(info_raw)
                    if isinstance(info, dict):
                        version = ""
                        for opt in info.get("configOptions", []):
                            if opt.get("id") == "devin_version":
                                version = opt.get("currentValue", "")
                        if version:
                            title = f"Devin ({version}) {sid[:8]}"
            finally:
                conn.close()
        except Exception:
            pass

        out.append({
            "source": "devin",
            "id": str(sid),
            "title": title,
            "project": project,
            "updated": ts_str,
            "subagent": False,
        })

    return out


def devin_read(sid: str) -> List[Dict[str, Any]]:
    """Read all turns for a Devin ACP session."""
    turns: List[Dict[str, Any]] = []
    db_path = os.path.join(_devin_dir(), "acp-messages", f"{sid}.db")
    if not os.path.exists(db_path):
        return turns

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            rows = conn.execute(
                "SELECT position, kind, payload FROM messages ORDER BY position ASC"
            ).fetchall()
            for _pos, kind, payload in rows:
                k = str(kind or "").lower()
                role = "user" if "user" in k else "assistant"
                text = ""
                ts = None

                if payload:
                    try:
                        p_data = json.loads(payload)
                        if isinstance(p_data, dict):
                            text = p_data.get("text") or p_data.get("content") or ""
                            ts = p_data.get("timestamp") or p_data.get("time")
                            if not role:
                                role = p_data.get("role") or role
                        elif isinstance(p_data, str):
                            text = p_data
                    except Exception:
                        text = str(payload)

                if text:
                    turns.append({"role": role, "text": text.strip(), "ts": ts})
        finally:
            conn.close()
    except Exception:
        pass

    return turns


def devin_read_segments(chat: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    sid = chat.get("id", "")
    turns = devin_read(sid)
    return [turns] if turns else [[]]
