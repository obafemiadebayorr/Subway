"""Stream a headless CLI session into a plain-text run log.

Usage: python -m transplant.stream_tee <provider> -- <cli argv...>

For providers listed in provider_data.json the child is run in streaming-JSON mode
and only assistant text deltas are written to stdout immediately (stdout is the run
log). Non-JSON lines pass through unchanged. Stderr passes through. Exit code = child's.
Unknown providers exec the argv unchanged (pass-through).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Iterable, List, Optional

_HANDOFFS_FILE = "handoff_sessions.json"


def _record_launch_id(launched_sid, real_id):
    subway_dir = os.environ.get("SUBWAY_DIR") or os.path.join(os.path.expanduser("~"), ".subway")
    os.makedirs(subway_dir, exist_ok=True)
    path = os.path.join(subway_dir, _HANDOFFS_FILE)
    try:
        data = {}
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
    except Exception:
        data = {}
    data[launched_sid] = real_id
    fd, tmp = tempfile.mkstemp(dir=subway_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


def _load_data():
    path = os.path.join(os.path.dirname(__file__), "provider_data.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


_DATA = _load_data()
_STREAM_FLAGS = _DATA.get("stream_flags", {})
_STREAM_KIND = _DATA.get("stream_kind", {})


def _dig(obj, path):
    cur = obj
    for key in (path or "").split("."):
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _extract_error_msg(err) -> Optional[str]:
    if isinstance(err, str) and err.strip():
        return err.strip()
    if isinstance(err, dict):
        for key in ("message", "error", "detail", "reason", "result"):
            val = err.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, dict):
                nested = _extract_error_msg(val)
                if nested:
                    return nested
        data = err.get("data")
        if isinstance(data, dict):
            nested = _extract_error_msg(data)
            if nested:
                return nested
    return None


def _extract_real_session_id(rec: dict) -> Optional[str]:
    if not isinstance(rec, dict):
        return None
    if rec.get("event") == "init":
        val = rec.get("conversation_id") or rec.get("session_id") or rec.get("sessionId")
        if isinstance(val, str) and val:
            return val
    t = rec.get("type")
    if t == "thread.started":
        val = rec.get("thread_id")
        if isinstance(val, str) and val:
            return val
    if t == "session.start":
        data = rec.get("data") or {}
        if isinstance(data, dict):
            val = data.get("sessionId") or data.get("session_id") or data.get("id")
            if isinstance(val, str) and val:
                return val
        val = rec.get("sessionId") or rec.get("session_id")
        if isinstance(val, str) and val:
            return val
    if t in ("init", "system"):
        if t == "init" or rec.get("subtype") == "init":
            val = rec.get("session_id") or rec.get("conversation_id") or rec.get("sessionId")
            if isinstance(val, str) and val:
                return val
    return None


def extract_text(provider: str, line: str) -> Optional[str]:
    """Return an assistant text delta extracted from a streaming-JSON line, or None."""
    line = (line or "").rstrip("\r\n")
    if not line:
        return None
    try:
        rec = json.loads(line)
    except Exception:
        return None
    if not isinstance(rec, dict):
        return None

    kind = _STREAM_KIND.get(provider)
    if kind == "event_delta":
        t = rec.get("type")
        if t == "error":
            msg = _extract_error_msg(rec.get("error") or rec)
            return msg + "\n" if msg else None
        if t != "message_update":
            return None
        ev = rec.get("assistantMessageEvent") or {}
        if ev.get("type") == "text_delta":
            delta = ev.get("delta")
            return delta if isinstance(delta, str) and delta else None
        if ev.get("type") == "error":
            msg = _extract_error_msg(ev)
            return msg + "\n" if msg else None
        return None

    if kind == "partial_messages":
        t = rec.get("type")
        if t == "error":
            msg = _extract_error_msg(rec.get("error") or rec)
            return msg + "\n" if msg else None
        if t == "result" and rec.get("is_error"):
            msg = _extract_error_msg(rec)
            if not msg and isinstance(rec.get("errors"), list):
                joined = "; ".join(str(x) for x in rec["errors"] if x)
                msg = joined or None
            return msg + "\n" if msg else None
        if t != "stream_event":
            return None
        ev = rec.get("event") or {}
        if ev.get("type") != "content_block_delta":
            return None
        delta = ev.get("delta") or {}
        if delta.get("type") != "text_delta":
            return None
        text = delta.get("text")
        return text if isinstance(text, str) and text else None

    if kind == "step_delta":
        # Antigravity stream-json: {"event":"step_update","step_update":{"step_type":"agent_response","text_delta":...}}
        if rec.get("event") == "step_update":
            st = rec.get("step_update") or {}
            if st.get("step_type") != "agent_response":
                return None
            text = st.get("text_delta")
            return text if isinstance(text, str) and text else None
        if rec.get("event") == "result":
            res = rec.get("result") or {}
            err = res.get("error")
            if res.get("status") == "ERROR" and isinstance(err, str) and err:
                return err + "\n"
        return None

    if kind == "message_delta":
        # Copilot CLI --output-format json: {"type":"assistant.message_delta","data":{"deltaContent":...}}
        t = rec.get("type")
        if t == "assistant.message_delta":
            text = (rec.get("data") or {}).get("deltaContent")
            return text if isinstance(text, str) and text else None
        if t in ("session.error", "error"):
            msg = (rec.get("data") or {}).get("message") or rec.get("message")
            return msg + "\n" if isinstance(msg, str) and msg else None
        return None

    if kind == "item_text":
        # Codex exec --json: whole agent messages; turn.failed carries the error. Reconnect notices stay hidden.
        t = rec.get("type")
        if t == "item.completed":
            item = rec.get("item") or {}
            if item.get("type") != "agent_message":
                return None
            text = item.get("text")
            return text if isinstance(text, str) and text else None
        if t == "turn.failed":
            msg = _extract_error_msg(rec.get("error") or rec)
            return msg + "\n" if msg else None
        if t == "error":
            msg = _extract_error_msg(rec.get("error") or rec)
            if msg and not msg.lower().startswith("reconnecting"):
                return msg + "\n"
            return None
        return None

    if kind == "whole_parts":
        # emits whole text parts/events, not token deltas.
        t = rec.get("type")
        if t == "error":
            msg = _extract_error_msg(rec.get("error") or rec)
            return msg + "\n" if msg else None
        if t == "text":
            part = rec.get("part") or {}
            text = part.get("text")
            return text if isinstance(text, str) and text else None
        # tolerate alternate nesting
        part = rec.get("part") or {}
        if part.get("type") == "text":
            text = part.get("text")
            return text if isinstance(text, str) and text else None
        return None

    return None


def supports(provider: str) -> bool:
    """True when stream_tee knows how to read this provider's streaming output (provider_data.json)."""
    return provider in _STREAM_KIND and provider in _STREAM_FLAGS


def _insert_flags(argv: List[str], flags: List[str]) -> List[str]:
    """Insert streaming flags before a final prompt argument when present.

    Heuristic: for supported providers the prompt is the last positional arg or
    follows a flag that takes no value. Subcommands like ``run`` or ``exec``
    stay immediately after the binary so subcommand-scoped flags apply.
    """
    if not argv:
        return list(flags)
    if len(argv) >= 2 and argv[1] in ("run", "exec"):
        return argv[:2] + list(flags) + argv[2:]
    # place flags right after the executable/binary token
    return [argv[0]] + list(flags) + argv[1:]


def stream_child(provider: str, argv: List[str], stdout=None, stderr=None) -> int:
    """Run argv, streaming extracted text deltas to stdout. Returns child exit code."""
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    flags = _STREAM_FLAGS.get(provider)
    child_argv = _insert_flags(list(argv), flags) if flags else list(argv)
    kind = _STREAM_KIND.get(provider)

    proc = subprocess.Popen(
        child_argv,
        stdout=subprocess.PIPE,
        stderr=err,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    seen_parts = set()
    sender = None
    real_sender = None
    live_key = os.environ.get("SUBWAY_LIVE_KEY")
    if live_key:
        try:
            from hud_server.live_relay import Sender
            sender = Sender(live_key)
        except Exception:
            sender = None

    def _relay(chunk: str, done: bool = False):
        for s in (sender, real_sender):
            if s is not None:
                try:
                    s.send(chunk, done=done) if done else s.send(chunk)
                except Exception:
                    pass

    try:
        for line in proc.stdout:
            raw = line.rstrip("\r\n")
            if not raw:
                continue
            # Map launched sid -> real conversation/thread/session id for new chats.
            try:
                rec = json.loads(raw)
            except Exception:
                rec = None
            real_id = _extract_real_session_id(rec) if isinstance(rec, dict) else None
            if real_id:
                launched_sid = os.environ.get("SUBWAY_LAUNCH_SID")
                if launched_sid and real_id != launched_sid:
                    try:
                        _record_launch_id(launched_sid, real_id)
                    except Exception:
                        pass
                if live_key and real_sender is None:
                    real_live_key = f"{provider}-{real_id}"
                    if real_live_key != live_key:
                        try:
                            from hud_server.live_relay import Sender
                            real_sender = Sender(real_live_key)
                        except Exception:
                            real_sender = None
            # Try JSON extraction first.
            delta = extract_text(provider, raw)
            if delta is not None:
                # whole_parts may repeat full accumulated text; emit only unseen chunks.
                if kind == "whole_parts":
                    if delta in seen_parts:
                        continue
                    # if this is a superset of a previous part, emit the suffix
                    emitted = False
                    for prev in list(seen_parts):
                        if delta.startswith(prev) and len(delta) > len(prev):
                            piece = delta[len(prev):]
                            out.write(piece)
                            out.flush()
                            _relay(piece)
                            seen_parts.add(delta)
                            emitted = True
                            break
                    if emitted:
                        continue
                    seen_parts.add(delta)
                out.write(delta)
                out.flush()
                _relay(delta)
                continue
            # JSON but no delta (tool/usage/thinking events) -> suppress; non-JSON passes through.
            stripped = raw.lstrip()
            if stripped.startswith("{") or stripped.startswith("["):
                continue
            out.write(raw + "\n")
            out.flush()
            _relay(raw + "\n")
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        _relay("", done=True)
    return proc.wait()


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--" in args:
        sep = args.index("--")
        provider = args[sep - 1].strip() if sep >= 1 else ""
        child = args[sep + 1:]
    elif len(args) >= 2:
        provider = args[0].strip()
        child = args[1:]
    else:
        sys.stderr.write("usage: python -m transplant.stream_tee <provider> -- <cli argv...>\n")
        return 2

    if not child:
        return 0

    if provider not in _STREAM_FLAGS:
        os.execvp(child[0], child)

    return stream_child(provider, child)


if __name__ == "__main__":
    raise SystemExit(main())
