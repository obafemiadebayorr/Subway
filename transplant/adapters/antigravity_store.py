"""Write hook for a per-conversation SQLite store whose steps are protobuf blobs.

Approach (facts in the adapter JSON "notes"): clone a real conversation as a template.
The newest local conversation that has a user step and a text reply step is read through
a private copy, its schema is recreated in a new file, its trajectory rows are copied with
every id and workspace path swapped for new ones, and one step per IR turn is built by
cloning the template's user step / reply step and replacing only the text fields. Reasoning
text and its signature are removed. Existing conversation files are never opened for writing.
"""

import glob
import os
import shutil
import sqlite3
import tempfile
import time
import uuid

# ---------------------------------------------------------------- protobuf (lossless)


def _varint(buf, i):
    result = shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if b < 0x80:
            return result, i


def _enc_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def parse(buf):
    """bytes -> list of [field, wire, value]; raises ValueError if buf is not a clean message."""
    out, i, n = [], 0, len(buf)
    try:
        while i < n:
            key, i = _varint(buf, i)
            field, wire = key >> 3, key & 7
            if field == 0:
                raise ValueError("field 0")
            if wire == 0:
                val, i = _varint(buf, i)
            elif wire == 1:
                val, i = buf[i:i + 8], i + 8
            elif wire == 5:
                val, i = buf[i:i + 4], i + 4
            elif wire == 2:
                ln, i = _varint(buf, i)
                val, i = buf[i:i + ln], i + ln
            else:
                raise ValueError("wire %d" % wire)
            if i > n:
                raise ValueError("truncated")
            out.append([field, wire, val])
    except IndexError as exc:
        raise ValueError("truncated") from exc
    return out


def encode(msg):
    out = bytearray()
    for field, wire, val in msg:
        out += _enc_varint((field << 3) | wire)
        if wire == 0:
            out += _enc_varint(val)
        elif wire == 2:
            out += _enc_varint(len(val)) + bytes(val)
        else:
            out += bytes(val)
    return bytes(out)


def get(buf, path):
    """First value at a field path (tuple of field numbers), or None."""
    msg = parse(buf)
    for f, w, v in msg:
        if f == path[0]:
            if len(path) == 1:
                return v
            if w == 2:
                return get(v, path[1:])
    return None


def set_field(buf, path, value, wire=2):
    """Replace (or append) the field at path; intermediate messages are created if missing."""
    msg = parse(buf)
    for item in msg:
        if item[0] == path[0]:
            if len(path) == 1:
                item[1], item[2] = wire, value
            else:
                item[2] = set_field(item[2], path[1:], value, wire)
            return encode(msg)
    if len(path) == 1:
        msg.append([path[0], wire, value])
    else:
        msg.append([path[0], 2, set_field(b"", path[1:], value, wire)])
    return encode(msg)


def drop_field(buf, path):
    msg = parse(buf)
    if len(path) == 1:
        return encode([m for m in msg if m[0] != path[0]])
    for item in msg:
        if item[0] == path[0] and item[1] == 2:
            item[2] = drop_field(item[2], path[1:])
    return encode(msg)


def swap_strings(buf, mapping, depth=0):
    """Return buf with every string field equal to (or containing) a mapping key replaced.

    Only fields that change are re-encoded; everything else keeps its exact bytes.
    """
    if depth > 40 or not mapping:
        return buf
    try:
        msg = parse(buf)
    except ValueError:
        return buf
    changed = False
    for item in msg:
        if item[1] != 2:
            continue
        raw = item[2]
        text = None
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            pass
        if text is not None and any(k in text for k in mapping):
            new = text
            for old, repl in mapping.items():
                new = new.replace(old, repl)
            if new != text and _is_text(text):
                item[2] = new.encode("utf-8")
                changed = True
                continue
        sub = swap_strings(raw, mapping, depth + 1)
        if sub != raw:
            item[2] = sub
            changed = True
    return encode(msg) if changed else buf


def _is_text(s):
    return all(c.isprintable() or c in "\n\r\t" for c in s)


def _ts(seconds):
    sec = int(seconds)
    return encode([[1, 0, sec], [2, 0, int(round((seconds - sec) * 1e9))]])


# ---------------------------------------------------------------- store

USER_STEP, REPLY_STEP = 14, 15
# Field paths measured in step_payload (see notes).
P_META = (5,)
P_USER_TEXT = (19, 2)
P_USER_ITEMS = (19, 3)
P_REPLY = (20,)
P_REPLY_TEXT = (20, 1)
P_REPLY_TEXT2 = (20, 8)
P_REPLY_THINKING = (20, 3)
P_REPLY_SIGNATURE = (20, 14)
P_REPLY_TOOLCALL = (20, 7)
P_REPLY_BOT = (20, 6)
M_TIME, M_STEP_INDEX, M_GEN_INDEX, M_STEP_UID = (1,), (20, 2), (20, 3), (35,)
M_START, M_END, M_GEN_INFO = (6,), (7,), (9,)


def _open_copy(path, tmpdir):
    dst = os.path.join(tmpdir, os.path.basename(path))
    shutil.copyfile(path, dst)
    return sqlite3.connect("file:%s?immutable=1" % dst, uri=True)


def _find_template(store_dir, tmpdir):
    files = sorted(glob.glob(os.path.join(store_dir, "*.db")), key=os.path.getmtime, reverse=True)
    for path in files[:80]:
        try:
            con = _open_copy(path, tmpdir)
            rows = con.execute(
                "SELECT idx, step_type, step_payload FROM steps WHERE step_type IN (?, ?) ORDER BY idx",
                (USER_STEP, REPLY_STEP),
            ).fetchall()
        except (sqlite3.Error, OSError):
            continue
        user = next((r for r in rows if r[1] == USER_STEP and r[2]), None)
        reply = None
        for r in reversed(rows):
            if r[1] != REPLY_STEP or not r[2]:
                continue
            try:
                if get(r[2], P_REPLY_TEXT) and get(r[2], P_REPLY_TOOLCALL) is None:
                    reply = r
                    break
            except ValueError:
                continue
        if user and reply:
            return con, path, user[2], reply[2]
        con.close()
    raise RuntimeError("no local conversation with a user step and a text reply to use as a template")


def _ids(con):
    traj_id, cascade_id = con.execute("SELECT trajectory_id, cascade_id FROM trajectory_meta").fetchone()
    blob = con.execute("SELECT data FROM trajectory_metadata_blob").fetchone()[0]
    executor = con.execute("SELECT data FROM executor_metadata ORDER BY idx LIMIT 1").fetchone()
    workspace = get(blob, (1, 1))
    ids = {
        "trajectory": traj_id,
        "cascade": cascade_id,
        "blob_id": (get(blob, (3,)) or b"").decode() or None,
        "executor": (get(executor[0], (9,)) or b"").decode() if executor else None,
        "workspace_uri": workspace.decode() if workspace else None,
        "branch": ((get(blob, (1, 4)) or b"").decode() or None),
    }
    return ids


def _reply_text(turn):
    from transplant import writer
    return writer.render_assistant_text(turn)


def write_session(adapter, conv, *, cwd, model="", extra=None):
    extra = dict(extra or {})
    session_id = extra.get("session_id") or str(uuid.uuid4())
    store_dir = extra.get("store_dir") or os.path.dirname(adapter.store_paths(session_id=session_id)[0])
    template_dir = extra.get("template_dir") or store_dir
    dest = os.path.join(store_dir, "%s.db" % session_id)
    if os.path.exists(dest):
        raise FileExistsError("refusing to overwrite existing conversation: %s" % dest)
    turns = [t for t in (conv.get("turns") or []) if t.get("role") in ("user", "assistant")]
    if not turns:
        raise ValueError("nothing to write")

    tmpdir = tempfile.mkdtemp(prefix="transplant-")
    try:
        con, template_path, user_tpl, reply_tpl = _find_template(template_dir, tmpdir)
        old = _ids(con)
        new_ws = "file://" + cwd
        mapping = {old["cascade"]: session_id, old["trajectory"]: str(uuid.uuid4())}
        if old["blob_id"]:
            mapping[old["blob_id"]] = str(uuid.uuid4())
        if old["executor"]:
            mapping[old["executor"]] = str(uuid.uuid4())
        if old["workspace_uri"] and old["workspace_uri"] != new_ws:
            mapping[old["workspace_uri"]] = new_ws
            old_path = old["workspace_uri"][len("file://"):]
            if old_path and old_path != cwd:
                mapping[old_path] = cwd
        branch = (conv.get("git") or {}).get("branch")
        if branch and old["branch"] and branch != old["branch"]:
            mapping[old["branch"]] = branch

        tmp = dest + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        out = sqlite3.connect(tmp)
        out.execute("PRAGMA secure_delete = ON")
        try:
            for (sql,) in con.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END"
            ):
                out.execute(sql)
            row = con.execute("SELECT trajectory_id, cascade_id, trajectory_type, source FROM trajectory_meta").fetchone()
            out.execute("INSERT INTO trajectory_meta VALUES (?, ?, ?, ?)",
                        (mapping[old["trajectory"]], session_id, row[2], row[3]))
            for rid, data in con.execute("SELECT id, data FROM trajectory_metadata_blob"):
                out.execute("INSERT INTO trajectory_metadata_blob VALUES (?, ?)", (rid, swap_strings(data, mapping)))
            for idx, data in con.execute("SELECT idx, data FROM executor_metadata"):
                out.execute("INSERT INTO executor_metadata VALUES (?, ?)", (idx, swap_strings(data, mapping)))

            user_tpl = swap_strings(user_tpl, mapping)
            reply_tpl = swap_strings(reply_tpl, mapping)
            cols = [r[1] for r in con.execute("PRAGMA table_info(steps)")]
            tpl_rows = {
                st: dict(zip(cols, con.execute("SELECT * FROM steps WHERE step_type = ? ORDER BY idx LIMIT 1",
                                               (st,)).fetchone()))
                for st in (USER_STEP, REPLY_STEP)
            }
            now = time.time()
            prev = 0.0
            gen = 0
            for idx, turn in enumerate(turns):
                ts = float(turn.get("ts") or 0) or now
                ts = max(ts, prev + 0.001)
                prev = ts
                if turn["role"] == "user":
                    payload = user_tpl
                    text = (turn.get("text") or "").encode("utf-8")
                    payload = set_field(payload, P_USER_TEXT, text)
                    payload = set_field(payload, P_USER_ITEMS, encode([[1, 2, text]]))
                    step_type = USER_STEP
                else:
                    payload = reply_tpl
                    text = _reply_text(turn).encode("utf-8")
                    payload = set_field(payload, P_REPLY_TEXT, text)
                    payload = set_field(payload, P_REPLY_TEXT2, text)
                    payload = drop_field(payload, P_REPLY_THINKING)
                    payload = drop_field(payload, P_REPLY_SIGNATURE)
                    payload = set_field(payload, P_REPLY_BOT, ("bot-%s" % uuid.uuid4()).encode())
                    step_type = REPLY_STEP
                meta = get(payload, P_META) or b""
                meta = set_field(meta, M_TIME, _ts(ts))
                meta = set_field(meta, M_STEP_UID, uuid.uuid4().hex.encode())
                if step_type == REPLY_STEP:
                    meta = set_field(meta, M_START, _ts(ts))
                    meta = set_field(meta, M_END, _ts(ts))
                    meta = set_field(meta, M_STEP_INDEX, idx, wire=0)
                    if gen:
                        meta = set_field(meta, M_GEN_INDEX, gen, wire=0)
                    else:
                        meta = drop_field(meta, M_GEN_INDEX)
                    gen += 1
                payload = set_field(payload, P_META, meta)
                row = dict(tpl_rows[step_type])
                row.update({"idx": idx, "step_type": step_type, "step_payload": payload, "metadata": meta})
                out.execute(
                    "INSERT INTO steps (%s) VALUES (%s)" % (", ".join(cols), ", ".join("?" * len(cols))),
                    [row[c] for c in cols],
                )
            out.commit()
            out.execute("VACUUM")  # no template bytes left in page slack
        finally:
            out.close()
            con.close()
        os.replace(tmp, dest)
    except BaseException:
        if os.path.exists(dest + ".tmp"):
            os.remove(dest + ".tmp")
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return {"session_id": session_id, "path": dest, "records": len(turns), "template": os.path.basename(template_path)}
