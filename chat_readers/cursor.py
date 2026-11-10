from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional

import server as _s

def _cursor_dir() -> str:
    """Return the globalStorage directory for Cursor."""
    custom = os.environ.get("CURSOR_STORAGE_DIR")
    if custom:
        return custom
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Cursor/User/globalStorage")
    elif sys.platform == "win32":
        app_data = os.environ.get("APPDATA", "")
        return os.path.join(app_data, "Cursor", "User", "globalStorage")
    return os.path.expanduser("~/.config/Cursor/User/globalStorage")


def _cursor_db() -> Optional[sqlite3.Connection]:
    p = os.path.join(_cursor_dir(), "conversation-search.db")
    if not os.path.exists(p):
        return None
    for uri in (f"file:{p}?mode=ro", f"file:{p}?immutable=1"):
        try:
            con = sqlite3.connect(uri, uri=True, timeout=5)
            con.execute("SELECT 1")
            return con
        except Exception:
            continue
    try:
        con = sqlite3.connect(p, timeout=5)
        con.execute("SELECT 1")
        return con
    except Exception:
        return None


def _cursor_vscdb() -> Optional[sqlite3.Connection]:
    p = os.path.join(_cursor_dir(), "state.vscdb")
    if not os.path.exists(p):
        return None
    for uri in (f"file:{p}?mode=ro", f"file:{p}?immutable=1"):
        try:
            con = sqlite3.connect(uri, uri=True, timeout=5)
            con.execute("SELECT 1")
            return con
        except Exception:
            continue
    try:
        con = sqlite3.connect(p, timeout=5)
        con.execute("SELECT 1")
        return con
    except Exception:
        return None


def cursor_list(include_subagents: bool = False) -> List[Dict[str, Any]]:
    """List all Cursor Composer conversations."""
    out: List[Dict[str, Any]] = []
    con = _cursor_db()
    if con is not None:
        try:
            rows = con.execute(
                "SELECT id, title, updated_at, is_archived FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
            for cid, title, up_at, is_arch in rows:
                ts_str = ""
                if up_at:
                    try:
                        ts_str = _dt.datetime.fromtimestamp(
                            up_at / 1000.0, tz=_dt.timezone.utc
                        ).isoformat()
                    except Exception:
                        ts_str = str(up_at)
                display_title = (title or "").strip() or f"Cursor Composer {cid[:8]}"
                out.append({
                    "source": "cursor",
                    "id": str(cid),
                    "title": display_title,
                    "project": "",
                    "updated": ts_str,
                    "subagent": False,
                    "archived": bool(is_arch),
                })
        except Exception:
            pass
        finally:
            con.close()

    # Fallback to state.vscdb cursorDiskKV if conversation-search.db is empty
    if not out:
        vcon = _cursor_vscdb()
        if vcon is not None:
            try:
                rows = vcon.execute(
                    "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
                ).fetchall()
                for key, val in rows:
                    cid = key.replace("composerData:", "")
                    if not cid or cid == "empty-state-draft":
                        continue
                    title = f"Cursor Composer {cid[:8]}"
                    ts_str = ""
                    try:
                        d = json.loads(val or "{}")
                        created = d.get("createdAt")
                        if created:
                            ts_str = _dt.datetime.fromtimestamp(
                                created / 1000.0, tz=_dt.timezone.utc
                            ).isoformat()
                        txt = (d.get("text") or "").strip()
                        if txt:
                            title = _s.one_line(txt, 60)
                    except Exception:
                        pass
                    out.append({
                        "source": "cursor",
                        "id": str(cid),
                        "title": title,
                        "project": "",
                        "updated": ts_str,
                        "subagent": False,
                    })
            except Exception:
                pass
            finally:
                vcon.close()

    return out


def cursor_read(cid: str) -> List[Dict[str, Any]]:
    """Read all turns for a Cursor conversation."""
    turns: List[Dict[str, Any]] = []
    vcon = _cursor_vscdb()
    if vcon is None:
        return turns

    try:
        row = vcon.execute(
            "SELECT value FROM cursorDiskKV WHERE key = ?",
            (f"composerData:{cid}",),
        ).fetchone()
        if not row or not row[0]:
            return turns

        data = json.loads(row[0])
        created = data.get("createdAt")
        ts_str = ""
        if created:
            with contextlib.suppress(Exception):
                ts_str = _dt.datetime.fromtimestamp(
                    created / 1000.0, tz=_dt.timezone.utc
                ).isoformat()

        cmap = data.get("conversationMap") or {}
        if isinstance(cmap, dict) and cmap:
            # Bubbles in conversationMap
            for _bid, bval in sorted(cmap.items(), key=lambda kv: kv[1].get("createdAt") or 0):
                if not isinstance(bval, dict):
                    continue
                b_type = bval.get("type") or bval.get("bubbleType")
                role = "user" if b_type in (1, "user", "human") else "assistant"
                txt = bval.get("text") or ""
                b_ts = bval.get("createdAt")
                t_str = ts_str
                if b_ts:
                    with contextlib.suppress(Exception):
                        t_str = _dt.datetime.fromtimestamp(
                            b_ts / 1000.0, tz=_dt.timezone.utc
                        ).isoformat()
                if txt.strip():
                    turns.append({"role": role, "text": txt.strip(), "ts": t_str})

        # Fallback to top-level text if conversationMap was empty
        if not turns:
            top_text = (data.get("text") or "").strip()
            if top_text:
                turns.append({"role": "user", "text": top_text, "ts": ts_str})
    except Exception:
        pass
    finally:
        vcon.close()

    return turns


def cursor_read_segments(chat: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    cid = chat.get("id", "")
    turns = cursor_read(cid)
    return [turns] if turns else [[]]
