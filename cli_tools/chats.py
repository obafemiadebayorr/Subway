from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Union

import threads

import server as _s
def _t_list_chats_local(source: str = "all", project: str = "", query: str = "", limit: int = 15,
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


def t_list_chats(source: str = "all", project: str = "", query: str = "", limit: int = 15,
                 include_subagents: bool = False) -> str:
    out = _t_list_chats_local(source, project, query, limit, include_subagents)
    import peers as _peers
    if not _peers.list_peers():
        return out
    from tables import table
    rows = []
    for r in _peers.remote_chats(query, limit):
        if r.get("offline"):
            rows.append([r["station"], "-", "not granted" if r.get("denied") else "offline", "-"])
        else:
            rows.append([r["station"], f"{r['alias'] or r['id'][:12]}@{r['station']}", r["title"][:60], r["source"]])
    if not rows:
        return out
    linked = table(f"linked stations {len(rows)}", ["Station", "ID", "Title", "Source"], rows)
    if out.startswith("No chats found"):
        out = ""
    return (out + "\n\n" if out else "") + linked + "\n\nRead a chat on a linked station with read_chat('@alias@station')."


def _t_search_chat_local(quote: str = "", query: str = "", source: str = "all", project: str = "", limit: int = 5,
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


def t_search_chat(quote: str = "", query: str = "", source: str = "all", project: str = "", limit: int = 5,
                  include_subagents: bool = True, segment: Union[str, int] = "all", peers_too: bool = True) -> str:
    out = _t_search_chat_local(quote, query, source, project, limit, include_subagents, segment)
    target = (quote or query or "").strip()
    if peers_too and target:
        import peers as _peers
        extra = []
        for p in _peers.list_peers():
            peer = _peers.get(p["peer_id"])
            if not peer:
                continue
            res = _peers.remote_get(peer, "/api/peer/search_chat", {"quote": target, "limit": limit})
            if res.get("status") == 200:
                st = _peers.slug(peer)
                extra.append(f"--- on {st} (ids end with @{st}) ---\n" + str((res.get("data") or {}).get("text") or ""))
        if extra:
            out = out + "\n\nLinked stations\n" + "\n\n".join(extra)
    return out


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


def resolve_include_thoughts(arg: Optional[bool] = None) -> bool:
    """An explicit include_thoughts wins; otherwise the thoughts_mode setting decides (default: include)."""
    if arg is not None:
        return bool(arg)
    try:
        from hud_server import settings_api
        return settings_api.value("thoughts_mode") != "exclude"
    except Exception:
        return True


def t_read_chat(chat_id: str, mode: str = "recent", turns: int = 6, start: Optional[int] = None,
                since: str = "", anchor_text: str = "",
                max_chars: int = _s.DEFAULT_MAX_CHARS, include_tools: bool = False,
                include_thoughts: Optional[bool] = None, source: str = "all", project: str = "",
                segment: Union[str, int] = "all", local_only: bool = False) -> str:
    if not local_only:
        import peers as _peers
        r = _peers.split_remote(chat_id)
        if r:
            head, peer = r
            st = _peers.slug(peer)
            res = _peers.call(peer["peer_id"], "GET", "/api/peer/read_chat?" + __import__("urllib.parse").parse.urlencode(
                {"chat_id": head, "mode": mode, "turns": turns, "since": since or "", "anchor_text": anchor_text or ""}))
            if res.get("status") == 200:
                return f"[on {st}] " + str((res.get("data") or {}).get("text") or "")
            if res.get("status") == 403:
                return f"{st} has not granted reading chats to this station."
            return f"{st} is offline."
    if mode not in ("recent", "outline", "full", "decisions"):
        raise ValueError("mode must be 'recent', 'outline', 'full', or 'decisions'")
    include_thoughts = resolve_include_thoughts(include_thoughts)
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


def _chat_exchanges(chat_id: str, source: str) -> tuple:
    """Resolve a chat (honouring exclusions) and return (chat dict, exchanges)."""
    c = _s.resolve(chat_id, source, "")
    if threads.is_chat_excluded(c, base_dir=_s.SUBWAY_DIR):
        raise ValueError(f"Chat {chat_id} is excluded by app/project exclusion rules")
    info = _s.chat_segments_info(c, "all")
    return c, _s.to_exchanges(info[0])


def chat_turns(chat_id: str, source: str = "all", turns: int = 20, max_chars: int = 4000) -> dict:
    """Recent user/assistant turns for the HUD: user text plus the assistant's final text only."""
    c, ex = _chat_exchanges(chat_id, source)
    limit = max(1, int(turns))
    limit_chars = max(0, int(max_chars))
    out: List[Dict[str, Any]] = []
    for e in ex[-limit:]:
        ts = e.get("ts")
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            ts = float(ts)
        elif isinstance(ts, str) and ts:
            # Claude transcripts carry ISO timestamps; without them a moved conversation sorts these turns first.
            try:
                from transplant.ir import norm_ts
                ts = norm_ts(ts)
            except Exception:
                ts = None
        else:
            ts = None
        user_text = str(e.get("user") or "").strip()
        if user_text:
            out.append({"role": "user", "text": user_text[:limit_chars], "ts": ts})
        final = str(e["assistant"][-1]) if e.get("assistant") else ""
        if final.strip():
            out.append({"role": "assistant", "text": final[:limit_chars], "ts": ts})
    return {
        "id": c.get("id", chat_id),
        "source": c.get("source", source),
        "title": str(c.get("title") or ""),
        "turns": out,
        "total": len(ex),
    }


def _tool_line(t: Any) -> str:
    """One short line describing a tool step: 'Name: arg' (<=120 chars)."""
    if isinstance(t, dict):
        name = str(t.get("name") or t.get("tool") or "tool")
        inp = t.get("input") if isinstance(t.get("input"), dict) else {}
        arg = ""
        for k in ("command", "file_path", "path", "pattern", "url", "query", "description"):
            v = inp.get(k) or t.get(k)
            if v:
                arg = str(v)
                break
        s = f"{name}: {arg}" if arg else name
    else:
        s = str(t)
    return " ".join(s.split())[:120]


def chat_live(chat_id: str, source: str = "all", turns: int = 8, max_chars: int = 6000) -> dict:
    """chat_turns plus the in-progress tail: what the agent is doing right now."""
    out = chat_turns(chat_id, source, turns=turns, max_chars=max_chars)
    c, ex = _chat_exchanges(chat_id, source)
    last = ex[-1] if ex else {}
    user = str(last.get("user") or "").strip()
    assistant = [str(a) for a in (last.get("assistant") or [])]
    tools = list(last.get("tools") or [])
    limit_chars = max(0, int(max_chars))
    latest = next((a for a in reversed(assistant) if a.strip()), "")
    out["live"] = {
        "working": bool(user) and not any(a.strip() for a in assistant),
        "activity": [_tool_line(t) for t in tools[-5:]],
        "text": latest[-limit_chars:] if limit_chars else "",
        "user": user[:300],
    }
    out["path"] = str(c.get("path") or "")
    return out
