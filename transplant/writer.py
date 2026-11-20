# Generic session writer: providers are data, the writer is provider-agnostic.

import datetime
import importlib
import json
import os
import random
import re
import string
import time
import uuid

from transplant import template
from transplant.ir import norm_ts

NO_TEXT = "(no reply text)"
_HOOK_RE = re.compile(r"^[a-z0-9_]+$")
_HEX = "0123456789abcdef"
_PREFIX_LEN = 26


def project_slug(adapter, cwd):
    """Slug for a cwd: store.slug rewrite when present, else the basename."""
    slug = (adapter.data.get("store") or {}).get("slug")
    if isinstance(slug, dict) and slug.get("pattern"):
        return re.sub(slug["pattern"], slug["repl"], cwd)
    return os.path.basename(cwd.rstrip("/\\") or cwd)


def _action_path(args):
    """Best-effort file path from an action args payload."""
    if not args:
        return None
    try:
        data = json.loads(args)
        if isinstance(data, dict):
            for key in ("file_path", "path", "filePath", "filename", "target_file"):
                val = data.get(key)
                if val:
                    return str(val)
    except Exception:
        pass
    if isinstance(args, str):
        token = args.split(None, 1)[0] if args.split() else ""
        if "/" in token or "." in token:
            return token
    return None


def action_summary(actions, files=None):
    """Compact marker for prior provider actions, or '' when nothing."""
    read_paths, mod_paths = [], []
    seen_read, seen_mod = set(), set()
    run_count = 0
    other_counts = {}
    other_order = []
    saw_mod_tool = False
    for action in actions or []:
        name = (action.get("tool") or "")
        lower = name.lower()
        path = _action_path(action.get("args", ""))
        if any(k in lower for k in ("read", "view", "cat", "open")):
            if path and path not in seen_read:
                seen_read.add(path)
                read_paths.append(path)
        elif any(k in lower for k in ("edit", "write", "patch", "replace", "create")):
            saw_mod_tool = True
            if path and path not in seen_mod:
                seen_mod.add(path)
                mod_paths.append(path)
        elif any(k in lower for k in ("bash", "shell", "run", "exec", "terminal", "command")):
            run_count += 1
        else:
            key = lower or name
            if key not in other_counts:
                other_counts[key] = 0
                other_order.append(key)
            other_counts[key] += 1

    for fp in files or []:
        if not fp:
            continue
        if saw_mod_tool and fp not in seen_mod and fp not in seen_read:
            seen_mod.add(fp)
            mod_paths.append(fp)
        elif fp not in seen_read and fp not in seen_mod:
            seen_read.add(fp)
            read_paths.append(fp)

    def fmt_paths(paths):
        shown = paths[:6]
        extra = len(paths) - len(shown)
        if extra > 0:
            shown = shown + ["+%d more" % extra]
        return ", ".join(shown)

    groups = []
    if read_paths:
        groups.append("Read " + fmt_paths(read_paths))
    if mod_paths:
        groups.append("Modified " + fmt_paths(mod_paths))
    if run_count:
        groups.append("Ran %d command%s" % (run_count, "" if run_count == 1 else "s"))
    for key in other_order:
        n = other_counts[key]
        groups.append("Other: %s%s" % (key, "" if n == 1 else " \u00d7%d" % n))
    if not groups:
        return ""
    return "[Prior provider actions: %s]" % "; ".join(groups)


def render_assistant_text(turn):
    """Turn text, then a compact marker line for actions when present."""
    parts = [turn.get("text", "")]
    summary = action_summary(turn.get("actions") or [], files=turn.get("files") or [])
    if summary:
        parts.append(summary)
    text = "\n\n".join(parts)
    return text if text.strip() else NO_TEXT


def _ts_context(conv):
    """Base timestamp context from the first turn's ts (or now)."""
    turns = conv.get("turns") or []
    ts = norm_ts(turns[0].get("ts")) if turns else None
    if ts:
        dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    else:
        dt = datetime.datetime.now(datetime.timezone.utc)
    return {
        "ts_file": dt.strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3] + "Z",
        "ts_iso": dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "ts_ms": int(dt.timestamp() * 1000),
    }


def build_records(adapter, conv, *, session_id, cwd, model="", extra=None):
    """Render write.header / write.turn / write.footer into flat records."""
    extra = dict(extra or {})
    spec = adapter.data.get("write") or {}
    slug = project_slug(adapter, cwd)
    ctx = {
        "session_id": session_id,
        "cwd": cwd,
        "model": model,
        "title": conv.get("title", ""),
        "project_slug": slug,
        "home": os.path.expanduser("~"),
        "version": extra.get("version", ""),
        "git_branch": (conv.get("git") or {}).get("branch", ""),
    }
    ctx.update(_ts_context(conv))
    ctx.update(extra)

    records = []
    header_ctx = dict(ctx)
    header_ctx["uuid"] = template.new_uuid()
    header_ctx["parent_uuid"] = None
    for record in spec.get("header") or []:
        records.append(template.render(record, header_ctx))

    last_uuid = None
    first_uuid = None
    for index, turn in enumerate(conv.get("turns") or []):
        turn_ctx = dict(ctx)
        if turn.get("role") == "user":
            turn_ctx["text"] = turn.get("text", "")
        else:
            turn_ctx["text"] = render_assistant_text(turn)
        turn_ctx["uuid"] = _new_id(
            spec.get("record_id"), "write.record_id"
        )
        turn_ctx["parent_uuid"] = last_uuid
        turn_ctx["index"] = index
        turn_ctx["role"] = turn.get("role", "")
        ts = norm_ts(turn.get("ts"))
        if ts:
            dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
        else:
            dt = datetime.datetime.now(datetime.timezone.utc)
        turn_ctx["ts_iso"] = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        turn_ctx["ts_ms"] = int(dt.timestamp() * 1000)
        turn_tpl = (spec.get("turn") or {}).get(turn.get("role", ""))
        if turn_tpl:
            records.append(template.render(turn_tpl, turn_ctx))
        last_uuid = turn_ctx["uuid"]
        if first_uuid is None:
            first_uuid = turn_ctx["uuid"]

    footer_ctx = dict(ctx)
    footer_ctx["leaf_uuid"] = last_uuid
    footer_ctx["first_uuid"] = first_uuid
    for record in spec.get("footer") or []:
        records.append(template.render(record, footer_ctx))
    return records


def _uuid7():
    """Time-ordered uuid7: 48-bit ms timestamp, version 7, random."""
    ms = int(time.time_ns() // 1_000_000) & ((1 << 48) - 1)
    value = (
        (ms << 80)
        | (7 << 76)
        | (random.getrandbits(12) << 64)
        | (2 << 62)
        | random.getrandbits(62)
    )
    return str(uuid.UUID(int=value))


def _new_id(spec, what="write.session_id"):
    """New id per spec: "uuid4", "uuid7", "hex8" or "prefix:<p>"."""
    spec = spec or "uuid4"
    if spec == "uuid4":
        return template.new_uuid()
    if spec == "uuid7":
        return _uuid7()
    if spec == "hex8":
        return "".join(random.choice(_HEX) for _ in range(8))
    if isinstance(spec, str) and spec.startswith("prefix:"):
        alphabet = string.ascii_lowercase + string.digits
        suffix = "".join(random.choice(alphabet) for _ in range(_PREFIX_LEN))
        return spec[len("prefix:"):] + suffix
    raise ValueError("unsupported %s spec: %r" % (what, spec))


def _new_session_id(spec):
    """Session id per write.session_id spec."""
    return _new_id(spec, "write.session_id")


def _hook_write_session(adapter, conv, *, cwd, model, extra):
    """Dispatch to adapter.data["hook"] module; it owns the write.

    Returns (handled, result): handled is False when no hook is set.
    """
    hook = adapter.data.get("hook")
    if not isinstance(hook, str) or not hook:
        return False, None
    if not _HOOK_RE.match(hook):
        raise ValueError("invalid write hook name: %r" % (hook,))
    mod = importlib.import_module("transplant.adapters." + hook)
    return True, mod.write_session(adapter, conv, cwd=cwd, model=model, extra=extra)


def write_session(adapter, conv, *, cwd, model="", extra=None):
    """Write a new session file; never patch or overwrite an existing one."""
    handled, dispatched = _hook_write_session(
        adapter, conv, cwd=cwd, model=model, extra=extra
    )
    if handled:
        return dispatched
    kind = adapter.kind
    if kind not in ("jsonl", "json"):
        raise NotImplementedError("hook required for kind %s" % kind)
    spec = adapter.data.get("write") or {}
    session_id = _new_session_id(spec.get("session_id"))
    slug = project_slug(adapter, cwd)
    path = adapter.store_paths(
        session_id=session_id,
        project_slug=slug,
        **_ts_context(conv),
    )[0]
    if os.path.exists(path):
        raise FileExistsError(
            "refusing to overwrite existing session file: %s" % path
        )
    records = build_records(
        adapter, conv, session_id=session_id, cwd=cwd, model=model, extra=extra
    )

    parent = os.path.dirname(path)
    dir_mode = spec.get("dir_mode")
    if dir_mode is None:
        os.makedirs(parent, exist_ok=True)
    else:
        os.makedirs(parent, dir_mode, exist_ok=True)

    tmp = path + ".tmp"
    try:
        if kind == "jsonl":
            payload = "\n".join(
                json.dumps(r, ensure_ascii=False) for r in records
            )
            if records:
                payload += "\n"
        else:
            payload = json.dumps(records, ensure_ascii=False)
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return {"session_id": session_id, "path": path, "records": len(records)}
