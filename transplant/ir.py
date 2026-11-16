"""Neutral turn format (IR) for the provider transplant (roadmap 73, step A, item 2.1-2.4)."""

import re
from datetime import datetime
from typing import Any, List, TypedDict

ARGS_MAX = 200
RESULT_MAX = 400
TOOL_MAX_LINES = 40
DECISION_MAX_COUNT = 5
DECISION_MIN_LEN = 20
DECISION_MAX_LEN = 400
DECISION_CLIP = 250
FORBIDDEN_KEYS = ("thought", "thoughts", "reasoning", "signature")


class Action(TypedDict):
    tool: str
    args: str
    result: str
    ok: bool


class Turn(TypedDict):
    role: str
    text: str
    actions: List[Action]
    files: List[str]
    decisions: List[str]
    model: str
    provider: str
    ts: float


class Conversation(TypedDict):
    id: str
    source: str
    project_dir: str
    title: str
    turns: List[Turn]
    git: dict


def norm_ts(v: Any) -> float:
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s:
            if s.endswith("Z") or s.endswith("z"):
                s = s[:-1] + "+00:00"
            try:
                return float(datetime.fromisoformat(s).timestamp())
            except (ValueError, OverflowError, OSError):
                return 0.0
    return 0.0


def trim_action(tool: str, args: str, result: str, ok: bool = True) -> Action:
    a = args or ""
    r = result or ""
    if len(a) > ARGS_MAX:
        a = a[:ARGS_MAX] + "…"
    n_lines = len((r or "").splitlines())
    if n_lines > TOOL_MAX_LINES:
        r = f"[output: {n_lines} lines]"
    if len(r) > RESULT_MAX:
        r = r[:RESULT_MAX] + "…"
    return {"tool": tool, "args": a, "result": r, "ok": ok}


def _paragraphs(text: str) -> List[str]:
    return [p for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def _exchange_decisions(texts: List[str]) -> List[str]:
    import server as _s

    out: List[str] = []
    for a in texts or []:
        for block in _paragraphs(a):
            b = block.strip()
            if not (DECISION_MIN_LEN < len(b) < DECISION_MAX_LEN):
                continue
            if not _s.DECISION_RE.search(b):
                continue
            d = _s.one_line(b, DECISION_CLIP)
            if d not in out:
                out.append(d)
            if len(out) >= DECISION_MAX_COUNT:
                return out
    return out


def from_turns(
    turns: List[dict],
    *,
    source: str,
    chat_id: str = "",
    title: str = "",
    project_dir: str = "",
    model: str = "",
) -> Conversation:
    import server as _s

    from chat_readers.render import to_exchanges

    conv: Conversation = {
        "id": chat_id,
        "source": source,
        "project_dir": project_dir,
        "title": title,
        "turns": [],
        "git": {},
    }
    for ex in to_exchanges(turns or []):
        if ex.get("kind") == "summary":
            continue
        user_text = ex.get("user") or ""
        if user_text.strip():
            conv["turns"].append(
                {
                    "role": "user",
                    "text": user_text,
                    "actions": [],
                    "files": sorted(ex.get("files") or set()),
                    "decisions": [],
                    "model": model,
                    "provider": source,
                    "ts": norm_ts(ex.get("ts")),
                }
            )
        assistant_texts = list(ex.get("assistant") or [])
        if not assistant_texts:
            continue
        assistant_text = "\n\n".join(assistant_texts)
        actions: List[Action] = []
        for t in ex.get("tools") or []:
            if not t:
                continue
            parts = str(t).split(None, 1)
            tool = parts[0] if parts else ""
            args = parts[1].strip() if len(parts) > 1 else ""
            actions.append(trim_action(tool=tool, args=args, result="", ok=True))
        conv["turns"].append(
            {
                "role": "assistant",
                "text": assistant_text,
                "actions": actions,
                "files": sorted(ex.get("files") or set()),
                "decisions": _exchange_decisions(assistant_texts),
                "model": model,
                "provider": source,
                "ts": norm_ts(ex.get("ts")),
            }
        )
    return conv


def from_chat(chat: dict, **kw) -> Conversation:
    import server as _s  # noqa: F401  (claims the "server" module name before chat_readers aliases it)

    from chat_readers.common import chat_turns

    return from_turns(
        chat_turns(chat),
        source=chat.get("source") or "",
        chat_id=chat.get("id") or "",
        title=chat.get("title") or "",
        project_dir=chat.get("project_dir") or chat.get("cwd") or "",
        model=kw.get("model") or "",
    )


def exchanges(conv) -> List[List[Turn]]:
    groups: List[List[Turn]] = []
    cur: List[Turn] = []
    for t in conv.get("turns") or []:
        # Stacked user messages (sent before any reply) stay in one exchange, so trimming
        # never keeps the reply while cutting the messages it answered.
        if not cur or cur[-1].get("role") != "user":
            cur = [t]
            groups.append(cur)
        else:
            cur.append(t)
    return groups


def _copy_turn(t: Turn) -> Turn:
    return {
        "role": t.get("role") or "",
        "text": t.get("text") or "",
        "actions": [dict(a) for a in t.get("actions") or []],
        "files": list(t.get("files") or []),
        "decisions": list(t.get("decisions") or []),
        "model": t.get("model") or "",
        "provider": t.get("provider") or "",
        "ts": t.get("ts") if isinstance(t.get("ts"), (int, float)) else 0.0,
    }


def _turn_chars(t) -> int:
    total = len(t.get("text") or "")
    for a in t.get("actions") or []:
        total += len(a.get("args") or "") + len(a.get("result") or "")
    return total


def trim(conv, k: int = 12, budget_chars: int = 0, full: bool = False) -> Conversation:
    out: Conversation = {
        "id": conv.get("id") or "",
        "source": conv.get("source") or "",
        "project_dir": conv.get("project_dir") or "",
        "title": conv.get("title") or "",
        "turns": [_copy_turn(t) for t in (conv.get("turns") or [])],
        "git": dict(conv.get("git") or {}),
    }
    if full:
        return out
    groups = exchanges(conv)
    k_eff = 12 if (k is None or k <= 0) else max(5, min(20, k))
    kept: List[List[Turn]] = list(groups[-k_eff:]) if groups else []
    if budget_chars and budget_chars > 0:
        def _total(gs: List[List[Turn]]) -> int:
            return sum(_turn_chars(t) for g in gs for t in g)

        while len(kept) > 1 and _total(kept) > budget_chars:
            kept.pop(0)
    turns: List[Turn] = []
    for g in kept:
        for t in g:
            turns.append(_copy_turn(t))
    out["turns"] = turns
    return out


def _scan_forbidden(obj: Any, path: str, problems: List[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_KEYS:
                problems.append(f"{path}: forbidden key {k!r}")
            _scan_forbidden(v, f"{path}.{k}", problems)
    elif isinstance(obj, (list, tuple)):
        for idx, v in enumerate(obj):
            _scan_forbidden(v, f"{path}[{idx}]", problems)


def validate(conv) -> List[str]:
    problems: List[str] = []
    if not isinstance(conv, dict):
        return [f"conversation is not a dict: {type(conv).__name__}"]
    for key in ("id", "source", "project_dir", "title", "turns", "git"):
        if key not in conv:
            problems.append(f"missing conversation key: {key}")
    turns = conv.get("turns")
    if not isinstance(turns, list):
        problems.append("turns is not a list")
        turns = []
    for i, t in enumerate(turns):
        if not isinstance(t, dict):
            problems.append(f"turn {i}: not a dict")
            continue
        for key in ("role", "text", "actions", "files", "decisions", "model", "provider", "ts"):
            if key not in t:
                problems.append(f"turn {i}: missing key {key}")
        if t.get("role") not in ("user", "assistant"):
            problems.append(f"turn {i}: bad role {t.get('role')!r}")
        ts = t.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            problems.append(f"turn {i}: non-float ts {ts!r}")
        for key in FORBIDDEN_KEYS:
            if key in t:
                problems.append(f"turn {i}: forbidden key {key!r}")
    _scan_forbidden(conv, "", problems)
    return problems
