from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Union

import threads

import server as _s
def t_list_chats(source: str = "all", project: str = "", query: str = "", limit: int = 15,
                 include_subagents: bool = False) -> str:
    chats = _s.all_chats(source, include_subagents)
    aliases = _s.load_aliases()
    id_to_aliases: Dict[str, List[str]] = {}
    for a, cid in aliases.items():
        id_to_aliases.setdefault(cid, []).append(a)

    if project:
        chats = [c for c in chats if project.lower() in (c.get("project") or "").lower()]
    if query:
        q = query.lower()
        chats = [c for c in chats if q in " ".join(str(c.get(k) or "") for k in
                                                   ("title", "first_prompt", "last_prompt", "tab_name")).lower()]
    chats.sort(key=lambda c: (bool(c.get("open")), _s.sort_key_ts(c.get("updated"))), reverse=True)
    chats = chats[: max(1, int(limit))]
    if not chats:
        return "No chats found across Antigravity, Claude Code, OpenCode, or GitHub Copilot."
    me = _s.detect_self()
    from tables import table, truncate_text
    cols = ["line", "id", "alias", "project", "active", "tokens", "highlight"]
    rows = []
    for c in chats:
        labels = []
        if c.get("open"):
            labels.append(c.get("tab_name") or "open")
        if c.get("subagent"):
            parent_short = c.get('parent')[:8] if c.get('parent') else '?'
            labels.append(f"↳ {'worker' if c.get('worker') else 'child'} of `{parent_short}`")
        if c["id"] in id_to_aliases:
            labels.extend(f"@{a}" for a in id_to_aliases[c["id"]])
        else:
            # Show readable auto-alias hint if no explicit pin
            slug = _s.slugify_topic(c.get("title") or "")
            prefix = {"antigravity": "agy", "pi": "deep"}.get(c["source"], c["source"])
            if slug and slug != "chat":
                labels.append(f"(@{prefix}-{slug})")
        if me and me.get("id") == c["id"]:
            labels.append("(current line)")
        label_str = ", ".join(labels)
        highlight = _s._get_last_entry_preview(c)
        token_count = c.get("tokens")
        token_label = f"{int(token_count):,}" if isinstance(token_count, (int, float)) else ""
        rows.append([
            c["source"],
            c["id"][:8],
            truncate_text(label_str, 40),
            truncate_text(os.path.basename(c.get("project") or ""), 40),
            _s.fmt_ts(c.get("updated")),
            token_label,
            truncate_text(_s.one_line(highlight, 160).replace("|", "/"), 40),
        ])
    tbl = table(f"chats {len(chats)}", cols, rows)
    try:
        _s._sync_opencode_agent_stubs()
    except Exception:
        pass
    return tbl + "\n\nTip: Board any chat by ID, prefix, @pinned-alias, dynamic workspace head (e.g. @agy-dziner), or topic slug (e.g. @agy-subway)."


def t_search_chat(quote: str = "", query: str = "", source: str = "all", project: str = "", limit: int = 5,
                  include_subagents: bool = True, segment: Union[str, int] = "all") -> str:
    target = (quote or query or "").strip()
    if not target:
        return "Please provide a quote or snippet to search for."
    q_low = target.lower()
    needles = list({q_low.encode("utf-8"), json.dumps(q_low, ensure_ascii=False)[1:-1].encode("utf-8"),
                    json.dumps(q_low)[1:-1].encode("utf-8")})
    chats = _s.all_chats(source, include_subagents=include_subagents)
    if project:
        chats = [c for c in chats if project.lower() in (c.get("project") or "").lower()]
    chats.sort(key=lambda c: _s.sort_key_ts(c.get("updated")), reverse=True)  # Newest windows answer first.

    matches = []
    for c in chats:
        try:
            if c["source"] == "claude" and not _s._file_may_contain(c["path"], needles):
                continue
            data, seg_label, total_segments = _s.chat_segments_info(c, segment)
            ex = _s.to_exchanges(data)
        except OSError as e:
            _s.log("search skipped", c.get("id"), e)
            continue
        for i, e in enumerate(ex):
            corpus = " ".join([e["user"]] + e["assistant"] + e["thoughts"] + e["tools"])
            if q_low in corpus.lower():
                matches.append({
                    "chat": c,
                    "exchange_index": i,
                    "total_exchanges": len(ex),
                    "segment": seg_label,
                    "total_segments": total_segments,
                    "ts": e.get("ts"),
                    "sample": _s.one_line(e["user"] or (e["assistant"][0] if e["assistant"] else ""), 120),
                })
                break
        if len(matches) >= limit:
            break

    if not matches:
        return f"No matches found across active agents for: '{target}'."

    lines = [f"# Subway: Search results for '{target}':"]
    for m in matches:
        c = m["chat"]
        idx = m["exchange_index"]
        rem = m["total_exchanges"] - idx
        sub_tag = f" [Subagent of `{c['parent'][:8]}`]" if c.get("subagent") else ""
        seg_tag = f" [Segment {m['segment']} of {m['total_segments']}]"
        lines.append(
            f"- **{c['source'].upper()}** `{c['id'][:8]}`{sub_tag}{seg_tag} ({c.get('title')})\n"
            f"  Matched at Station `[{idx}]` of {m['total_exchanges']} ({_s.fmt_ts(m['ts'])})\n"
            f"  Snippet: \"{m['sample']}\"\n"
            f"  -> Transit forward: `read_chat(chat_id='{c['id'][:8]}', start={idx})` ({rem} stops left)"
        )
    return "\n".join(lines)


def t_tag_chat(alias: str, chat_id: str = "") -> str:
    """Assign a memorable sticky alias to a chat. If chat_id is omitted, tags the current active session."""
    det = _s._detected_chat()
    if det is not None:
        det_id = det["id"]
        if chat_id and not _s._id_matches(chat_id, det_id):
            return f"Error: caller is '{det_id}', cannot tag chat '{chat_id}'."
        target_id = chat_id or (det_id if not det.get("synthetic") else "latest")
    else:
        target_id = chat_id
        if not target_id:
            me = _s.detect_self()
            target_id = me["id"] if me and not me.get("synthetic") else "latest"
    c = _s.resolve(target_id)
    _s.save_alias(alias, c["id"])
    return f"Station alias '@{alias.lower().lstrip('@')}' now points to {c['source'].upper()} `{c['id'][:8]}` ({c.get('title')})."


def t_read_chat(chat_id: str, mode: str = "recent", turns: int = 6, start: Optional[int] = None,
                since: str = "", anchor_text: str = "",
                max_chars: int = _s.DEFAULT_MAX_CHARS, include_tools: bool = False,
                include_thoughts: bool = True, source: str = "all", project: str = "",
                segment: Union[str, int] = "all") -> str:
    if mode not in ("recent", "outline", "full", "decisions"):
        raise ValueError("mode must be 'recent', 'outline', 'full', or 'decisions'")
    since_spec = (since or "").strip().lower()
    bridge = re.fullmatch(r"bridge\s*-\s*(\d+)", since_spec)
    if since_spec and since_spec != "last" and not bridge:
        raise ValueError("since must be 'last' or 'bridge -N'")

    c = _s.resolve(chat_id, source, project)
    if threads.is_chat_excluded(c, base_dir=_s.SUBWAY_DIR):
        raise ValueError(f"Chat {chat_id} is excluded by app/project exclusion rules")
    c["mode"] = mode
    info = _s.chat_segments_info(c, segment)
    data, seg_label, total_segments = info[0], info[1], info[2]
    note = getattr(info, "note", None)
    last_tool = _s._last_tool_line(data) if mode == "recent" else ""
    ex = _s.to_exchanges(data)
    explicit_start = start is not None
    start_idx = max(0, int(start or 0))
    anchor_info = None

    if anchor_text:
        a_low = anchor_text.strip().lower()
        found_idx = None
        for i, e in enumerate(ex):
            corpus = " ".join([e["user"]] + e["assistant"] + e["thoughts"] + e["tools"])
            if a_low in corpus.lower():
                found_idx = i
                break
        if found_idx is not None:
            start_idx = found_idx
            explicit_start = True
            anchor_info = f"Boarding at quote '{anchor_text}' (station [{found_idx}])"
        else:
            anchor_info = f"Quote '{anchor_text}' not found; using default turn range."

    target_id = c["id"]
    my_id = _s._self_id()
    advance = False

    seg_notice = f"Segment {seg_label} of {total_segments}"
    if note:
        seg_notice += f" ({note})"

    if since_spec:
        cps = _s.get_checkpoints(my_id, target_id)
        if not cps:
            shown = min(max(1, int(turns)), len(ex))
            skipped = len(ex) - shown
            anchor_info = f"First transit between these two windows: showing the latest {shown} of {len(ex)} stations."
            if skipped and mode != "full":
                anchor_info += f" Stations 0-{skipped - 1} were not delivered; read them with start=0."
            advance = True
        else:
            offset = int(bridge.group(1)) if bridge else 1
            last_cp = cps[max(0, len(cps) - max(1, offset))]
            start_idx = min(int(last_cp.get(target_id, 0)), len(ex))
            explicit_start = True
            advance = not bridge  # A look back through history must not rewind the live position.
            anchor_info = f"Transit delta since watermark ({_s.fmt_ts(last_cp.get('ts'))}); boarding at station [{start_idx}]"
            if start_idx >= len(ex):
                result = (f"# Subway: {c['source'].upper()} `{target_id}`\n"
                          f"Segment: {seg_notice}\n"
                          f"No new stations since your last transit ({len(ex)} stations, watermark {_s.fmt_ts(last_cp.get('ts'))}).")
                return f"{result}\n{last_tool}" if last_tool else result

    anchor_info = f"[{seg_notice}] {anchor_info}" if anchor_info else f"[{seg_notice}]"

    delivery: Dict[str, Any] = {}
    text = _s.render_chat(c, data, mode, int(turns), start_idx, int(max_chars),
                       include_thoughts, include_tools, anchor_info,
                       explicit_start=explicit_start, delivery=delivery)
    if last_tool:
        text = f"{text}\n\n{last_tool}"

    # The watermark only moves on since='last' reads, and only as far as what was really delivered.
    last = delivery.get("last")
    if advance and last is not None:
        _s.record_checkpoint(my_id, target_id, 0, int(last) + 1)
    return text
