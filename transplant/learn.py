"""Learning session stores for providers that have no adapter yet (plan section 5).

  detect   -> unmapped providers (catalog ids or installed binaries without an adapter)
  locate   -> where a CLI keeps its sessions, by evidence (files under per-OS data dirs named
              after the binary, newest first, optionally only those changed after a known run)
  sample   -> ONE trimmed session for a drafting worker (long strings cut, few records)
  brief    -> the worker prompt: adapter schema + sample + resume flags from --help
  promote  -> a drafted adapter is checked, live-probed, and only then moved into the learned
              adapters dir; a failure keeps the primer and is logged in learn_audit.jsonl
  drift    -> a mapped adapter whose newest real session no longer matches its fingerprint

No provider names appear here: everything is discovered or comes from adapter data.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time

from transplant import adapter as adapter_mod
from transplant import probe, registry

SESSION_EXTS = (".jsonl", ".json", ".db", ".sqlite", ".sqlite3")
MAX_DEPTH = 6
MAX_FILES = 4000
MAX_CANDIDATES = 12
SAMPLE_RECORDS = 24
SAMPLE_STR = 240
SAMPLE_ROWS = 3
HELP_TIMEOUT = 15
_SESSION_WORDS = ("session", "message", "conversation", "thread", "turn", "chat", "rollout", "history", "transcript")
_RECORD_KEYS = ("role", "message", "messages", "content", "parentUuid", "parentId", "turn", "author")
_SKIP_DIRS = ("node_modules", ".git", "cache", "Cache", "logs", "packages", "releases", "skills", "extensions", "plugins", "bin")
_FLAG_WORDS = ("resume", "session", "continue", "conversation", "print", "prompt", "model", "effort", "output")


def _state_dir():
    return os.path.dirname(registry.learned_dir())


def drafts_dir():
    return os.path.join(_state_dir(), "adapter_drafts")


def audit_path():
    return os.path.join(_state_dir(), "learn_audit.jsonl")


def audit(event, provider, **fields):
    rec = {"ts": time.time(), "event": event, "provider": provider}
    rec.update(fields)
    os.makedirs(os.path.dirname(audit_path()), exist_ok=True)
    with open(audit_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# ---------------------------------------------------------------- detect


def unmapped(binaries=None):
    """Provider ids with no adapter: the live catalog's, plus any given installed binaries."""
    known = registry.adapters()
    out = [pid for pid in registry.known_providers() if pid not in known]
    for name in binaries or []:
        if name and name not in known and name not in out:
            out.append(name)
    return out


# ---------------------------------------------------------------- locate


def _tokens(name):
    base = os.path.basename(str(name or "")).lower()
    base = re.sub(r"\.(exe|cmd|bat)$", "", base)
    parts = [p for p in re.split(r"[^a-z0-9]+", base) if len(p) >= 2]
    return [base] + [p for p in parts if p != base]


def search_roots(name, home=None):
    """Per-OS data dirs whose name matches the binary (dot-dirs in home, xdg, appdata)."""
    pv = adapter_mod.path_vars()
    home = home or pv["home"]
    bases = [home, pv.get("xdg_data"), pv.get("xdg_config"), pv.get("appdata"), pv.get("localappdata"),
             os.path.join(home, "Library", "Application Support")]
    toks = _tokens(name)
    roots = []
    for base in bases:
        if not base or not os.path.isdir(base):
            continue
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for entry in entries:
            low = entry.lower().lstrip(".")
            if any(low == t or low.startswith(t + "-") or low.startswith(t + "_") or low.startswith(t) and len(t) >= 4 for t in toks):
                path = os.path.join(base, entry)
                if os.path.isdir(path) and path not in roots:
                    roots.append(path)
    return roots


def locate(name, since=None, home=None, roots=None):
    """Candidate session files for a CLI, newest first: [{path, kind, mtime, size}]."""
    found = []
    seen = 0
    for root in roots if roots is not None else search_roots(name, home):
        base_depth = root.rstrip(os.sep).count(os.sep)
        for d, dirs, files in os.walk(root):
            if d.count(os.sep) - base_depth >= MAX_DEPTH:
                dirs[:] = []
            dirs[:] = [x for x in dirs if x not in _SKIP_DIRS]
            for f in files:
                seen += 1
                if seen > MAX_FILES:
                    break
                if not f.lower().endswith(SESSION_EXTS) or f.endswith((".tmp", "-wal", "-shm")):
                    continue
                p = os.path.join(d, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if since and st.st_mtime < since:
                    continue
                if st.st_size == 0:
                    continue
                kind = _kind(p)
                if not _session_like(p, kind):
                    continue
                found.append({"path": p, "kind": kind, "mtime": st.st_mtime, "size": st.st_size})
    found.sort(key=lambda c: c["mtime"], reverse=True)
    return found[:MAX_CANDIDATES]


def _session_like(path, kind):
    """Does this file hold conversation data? (records with role/content keys, or
    tables named like sessions/messages). Cheap: 8KB of text, or table names only."""
    if kind == "sqlite":
        try:
            con = sqlite3.connect("file:%s?immutable=1" % path, uri=True)
            names = [r[0].lower() for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            con.close()
        except sqlite3.Error:
            return False
        return any(w in n for n in names for w in _SESSION_WORDS)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(8192)
    except OSError:
        return False
    keys = set(re.findall(r'"([A-Za-z_]+)"\s*:', head))
    return bool(keys & set(_RECORD_KEYS))


def _kind(path):
    low = path.lower()
    if low.endswith(".jsonl"):
        return "jsonl"
    if low.endswith(".json"):
        return "json"
    return "sqlite"


def help_flags(binary, runner=subprocess.run):
    """Lines of `<bin> --help` that mention sessions, resuming, printing, model or effort."""
    try:
        proc = runner([binary, "--help"], capture_output=True, text=True, timeout=HELP_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return []
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and (s.startswith("-") or re.match(r"^[a-z][a-z-]+\s{2,}", s)) and any(w in s.lower() for w in _FLAG_WORDS):
            out.append(s[:200])
    return out[:40]


# ---------------------------------------------------------------- sample


def _trim(value, depth=0):
    if isinstance(value, str):
        return value if len(value) <= SAMPLE_STR else value[:SAMPLE_STR] + "...[%d chars]" % len(value)
    if isinstance(value, list):
        items = [_trim(v, depth + 1) for v in value[:6]]
        if len(value) > 6:
            items.append("...[%d more]" % (len(value) - 6))
        return items
    if isinstance(value, dict):
        return {k: _trim(v, depth + 1) for k, v in list(value.items())[:40]} if depth < 8 else "{...}"
    return value


def sample(path, kind=None):
    """One session, trimmed for a drafting worker. Read-only (sqlite via a private copy)."""
    kind = kind or _kind(path)
    if kind == "jsonl":
        recs = []
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        head, tail = lines[: SAMPLE_RECORDS // 2], lines[-(SAMPLE_RECORDS // 2):] if len(lines) > SAMPLE_RECORDS else []
        for line in head + tail:
            try:
                recs.append(_trim(json.loads(line)))
            except ValueError:
                continue
        return {"kind": "jsonl", "path": path, "records": len(lines), "sample": recs}
    if kind == "json":
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            try:
                data = json.load(fh)
            except ValueError:
                data = None
        return {"kind": "json", "path": path, "sample": _trim(data)}
    tmpdir = tempfile.mkdtemp(prefix="learn-")
    try:
        copy = os.path.join(tmpdir, "s.db")
        shutil.copyfile(path, copy)
        con = sqlite3.connect("file:%s?immutable=1" % copy, uri=True)
        tables = {}
        for name, sql in con.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            try:
                rows = con.execute('SELECT * FROM "%s" LIMIT %d' % (name.replace('"', '""'), SAMPLE_ROWS)).fetchall()
                count = con.execute('SELECT count(*) FROM "%s"' % name.replace('"', '""')).fetchone()[0]
            except sqlite3.Error:
                rows, count = [], 0
            tables[name] = {"sql": sql, "count": count,
                            "rows": [[_trim(v if not isinstance(v, bytes) else "<%d bytes>" % len(v)) for v in r] for r in rows]}
        con.close()
        return {"kind": "sqlite", "path": path, "tables": tables}
    except (OSError, sqlite3.Error) as exc:
        return {"kind": "sqlite", "path": path, "error": str(exc)}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------- brief


def brief(provider, binary, candidates, flags, sample_data):
    """The drafting worker's whole prompt: it sees the schema, ONE sample and the flags, nothing else."""
    shipped = registry.adapters()
    example = next((a.data for a in shipped.values() if a.kind in ("jsonl", "json")), None)
    out_path = os.path.join(drafts_dir(), "%s.json" % provider)
    lines = [
        "Draft a session-store adapter for the CLI `%s` (binary %s)." % (provider, binary),
        "Write ONE JSON file to %s and nothing else. Do not read other files or run the CLI." % out_path,
        "",
        "Adapter schema: keys %s (required: %s). store.kind one of %s." % (
            sorted(adapter_mod.ALLOWED_KEYS), sorted(adapter_mod.REQUIRED_KEYS), sorted(adapter_mod.STORE_KINDS)),
        "store.path: templates with {home} {xdg_data} {xdg_config} {appdata} {localappdata} {session_id} {project_slug} {ts_file}.",
        "write.turn.user / write.turn.assistant: one record template per turn, placeholders {uuid} {parent_uuid} {ts_iso} {ts_ms} {text} {model} {cwd} {session_id}.",
        "write.header / write.footer: optional record lists. write.session_id: uuid4 | uuid7 | hex8 | prefix:<p>.",
        "resume.argv MUST start with \"{bin}\" and contain {session_id} and {text}; model_flag/effort_flag optional.",
        "resume.output (optional): when the CLI has a JSON/JSONL output flag, put the flag in argv and add {\"format\": \"jsonl\", \"match\": {\"type\": \"<assistant event type>\"}, \"text\": \"<dotted path to the reply text>\"} so banners and usage lines are dropped.",
        "store.fingerprint.sample_keys: dotted keys every real record has (for drift detection).",
        "No \"hook\" key (hooks are shipped code only). Put what you inferred and anything uncertain in \"notes\".",
        "",
        "Candidate session files (newest first): %s" % json.dumps([c["path"] for c in candidates[:6]]),
        "Resume-related --help lines:", *("  " + f for f in flags),
        "",
        "ONE sample session (trimmed):",
        json.dumps(sample_data, ensure_ascii=False, indent=1)[:12000],
    ]
    if example:
        lines += ["", "A working adapter for another CLI, as a format example:", json.dumps(example, indent=1)[:5000]]
    return "\n".join(lines)


SAMPLE_TRIES = 8


def _has_turns(data):
    """Does a sample hold at least one record (an empty store teaches the drafter nothing)?"""
    if not data or data.get("error"):
        return False
    if data["kind"] == "jsonl":
        return bool(data.get("sample"))
    if data["kind"] == "json":
        return data.get("sample") not in (None, {}, [])
    return any(t["count"] for n, t in (data.get("tables") or {}).items()
               if any(w in n.lower() for w in _SESSION_WORDS) and not n.lower().endswith(_FTS_SHADOW)
               and "schema" not in n.lower() and "migration" not in n.lower())


# Full-text-search bookkeeping (message_search_data...) and migration logs
# (refinery_schema_history) hold no turns.
_FTS_SHADOW = ("_data", "_idx", "_docsize", "_config", "_content", "_segments", "_segdir", "_stat")


def evidence(provider, binary, since=None, home=None, runner=subprocess.run):
    """Everything a drafting worker needs, or a reason it can't be drafted yet."""
    cands = locate(os.path.basename(binary) if binary else provider, since=since, home=home)
    if not cands and binary and os.path.basename(binary) != provider:
        cands = locate(provider, since=since, home=home)
    if not cands:
        return {"ok": False, "reason": "no session files found for %s" % provider}
    flags = help_flags(binary, runner=runner) if binary else []
    data = None
    for cand in cands[:SAMPLE_TRIES]:  # newest store that actually holds a conversation
        data = sample(cand["path"], cand["kind"])
        if _has_turns(data):
            break
    else:
        return {"ok": False, "candidates": cands, "flags": flags,
                "reason": "session files for %s are empty; run one session first" % provider}
    return {"ok": True, "candidates": cands, "flags": flags, "sample": data,
            "brief": brief(provider, binary, cands, flags, data)}


# ---------------------------------------------------------------- promote


def check_draft(data):
    """Reasons a drafted adapter can't be trusted; empty when it may be probed."""
    problems = []
    try:
        ad = adapter_mod.Adapter.from_dict(data, origin="learned")
    except adapter_mod.AdapterError as exc:
        return ["schema: %s" % exc]
    if "hook" in data:
        problems.append("learned adapters may not name a hook")
    argv = (data.get("resume") or {}).get("argv") or []
    if not argv or argv[0] != "{bin}":
        problems.append("resume.argv must start with {bin}")
    joined = " ".join(str(a) for a in argv)
    if "{session_id}" not in joined or "{text}" not in joined:
        problems.append("resume.argv must contain {session_id} and {text}")
    if ad.kind not in ("jsonl", "json"):
        problems.append("store kind %s needs a shipped hook" % ad.kind)
    return problems


def promote(provider, conv, *, binary, cwd, model="", runner=None, draft_path=None):
    """Probe a drafted adapter; install it into the learned dir only if the probe passes."""
    draft_path = draft_path or os.path.join(drafts_dir(), "%s.json" % provider)
    try:
        with open(draft_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        audit("promote_failed", provider, reason="draft unreadable: %s" % exc)
        return False, "draft unreadable: %s" % exc
    problems = check_draft(data)
    if problems:
        audit("promote_failed", provider, reason="; ".join(problems))
        return False, "; ".join(problems)
    ad = adapter_mod.Adapter.from_dict(data, origin="learned")
    kw = {"bin_path": binary, "cwd": cwd, "model": model, "force": True}
    if runner is not None:
        kw["runner"] = runner
    info = None
    try:
        ok, detail, info = probe.probe_adapter(ad, conv, **kw)
    except Exception as exc:
        ok, detail = False, "probe error: %s" % exc
    if info and info.get("session_id"):
        from transplant import engine
        engine.mark_launched(info["session_id"], "learn probe")
    if not ok:
        audit("promote_failed", provider, reason="probe failed", detail=str(detail)[-300:])
        return False, "probe failed: %s" % str(detail)[-160:]
    dest_dir = registry.learned_dir()
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "%s.json" % ad.id)
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, dest)
    audit("promoted", provider, path=dest)
    return True, dest


# ---------------------------------------------------------------- drift


def drifted(ad, home=None):
    """(drifted, detail): the newest real session for this adapter's store no longer matches."""
    fp = (ad.data.get("store") or {}).get("fingerprint")
    if not isinstance(fp, dict):
        return False, "no fingerprint"
    roots = []
    marker = "\x00"
    home_norm = os.path.normpath(os.path.expanduser(home or "~"))
    for path in ad.store_paths(session_id=marker, project_slug=marker, ts_file=marker):
        head = path.split(marker)[0]
        while head and not os.path.isdir(head):
            head = os.path.dirname(head.rstrip(os.sep))
        if head and os.path.normpath(head) != home_norm and head not in roots and head != os.path.dirname(head):
            roots.append(head)
    files = locate(ad.id, roots=roots) if roots else []
    files = [f for f in files if f["kind"] == ("sqlite" if ad.kind == "sqlite" else f["kind"])]
    if not files:
        return False, "no real session to compare"
    newest = files[0]
    if ad.kind == "sqlite":
        s = sample(newest["path"], "sqlite")
        ok = ad.fingerprint_ok(tables=set((s.get("tables") or {}).keys()))
    else:
        s = sample(newest["path"], newest["kind"])
        recs = s.get("sample") or []
        ok = any(ad.fingerprint_ok(sample=r) for r in recs if isinstance(r, dict)) if isinstance(recs, list) else ad.fingerprint_ok(sample=recs)
    return (not ok), newest["path"]


def prepare(provider, binary=None, since=None):
    """Gather evidence and write the drafting brief to adapter_drafts/<id>.brief.md.

    Spinning the drafting worker is left to the caller: it costs a worker, so it needs the
    user's approval (HUD "Map this provider", or spin_worker with this brief)."""
    binary = binary or shutil.which(provider)
    ev = evidence(provider, binary, since=since)
    if not ev.get("ok"):
        audit("prepare_failed", provider, reason=ev.get("reason"))
        return ev
    os.makedirs(drafts_dir(), exist_ok=True)
    path = os.path.join(drafts_dir(), "%s.brief.md" % provider)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(ev["brief"])
    audit("prepared", provider, brief=path, sample=ev["sample"].get("path"))
    ev["brief_path"] = path
    return ev


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        sys.exit("usage: python3 -m transplant.learn <provider> [binary]")
    res = prepare(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    if not res.get("ok"):
        sys.exit(res.get("reason"))
    print("brief:", res["brief_path"])
    print("sample:", res["sample"].get("path"))
    print("candidates:", len(res["candidates"]), "| help lines:", len(res["flags"]))
