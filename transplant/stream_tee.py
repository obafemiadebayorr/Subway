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
from typing import Iterable, List, Optional


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
        if rec.get("type") != "message_update":
            return None
        ev = rec.get("assistantMessageEvent") or {}
        if ev.get("type") == "text_delta":
            delta = ev.get("delta")
            return delta if isinstance(delta, str) and delta else None
        return None

    if kind == "partial_messages":
        if rec.get("type") != "stream_event":
            return None
        ev = rec.get("event") or {}
        if ev.get("type") != "content_block_delta":
            return None
        delta = ev.get("delta") or {}
        if delta.get("type") != "text_delta":
            return None
        text = delta.get("text")
        return text if isinstance(text, str) and text else None

    if kind == "whole_parts":
        # emits whole text parts/events, not token deltas.
        if rec.get("type") == "text":
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


def _insert_flags(argv: List[str], flags: List[str]) -> List[str]:
    """Insert streaming flags before a final prompt argument when present.

    Heuristic: for supported providers the prompt is the last positional arg or
    follows a flag that takes no value. We prepend flags after the binary so they
    apply regardless, which is safe for supported CLIs.
    """
    if not argv:
        return list(flags)
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
    try:
        for line in proc.stdout:
            raw = line.rstrip("\r\n")
            if not raw:
                continue
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
                            out.write(delta[len(prev):])
                            out.flush()
                            seen_parts.add(delta)
                            emitted = True
                            break
                    if emitted:
                        continue
                    seen_parts.add(delta)
                out.write(delta)
                out.flush()
                continue
            # JSON but no delta (tool/usage/thinking events) -> suppress; non-JSON passes through.
            stripped = raw.lstrip()
            if stripped.startswith("{") or stripped.startswith("["):
                continue
            out.write(raw + "\n")
            out.flush()
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
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
