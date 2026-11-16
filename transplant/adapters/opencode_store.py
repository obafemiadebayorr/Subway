"""Write hook for a SQLite session store with session_v2 / session_message / event_sequence.

Facts measured 2026-11-15 (v2.3.0) are in the adapter JSON "notes". This module only
INSERTs rows for one new session inside a single BEGIN IMMEDIATE transaction; it never
updates or deletes existing rows and never invents a project.
"""

import json
import os
import random
import sqlite3
import string
import time

from transplant import writer

_B62 = string.digits + string.ascii_uppercase + string.ascii_lowercase
_MASK48 = 0xFFFFFFFFFFFF
_WORDS = (
    "amber", "brisk", "calm", "clever", "cosmic", "eager", "gentle", "golden",
    "hidden", "lucky", "mellow", "nimble", "proud", "quiet", "rapid", "silver",
    "steady", "sunny", "swift", "tidy", "bright", "noble", "bold", "keen",
)
_NOUNS = (
    "harbor", "meadow", "river", "canyon", "forest", "garden", "island", "lagoon",
    "orchard", "prairie", "summit", "valley", "beacon", "comet", "falcon", "lantern",
    "pine", "reef", "willow", "atlas", "cedar", "delta", "ember", "harvest",
)

_last = {"ms": 0, "n": 0}


def _counter(ms):
    if ms == _last["ms"]:
        _last["n"] += 1
    else:
        _last["ms"], _last["n"] = ms, 0
    return _last["n"]


def _rand62(n=14):
    return "".join(random.choice(_B62) for _ in range(n))


def new_id(prefix, ms, descending=False):
    """prefix + 12 hex (ms*4096 + counter, 48-bit; bit-inverted when descending) + 14 base62."""
    value = (ms * 4096 + _counter(ms)) & _MASK48
    if descending:
        value = (~value) & _MASK48
    return "%s%012x%s" % (prefix, value, _rand62())


def _project_id(con, cwd):
    row = con.execute(
        "SELECT project_id FROM project_directory WHERE directory = ? "
        "ORDER BY time_created DESC LIMIT 1",
        (cwd,),
    ).fetchone()
    if row:
        return row[0]
    row = con.execute(
        "SELECT id FROM project WHERE worktree = ? ORDER BY time_updated DESC LIMIT 1",
        (cwd,),
    ).fetchone()
    if row:
        return row[0]
    raise RuntimeError("no session-store project for %s" % cwd)


def _ms(ts):
    try:
        ts = float(ts or 0)
    except (TypeError, ValueError):
        ts = 0.0
    return int(ts * 1000) if ts > 0 else 0


def write_session(adapter, conv, *, cwd, model="", extra=None):
    extra = dict(extra or {})
    db = extra.get("db_path") or adapter.store_paths()[0]
    if not os.path.isfile(db):
        raise RuntimeError("session store not found: %s" % db)
    provider_id = extra.get("provider_id", "")
    now = int(time.time() * 1000)
    turns = [t for t in (conv.get("turns") or []) if t.get("role") in ("user", "assistant")]

    con = sqlite3.connect(db, timeout=1.0, isolation_level=None)
    try:
        try:
            con.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise RuntimeError("session store busy: %s" % exc) from exc
        try:
            project_id = _project_id(con, cwd)
            session_id = new_id("ses_", now, descending=True)
            model_json = (
                json.dumps({"id": model, "providerID": provider_id}) if model else None
            )
            con.execute(
                "INSERT INTO session_v2 (id, project_id, slug, directory, path, title, "
                "version, model, time_created, time_updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    project_id,
                    "%s-%s" % (random.choice(_WORDS), random.choice(_NOUNS)),
                    cwd,
                    "",
                    conv.get("title") or "Transplanted conversation",
                    extra.get("version") or "2.3.0",
                    model_json,
                    now,
                    now,
                ),
            )
            prev = 0
            seq = 0
            for turn in turns:
                seq += 1
                created = max(_ms(turn.get("ts")) or now, prev + 1)
                prev = created
                if turn["role"] == "user":
                    data = {"time": {"created": created}, "text": turn.get("text", ""), "files": []}
                else:
                    data = {
                        "time": {"created": created, "completed": created},
                        "agent": "build",
                        "model": {"id": model or turn.get("model", ""), "providerID": provider_id},
                        "content": [{"type": "text", "text": writer.render_assistant_text(turn)}],
                        "finish": "stop",
                        "rawFinish": "stop",
                        "cost": 0,
                        "tokens": {"input": 0, "output": 0, "reasoning": 0,
                                   "cache": {"read": 0, "write": 0}},
                    }
                con.execute(
                    "INSERT INTO session_message (id, session_id, type, seq, time_created, "
                    "time_updated, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_id("msg_", created), session_id, turn["role"], seq, created, created,
                     json.dumps(data, ensure_ascii=False)),
                )
            con.execute(
                "INSERT INTO event_sequence (aggregate_id, seq, owner_id) VALUES (?, ?, NULL)",
                (session_id, seq),
            )
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return {"session_id": session_id, "path": db, "records": len(turns)}
