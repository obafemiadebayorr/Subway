# Resume probe: verify a written session by resuming it headless.

import hashlib
import json
import os
import re
import subprocess
import time

from transplant import writer

DEFAULT_ASK = "Reply with only the last user message before this one."
_MATCH_LEN = 60
_DETAIL_LEN = 300


def cache_path():
    """Probe cache file under SUBWAY_DIR (or ~/.subway-context)/adapters."""
    base = os.environ.get("SUBWAY_DIR") or os.path.join(
        os.path.expanduser("~"), ".subway-context"
    )
    return os.path.join(base, "adapters", "probe_cache.json")


def fingerprint_key(adapter):
    """Adapter id plus a short digest of the fingerprint + write sections."""
    raw = json.dumps(
        {
            "fingerprint": (adapter.data.get("store") or {}).get("fingerprint"),
            "write": adapter.data.get("write"),
        },
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return "%s:%s" % (adapter.id, digest)


def _load():
    try:
        with open(cache_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cached(adapter):
    """Return the cached passing entry for this adapter fingerprint, or None."""
    entry = _load().get(fingerprint_key(adapter))
    if isinstance(entry, dict) and entry.get("ok"):
        return entry
    return None


def record(adapter, ok, detail):
    """Store a probe outcome for this adapter fingerprint."""
    data = _load()
    data[fingerprint_key(adapter)] = {
        "ok": bool(ok),
        "detail": detail,
        "ts": time.time(),
    }
    path = cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def session_ref(adapter, result):
    """What the resume command names: the session id, or its file path when resume.ref is "path"."""
    if (adapter.data.get("resume") or {}).get("ref") == "path":
        return result["path"]
    return result["session_id"]


def run_probe(
    adapter,
    bin_path,
    session_id,
    expected,
    *,
    cwd,
    model="",
    timeout=120,
    runner=subprocess.run,
):
    """Run the resume argv headless; pass if the reply echoes one of the expected messages."""
    ask = (adapter.data.get("probe") or {}).get("ask") or DEFAULT_ASK
    argv = adapter.resume_argv(bin_path, session_id, ask, model=model)
    try:
        proc = runner(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    stdout = adapter.reply_text(proc.stdout or "")
    stderr = proc.stderr or ""
    # The probe proves the provider loaded the planted history, not that its model counts
    # turns well: echoing any planted user message passes (a light model may pick the first).
    wanted = [expected] if isinstance(expected, str) else list(expected or [])
    out = _norm(stdout)
    ok = proc.returncode == 0 and any(_norm(w)[:_MATCH_LEN] and _norm(w)[:_MATCH_LEN] in out for w in wanted)
    detail = (stdout + stderr)[-_DETAIL_LEN:]
    return ok, detail


def probe_adapter(
    adapter,
    conv,
    *,
    bin_path,
    cwd,
    model="",
    extra=None,
    force=False,
    runner=subprocess.run,
):
    """Write a copy of conv, resume it, and cache the outcome."""
    if not force:
        hit = cached(adapter)
        if hit:
            return True, hit.get("detail", "cached"), None
    result = writer.write_session(
        adapter, conv, cwd=cwd, model=model, extra=extra
    )
    turns = conv.get("turns") or []
    expected = [turn.get("text", "") for turn in reversed(turns) if turn.get("role") == "user" and turn.get("text")]
    ok, detail = run_probe(
        adapter,
        bin_path,
        session_ref(adapter, result),
        expected,
        cwd=cwd,
        model=model,
        runner=runner,
    )
    record(adapter, ok, detail)
    info = {
        "session_id": result["session_id"],
        "path": result["path"],
        "records": result["records"],
        "fingerprint": fingerprint_key(adapter),
    }
    return ok, detail, info
