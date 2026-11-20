"""Provider transplant engine (roadmap 73, plan section 6).

A conversation is one id that owns provider segments. Moving it to a provider that has no
segment yet transplants the recent history into that provider's native session store and
resumes it there. Moving back to a provider that already has a segment sends only what
happened since, through that provider's own resume command.

No provider names appear here: providers are adapter data (transplant/adapters/*.json).
Source sessions are only ever read.
"""

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from transplant import ir, probe, registry, writer


def _load_provider_data():
    path = os.path.join(os.path.dirname(__file__), "provider_data.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


_PROVIDER_DATA = _load_provider_data()

MIN_K, MAX_K, DEFAULT_K = 5, 20, 12
CHARS_PER_TOKEN = 4
CHARS_PER_EXCHANGE = 1500
CONTEXT_SHARE = 0.25
EARLY_EXIT_SECONDS = 2.0
PROBE_RETRY_SECONDS = 3600
DELTA_MAX_CHARS = 24000
FULL_SHARE = 0.8  # a reused session past this share of the target's context gets a fresh transplant
REPLY_MAX_CHARS = 4000

NATIVE, TRANSPLANT, PRIMER = "native", "transplant", "primer"

_lock = threading.RLock()
_runs = {}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# Signature-based caches (no TTL): safe because callers must not observe stale data
# when the backing files change, and _save/_load mutations are isolated.
_conv_cache = {"sig": None, "data": None}
_native_turns_cache = {}   # sid -> {"sig": ..., "turns": [...]}
_reply_cache = {}          # (path, sig, offset, stop, provider) -> text
_merge_cache = {}          # chat_id -> {"key": ..., "result": (...)}


# ---------------------------------------------------------------- state


def state_dir():
    """The host's state dir when it is loaded (tests patch it), else $SUBWAY_DIR, else ~/.subway-context."""
    host = sys.modules.get("server")
    path = getattr(host, "SUBWAY_DIR", None) if host is not None else None
    return path or os.environ.get("SUBWAY_DIR") or os.path.join(os.path.expanduser("~"), ".subway-context")


def _conv_path():
    return os.path.join(state_dir(), "conversations.json")


def _file_sig(path):
    if not path:
        return None
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _db_sig(provider):
    # Turns come from a provider SQLite DB; use the DB file signature for cache keys.
    db_sig = ((_PROVIDER_DATA.get("db_sig") or {}).get(provider or "") or {})
    dir_parts = db_sig.get("dir") or []
    if dir_parts:
        base = os.path.join(*(os.path.expanduser(dir_parts[0]),) + tuple(dir_parts[1:]))
    else:
        base = ""
    names = db_sig.get("names") or []
    for name in names:
        sig = _file_sig(os.path.join(base, name))
        if sig is not None:
            # SQLite in WAL mode writes new rows to <db>-wal first; the main file can stay unchanged until a checkpoint.
            return (sig, _file_sig(os.path.join(base, name + "-wal")))
    return _file_sig(base) if base else None


def _native_sig(path, provider):
    sig = _file_sig(path)
    if sig is not None:
        return sig
    prov = (provider or "").lower()
    if prov in (_PROVIDER_DATA.get("db_sig") or {}):
        return _db_sig(prov)
    return None


def _load():
    path = _conv_path()
    sig = _file_sig(path)
    with _lock:
        if sig is not None and _conv_cache.get("sig") == sig and _conv_cache.get("data") is not None:
            return copy.deepcopy(_conv_cache["data"])
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("conversations", {})
    data.setdefault("sessions", {})
    with _lock:
        _conv_cache["sig"] = _file_sig(path)
        _conv_cache["data"] = copy.deepcopy(data)
    return data


def _save(data):
    path = _conv_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    with _lock:
        _conv_cache["sig"] = _file_sig(path)
        _conv_cache["data"] = copy.deepcopy(data)
        _merge_cache.clear()


def _norm_id(chat_id):
    return str(chat_id or "").strip().lstrip("@")


def _session_key(chat_id, provider=""):
    sid = _norm_id(chat_id)
    prov = str(provider or "").strip()
    return f"{prov}:{sid}" if prov else sid


def _lookup_root(data, chat_id, provider=""):
    sid = _norm_id(chat_id)
    sessions = data["sessions"]
    root = None
    if provider:
        root = sessions.get(_session_key(sid, provider))
    if not root:
        root = sessions.get(sid)
    if not root and not provider:
        suffix = f":{sid}"
        for k, v in sessions.items():
            if k.endswith(suffix):
                root = v
                break
    return root


def conversation_for(chat_id, provider=""):
    """(root id, record) for any session that belongs to a conversation, else (None, None)."""
    data = _load()
    root = _lookup_root(data, chat_id, provider=provider)
    rec = data["conversations"].get(root) if root else None
    return (root, rec) if rec else (None, None)


def start_conversation(chat_id, provider, project_dir=""):
    """Create (or return) the conversation whose first segment is this native chat."""
    sid = _norm_id(chat_id)
    skey = _session_key(sid, provider)
    with _lock:
        data = _load()
        root = data["sessions"].get(skey) or (data["sessions"].get(sid) if not provider else None)
        if root and root in data["conversations"]:
            return root, data["conversations"][root]
        root = sid if sid not in data["conversations"] else skey
        rec = {
            "root": root,
            "project_dir": project_dir,
            "current": 0,
            "segments": [_segment(provider, sid, sid, NATIVE)],
        }
        data["conversations"][root] = rec
        data["sessions"][skey] = root
        _save(data)
        return root, rec


def _segment(provider, session_id, ref, method, **more):
    seg = {
        "provider": provider,
        "session_id": session_id,
        "ref": ref,
        "method": method,
        "start": time.time(),
        "end": None,
        "sent": [],
    }
    seg.update(more)
    return seg


def current_segment(rec):
    segs = rec.get("segments") or []
    i = rec.get("current", len(segs) - 1)
    return segs[i] if 0 <= i < len(segs) else (segs[-1] if segs else None)


def segment_for(rec, provider):
    """The most recent segment on this provider, or None."""
    for seg in reversed(rec.get("segments") or []):
        if seg.get("provider") == provider:
            return seg
    return None


def session_segment(chat_id):
    """(root, segment) when chat_id is a transplanted session (these may have no chat reader)."""
    root, rec = conversation_for(chat_id)
    if not rec:
        return None, None
    sid = _norm_id(chat_id)
    for seg in rec.get("segments") or []:
        if seg.get("session_id") == sid and seg.get("method") == TRANSPLANT:
            return root, seg
    return None, None


def add_segment(root, provider, session_id, ref, method, **more):
    """Close the current segment and make a new one current."""
    with _lock:
        data = _load()
        rec = data["conversations"][root]
        now = time.time()
        cur = current_segment(rec)
        if cur is not None and cur.get("end") is None:
            cur["end"] = now
        seg = _segment(provider, _norm_id(session_id), ref, method, **more)
        rec["segments"].append(seg)
        rec["current"] = len(rec["segments"]) - 1
        data["sessions"][_session_key(session_id, provider)] = root
        _save(data)
        return seg


def drop_segment(root, session_id):
    """Forget a segment whose session no longer exists (the next visit starts a new one)."""
    with _lock:
        data = _load()
        rec = data["conversations"].get(root)
        if not rec:
            return
        sid = _norm_id(session_id)
        keep = []
        dropped = []
        for s in rec["segments"]:
            if s.get("session_id") != sid or s.get("method") == NATIVE:
                keep.append(s)
            else:
                dropped.append(s)
        if dropped:
            cur = current_segment(rec)
            rec["segments"] = keep
            rec["current"] = keep.index(cur) if cur in keep else len(keep) - 1
            for s in dropped:
                data["sessions"].pop(_session_key(sid, s.get("provider", "")), None)
            if not any(s.get("session_id") == sid for s in keep):
                data["sessions"].pop(sid, None)
            _save(data)


def activate(root, session_id, at=None, provider=""):
    """Switch back: the existing segment for session_id becomes current again.

    `at` is when the switch began (before the message was sent), so the sent message
    falls inside the new segment and the old segment's delta window."""
    with _lock:
        data = _load()
        rec = data["conversations"][root]
        now = float(at) if at else time.time()
        sid = _norm_id(session_id)
        for i, seg in enumerate(rec["segments"]):
            if seg.get("session_id") == sid and (not provider or seg.get("provider") == provider):
                cur = current_segment(rec)
                if cur is not None and cur is not seg and cur.get("end") is None:
                    cur["end"] = now
                # A1: keep the resumed segment's previous end — it is the "you left here at"
                # timestamp the away-delta window needs. Nulling `end` alone used to make the
                # next visit re-send the whole segment history as a duplicate delta.
                prev_end = seg.get("end")
                if prev_end is not None:
                    seg["last_end"] = prev_end
                seg["end"] = None
                seg["resumed"] = now
                rec["current"] = i
                _save(data)
                return seg
    return None


def note_sent(root, session_id, text, log_path, offset=0):
    """Remember a message sent into an engine-made session (its delta source on the way back)."""
    with _lock:
        data = _load()
        rec = data["conversations"].get(root)
        if not rec:
            return
        for seg in rec["segments"]:
            if seg.get("session_id") == _norm_id(session_id):
                seg.setdefault("sent", []).append({"ts": time.time(), "text": text, "log": log_path, "offset": int(offset or 0)})
                _save(data)
                return


# ---------------------------------------------------------------- budget / binary


def budget(adapter, model="", turns=DEFAULT_K):
    """(k exchanges, char budget): k = clamp(5, 20, min(turns, what 25% of the context fits))."""
    chars = int(adapter.context_tokens(model) * CONTEXT_SHARE * CHARS_PER_TOKEN)
    fit = max(MIN_K, chars // CHARS_PER_EXCHANGE)
    want = int(turns) if turns else DEFAULT_K
    return max(MIN_K, min(MAX_K, want, fit)), chars


def find_binary(adapter, finder=None):
    if finder is not None:
        try:
            found = finder(adapter.id)
        except Exception:
            found = None
        if found:
            return found
    for name in (adapter.data.get("detect") or {}).get("binary") or []:
        found = shutil.which(name)
        if found:
            return found
    return None


def segment_tokens(seg):
    """Rough token count of an engine-made session: its own file when the store is per-session,
    else what was transplanted plus what was sent since."""
    path = seg.get("path") or ""
    if path and seg.get("session_id") and seg["session_id"] in os.path.basename(path):
        try:
            return os.path.getsize(path) // CHARS_PER_TOKEN
        except OSError:
            pass
    chars = int(seg.get("exchanges") or 0) * CHARS_PER_EXCHANGE
    for s in seg.get("sent") or []:
        chars += len(s.get("text") or "") + REPLY_MAX_CHARS // 2
    return chars // CHARS_PER_TOKEN


def segment_full(seg, model=""):
    """True when a transplanted session is near its provider's context limit."""
    if seg.get("method") != TRANSPLANT:
        return False
    adapter = registry.for_provider(seg.get("provider"))
    if adapter is None:
        return False
    return segment_tokens(seg) > adapter.context_tokens(model) * FULL_SHARE


# ---------------------------------------------------------------- resume


def _log_path(provider, session_id):
    d = os.path.join(state_dir(), "chat_runs")
    os.makedirs(d, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", "%s-%s" % (provider, session_id))[:180]
    return os.path.join(d, safe + ".log")


def _launch(argv, cwd, log_path):
    """Start argv detached; if it exits non-zero within 2s, return its stderr tail (6.7)."""
    kwargs = {"cwd": cwd if cwd and os.path.isdir(cwd) else None, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    from subway_env import headless_env
    env = headless_env()
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = repo + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    kwargs["env"] = env
    with open(log_path, "ab") as log:
        start = log.tell()
        proc = subprocess.Popen(argv, stdout=log, stderr=log, **kwargs)
    try:
        proc.wait(timeout=EARLY_EXIT_SECONDS)
    except subprocess.TimeoutExpired:
        return True, "running", {"pid": proc.pid, "log": log_path, "offset": start, "proc": proc}
    if proc.returncode != 0:
        return False, "exited %s: %s" % (proc.returncode, _log_tail(log_path, start, 300)), {"pid": proc.pid, "log": log_path}
    return True, "finished", {"pid": proc.pid, "log": log_path, "offset": start, "proc": proc}


def _log_tail(path, start=0, limit=REPLY_MAX_CHARS, stop=None):
    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            raw = fh.read(-1 if stop is None else max(0, stop - start))
    except OSError:
        return ""
    raw = _align_utf8(raw)
    return _ANSI.sub("", raw.decode("utf-8", "replace")).strip()[-limit:]


def _align_utf8(raw: bytes) -> bytes:
    """A7: a byte offset can land mid-multibyte-character when a log is flushed
    concurrently. Skip leading continuation bytes so decode starts on a boundary,
    and drop a trailing partial character instead of emitting U+FFFD."""
    i = 0
    while i < len(raw) and (raw[i] & 0xC0) == 0x80:
        i += 1
    raw = raw[i:]
    # Trim an incomplete trailing sequence (up to 3 continuation bytes can follow a start byte).
    for back in range(1, min(4, len(raw)) + 1):
        tail = raw[-back]
        if (tail & 0x80) == 0:  # ASCII: sequence complete
            break
        if (tail & 0xC0) == 0xC0:  # start byte too close to the end: incomplete
            raw = raw[:-back]
            break
    return raw


def resume(adapter, ref, text, *, binary, cwd, model="", effort="", launcher=None):
    """Run the adapter's resume argv for an existing session, detached."""
    key = "%s:%s" % (adapter.id, ref)
    prev = _runs.get(key)
    if prev is not None and prev.poll() is None:
        return False, "still replying to the previous message", {"pid": prev.pid}
    argv = adapter.resume_argv(binary, ref, text, model=model, effort=effort)
    streaming_ids = set(_PROVIDER_DATA.get("streaming_ids") or [])
    sid_str = os.path.basename(str(ref))
    if adapter.id in streaming_ids:
        os.environ["SUBWAY_LIVE_KEY"] = "%s-%s" % (adapter.id, sid_str)
        os.environ["SUBWAY_LAUNCH_SID"] = sid_str
        argv = [sys.executable, "-m", "transplant.stream_tee", adapter.id, "--"] + argv
    log_path = _log_path(adapter.id, sid_str)
    ok, detail, info = (launcher or _launch)(argv, cwd, log_path)
    proc = info.pop("proc", None)
    if proc is not None:
        _runs[key] = proc
        try:
            import provider_lines as _fl
            with _fl._headless_lock:
                _fl._headless_runs[key] = proc
                _fl._record_headless_meta(key, proc, log_path, info.get("offset") or 0, text)
        except Exception:
            pass
    return ok, detail, info


def continue_segment(root, seg, text, *, cwd, model="", effort="", finder=None, launcher=None):
    """Send text into an engine-made session through its adapter's resume command."""
    adapter = registry.for_provider(seg.get("provider"))
    if adapter is None:
        return {"ok": False, "delivered": False, "method": seg.get("method"), "provider": seg.get("provider"),
                "chat_id": seg.get("session_id"), "detail": "no adapter for %s" % seg.get("provider")}
    binary = find_binary(adapter, finder)
    if not binary:
        return {"ok": False, "delivered": False, "method": seg.get("method"), "provider": adapter.id,
                "chat_id": seg.get("session_id"), "detail": "%s binary not found" % adapter.id}
    ok, detail, info = resume(adapter, seg.get("ref") or seg.get("session_id"), text,
                              binary=binary, cwd=cwd, model=model, effort=effort, launcher=launcher)
    if ok:
        note_sent(root, seg.get("session_id"), text, info.get("log", ""), info.get("offset", 0))
    return {"ok": ok, "delivered": ok, "method": seg.get("method"), "provider": adapter.id,
            "chat_id": seg.get("session_id"), "detail": detail, "pid": info.get("pid"), "log": info.get("log")}


# ---------------------------------------------------------------- delta / IR


def sent_turns(seg, since=0.0):
    """IR turns for messages sent into an engine-made session after `since`, replies from its logs."""
    turns = []
    allsent = seg.get("sent") or []
    for i, s in enumerate(allsent):
        ts = float(s.get("ts") or 0)
        if ts <= since:
            continue
        turns.append(_turn("user", s.get("text") or "", seg.get("provider"), ts))
        nxt = next((n.get("offset") for n in allsent[i + 1:] if n.get("log") == s.get("log")), None)
        reply = _reply_from_log(s.get("log"), s.get("offset") or 0, nxt, seg.get("provider"))
        if reply:
            turns.append(_turn("assistant", reply, seg.get("provider"), ts + 0.001))
    return turns


def _reply_from_log(path, offset=0, stop=None, provider=""):
    """One run's output: its log from the offset it started at to where the next run started.
    An adapter with structured output (resume.output) yields only the assistant's words."""
    if not path:
        return ""
    key = (path, _file_sig(path), offset, stop, provider or "")
    with _lock:
        if key in _reply_cache:
            return _reply_cache[key]
    adapter = registry.for_provider(provider) if provider else None
    if adapter is None or not adapter.structured_output:
        text = _strip_cli_noise(_log_tail(path, offset, REPLY_MAX_CHARS, stop))
    else:
        raw = _log_tail(path, offset, 1 << 24, stop)  # whole run: the reply may sit before long usage events
        text = adapter.reply_text(raw)[-REPLY_MAX_CHARS:]
    # A7: only cache when the log was stable across the read — a signature change
    # means the run was mid-flush and the reply we just built is incomplete.
    if _file_sig(path) == key[1]:
        with _lock:
            # One entry per run log: a streaming reply changes the signature on every chunk, so drop older entries.
            for k in [k for k in _reply_cache if k[0] == path and k[2] == offset]:
                del _reply_cache[k]
            _reply_cache[key] = text
    return text


# Launcher chatter some CLIs print before the reply (wrapper, resume-by-id warnings).
_CLI_NOISE = re.compile(_PROVIDER_DATA.get("cli_noise_re") or r"^(Warning: No project session found with id .*)$")


def _strip_cli_noise(text):
    return "\n".join(l for l in (text or "").splitlines() if not _CLI_NOISE.match(l.strip())).strip()


def _turn(role, text, provider, ts):
    return {"role": role, "text": text, "actions": [], "files": [], "decisions": [],
            "model": "", "provider": provider or "", "ts": ts}


def delta_text(turns, max_chars=DELTA_MAX_CHARS):
    """Plain text of IR turns, newest kept when over budget."""
    blocks = []
    for t in turns:
        body = writer.render_assistant_text(t) if t.get("role") == "assistant" else (t.get("text") or "")
        if body.strip():
            blocks.append("%s: %s" % ("User" if t.get("role") == "user" else "Assistant", body.strip()))
    out = []
    total = 0
    for b in reversed(blocks):
        if total + len(b) > max_chars and out:
            break
        out.append(b)
        total += len(b)
    return "\n\n".join(reversed(out))


def turns_since(chat, since):
    """IR turns of a readable chat newer than `since` (epoch seconds)."""
    conv = ir.from_chat(chat)
    return [t for t in conv.get("turns") or [] if float(t.get("ts") or 0) > since]


# ---------------------------------------------------------------- transplant


def mark_launched(session_id, why):
    """Hide a session Subway made for itself (e.g. a probe) from the conversations list."""
    path = os.path.join(state_dir(), "launched_sessions.json")
    with _lock:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        if isinstance(data, list):
            data = {str(x): "" for x in data}
        data[str(session_id)] = why
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, path)


def _primer(provider, reason):
    return {"ok": False, "delivered": False, "method": PRIMER, "provider": provider, "reason": reason,
            "note": "primer: %s" % reason}


def _probe_ok(adapter, sample, *, binary, cwd, model, runner):
    """Cached probe verdict per adapter fingerprint; runs one probe when there is none (or a stale failure)."""
    if probe.cached(adapter):
        return True, "cached"
    entry = probe._load().get(probe.fingerprint_key(adapter))
    if isinstance(entry, dict) and not entry.get("ok") and time.time() - float(entry.get("ts") or 0) < PROBE_RETRY_SECONDS:
        return False, "last probe failed: %s" % str(entry.get("detail") or "")[-160:]
    kw = {"bin_path": binary, "cwd": cwd, "model": model}
    if runner is not None:
        kw["runner"] = runner
    try:
        ok, detail, info = probe.probe_adapter(adapter, sample, **kw)
    except Exception as exc:
        probe.record(adapter, False, "probe error: %s" % exc)
        return False, "probe error: %s" % exc
    if info and info.get("session_id"):
        mark_launched(info["session_id"], "transplant probe")
    return ok, detail


def transplant(chat, to_provider, text, *, model="", effort="", project_dir="", turns=DEFAULT_K, full=False,
               preface="", root=None, finder=None, launcher=None, probe_runner=None, conv=None):
    """Move a conversation to to_provider as a native session and resume it with text.

    Returns {ok, method: "transplant"|"primer", provider, chat_id, ...}. method "primer" with
    ok False means the caller should fall back to a primer (context-in-prompt) launch.
    """
    adapter = registry.for_provider(to_provider)
    if adapter is None:
        return _primer(to_provider, "not mapped yet")
    binary = find_binary(adapter, finder)
    if not binary:
        return _primer(adapter.id, "binary not found")
    try:
        source_conv = conv if conv is not None else ir.from_chat(chat)
    except Exception as exc:
        return _primer(adapter.id, "source chat unreadable: %s" % exc)
    if not source_conv.get("turns"):
        return _primer(adapter.id, "source chat has no readable turns")
    k, chars = budget(adapter, model, turns)
    trimmed = ir.trim(source_conv, k=k, budget_chars=0 if full else chars, full=full)
    problems = ir.validate(trimmed)
    if problems:
        return _primer(adapter.id, "invalid history: %s" % problems[0])
    cwd = project_dir or trimmed.get("project_dir") or ""
    if not cwd or not os.path.isdir(cwd):
        return _primer(adapter.id, "no project directory")
    try:
        from transplant import learn
        moved, where = learn.drifted(adapter)
    except Exception:
        moved, where = False, ""
    if moved:
        return _primer(adapter.id, "store format changed (%s); re-learning it needs your approval" % where)

    # Skip synchronous CLI probing on the hot send path; fall back to primer only if write_session/resume fails.

    try:
        result = writer.write_session(adapter, trimmed, cwd=cwd, model=model)
    except Exception as exc:
        return _primer(adapter.id, "write failed: %s" % exc)
    ref = probe.session_ref(adapter, result)
    message = (preface.rstrip() + "\n\n" + text) if preface.strip() else text
    run_ok, run_detail, info = resume(adapter, ref, message, binary=binary, cwd=cwd, model=model,
                                      effort=effort, launcher=launcher)
    n = len(ir.exchanges(trimmed))
    out = {
        "ok": run_ok,
        "delivered": run_ok,
        "method": TRANSPLANT,
        "provider": adapter.id,
        "chat_id": result["session_id"],
        "path": result["path"],
        "exchanges": n,
        "detail": run_detail,
        "pid": info.get("pid"),
        "log": info.get("log"),
        "note": "session, %d exchanges" % n,
    }
    if root:
        add_segment(root, adapter.id, result["session_id"], ref, TRANSPLANT, path=result["path"],
                    exchanges=n, cwd=cwd)
        if run_ok:
            note_sent(root, result["session_id"], text, info.get("log", ""), info.get("offset", 0))
    return out

def _rec_for_write(data, chat_id, provider=""):
    root = _lookup_root(data, chat_id, provider=provider)
    return root, (data["conversations"].get(root) if root else None)


def set_cut(chat_id, tid, provider=""):
    """Hide everything after turn `tid` in this chat (the transcript file is never touched)."""
    with _lock:
        root, rec = conversation_for(chat_id, provider=provider)
        if rec is None:
            start_conversation(chat_id, provider or "")
        data = _load()
        root, rec = _rec_for_write(data, chat_id, provider=provider)
        prior = (rec.get("cut") or {}).get("hidden") or []
        rec["cut"] = {"tid": str(tid), "at": time.time(), "sent": False, "hidden": prior}
        _save(data)
        return rec["cut"]


def clear_cut(chat_id):
    with _lock:
        data = _load()
        root, rec = _rec_for_write(data, chat_id)
        cut = (rec or {}).get("cut")
        if not cut:
            return False
        if cut.get("sent"):
            raise ValueError("already continued")
        rec.pop("cut", None)
        _save(data)
        return True


def cut_for(chat_id):
    root, rec = conversation_for(chat_id)
    return (rec or {}).get("cut") or None


def mark_cut_sent(chat_id, hidden=()):
    """The next message went out: from now on only the reverted rows (`hidden` tids) stay hidden."""
    with _lock:
        data = _load()
        root, rec = _rec_for_write(data, chat_id)
        if rec and rec.get("cut"):
            rec["cut"]["sent"] = True
            rec["cut"]["hidden"] = list(dict.fromkeys((rec["cut"].get("hidden") or []) + [str(t) for t in hidden]))
            _save(data)


def fork_conversation(chat_id, tid, provider):
    """New conversation record that starts from turn `tid` of this chat."""
    import secrets
    with _lock:
        data = _load()
        root = data["sessions"].get(_norm_id(chat_id)) or _norm_id(chat_id)
        fid = "fork-" + secrets.token_hex(4)
        data["conversations"][fid] = {"root": fid, "from_root": root, "from_tid": str(tid),
                                      "provider": str(provider or ""), "current": 0, "segments": [], "at": time.time()}
        _save(data)
        return fid


def discard_fork(fid):
    """Drop a fork record whose launch failed, so no orphan is left behind."""
    if not str(fid).startswith("fork-"):
        return
    with _lock:
        data = _load()
        if data["conversations"].pop(fid, None) is not None:
            _save(data)


def moved_copies():
    """Session ids that are a conversation's later segments (not its root): hidden from chat lists."""
    data = _load()
    out = set()
    for rec in (data.get("conversations") or {}).values():
        for s in (rec.get("segments") or [])[1:]:
            if s.get("session_id"):
                out.add(s["session_id"])
    return out


def merge_conversation_turns(chat_id, turns, turns_limit=None):
    """One conversation across providers, in order: the native chat's own turns for its window,
    then each engine-made segment's sent messages and replies. (turns, None) when not a conversation."""
    from transplant.ir import norm_ts
    root, rec = conversation_for(chat_id)
    segs = (rec or {}).get("segments") or []
    if len(segs) < 2:
        return turns, None
    cid = _norm_id(chat_id)
    fetch_turns = turns_limit if turns_limit is not None else max(100, len(turns or []))
    last_ts = norm_ts((turns or [{}])[-1].get("ts")) if turns else None
    merge_key = (chat_id, len(turns or []), last_ts, _conv_cache.get("sig"))

    merged = []
    native_done = set()
    native_sigs = []
    reply_sigs = []
    for seg in segs:
        if seg.get("method") == "native":
            # Every turn of the native chat, whenever it was sent (a send can go back to it after a move).
            sid = seg.get("session_id")
            if sid in native_done:
                continue
            native_done.add(sid)
            part_src = turns
            sig = None
            if cid != sid:
                try:
                    from cli_tools import chats as _chats
                    cached = _native_turns_cache.get(sid)
                    if cached:
                        cur_sig = _native_sig(cached.get("path"), seg.get("provider"))
                        if cur_sig is not None and cur_sig == cached.get("sig"):
                            part_src = cached.get("turns") or []
                            sig = cur_sig
                        else:
                            p = _chats.chat_turns(sid, source=seg.get("provider") or "all", turns=fetch_turns) or {}
                            fetched = p.get("turns")
                            sig = _native_sig(p.get("path"), seg.get("provider"))
                            if isinstance(fetched, list):
                                part_src = fetched
                                if sig is not None:
                                    _native_turns_cache[sid] = {"sig": sig, "turns": fetched, "path": p.get("path")}
                    else:
                        p = _chats.chat_turns(sid, source=seg.get("provider") or "all", turns=fetch_turns) or {}
                        fetched = p.get("turns")
                        sig = _native_sig(p.get("path"), seg.get("provider"))
                        if isinstance(fetched, list):
                            part_src = fetched
                            if sig is not None:
                                _native_turns_cache[sid] = {"sig": sig, "turns": fetched, "path": p.get("path")}
                except Exception:
                    part_src = turns
                    sig = None
            else:
                sig = _native_sig((turns[0].get("path") if turns else None), seg.get("provider"))
            native_sigs.append((sid, sig))
            part = [dict(t) for t in (part_src or [])]
        else:
            for s in (seg.get("sent") or []):
                reply_sigs.append((s.get("log"), _file_sig(s.get("log")), s.get("offset"), seg.get("provider")))
            part = [{"role": t.get("role"), "text": t.get("text") or "", "ts": t.get("ts")} for t in sent_turns(seg)]
        for t in part:
            t["provider"] = seg.get("provider") or t.get("provider") or ""
        merged.extend(part)

    full_key = merge_key + (tuple(native_sigs), tuple(reply_sigs))
    with _lock:
        cached_merge = _merge_cache.get(chat_id)
        if cached_merge and cached_merge.get("key") == full_key:
            return copy.deepcopy(cached_merge["result"])

    last_ts = None
    keyed = []
    for idx, t in enumerate(merged):
        ts = norm_ts(t.get("ts"))
        if ts:
            last_ts = ts
            eff = ts
        else:
            eff = last_ts if last_ts is not None else 0.0
        keyed.append((eff, idx, t))
    keyed.sort(key=lambda item: (item[0], item[1]))
    merged = [item[2] for item in keyed]
    cur = current_segment(rec) or {}
    meta = {"root": root, "current_provider": cur.get("provider") or "",
            "segments": [{k: s.get(k) for k in ("provider", "method", "start", "end")} for s in segs]}
    result = (merged, meta)
    with _lock:
        _merge_cache[chat_id] = {"key": full_key, "result": copy.deepcopy(result)}
    return result
