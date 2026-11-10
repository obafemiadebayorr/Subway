from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional

import server as _s
def _clean_track(track: str) -> str:
    return re.sub(r"[^\w-]", "", (track or "").strip().lower()) or "main"


def _track_file(track: str) -> str:
    _s._ensure_subway_dir()
    return os.path.join(_s.TRACKS_DIR, f"{_s._clean_track(track)}.jsonl")


def t_join_track(track: str = "main", station_id: str = "") -> str:
    """Subscribe a station to a shared context track and retrieve current track state."""
    clean_track = _s._clean_track(track)
    det = _s._detected_chat()
    if det is not None:
        det_id = det["id"]
        if station_id and not _s._id_matches(station_id, det_id):
            is_w, _ = _s._is_authorized_worker(station_id, det_id)
            if not is_w:
                return f"Error: caller is '{det_id}', cannot join track as '{station_id}'."
        station_id = station_id or _s._short_id(det_id)
    else:
        station_id = station_id or _s._short_id(_s._self_id())

    sub_file = os.path.join(_s.TRACKS_DIR, f"{clean_track}.subscribers.json")
    _s._ensure_subway_dir()
    with _s._state_lock("tracks"):
        subs: List[str] = []
        with contextlib.suppress(OSError, ValueError):
            with open(sub_file, "r", encoding="utf-8") as f:
                subs = json.load(f)
        if station_id not in subs:
            subs.append(station_id)
            _s._write_json_atomic(sub_file, subs)

    history = _s.t_read_track(track=clean_track, limit=5)
    return (f"[TRACK JOINED] Station `{station_id}` subscribed to shared context track '{clean_track}'. "
            f"Active subscribers: {len(subs)}.\n\n{history}")


def _broadcast_track_internal(message: str, track: str = "main", files: Optional[List[str]] = None,
                              decisions: Optional[List[str]] = None, station_id: str = "",
                              session_id: str = "", parent: Optional[str] = None,
                              is_unverified: bool = False) -> str:
    """Internal helper to write an entry to a track."""
    if not message:
        return "Error: message is required."
    clean_track = _s._clean_track(track)
    if clean_track == _s._ALERTS_TRACK:
        return f"Error: '{_s._ALERTS_TRACK}' is reserved for the destructive-action audit log."

    # A heuristic over what agents report, for the audit log. It can't see commands nobody mentions.
    is_destructive = bool(_s.DESTRUCTIVE_RE.search(message))
    full_message = f"{'[ALERT: DESTRUCTIVE ACTION] ' if is_destructive else ''}{message.strip()}"

    rec = {
        "id": uuid.uuid4().hex[:8],
        "timestamp": time.time(),
        "iso": _dt.datetime.now().astimezone().strftime("%H:%M:%S"),
        "station_id": station_id or _s._short_id(_s._self_id()),
        "session_id": session_id or station_id or _s._short_id(_s._self_id()),
        "message": full_message,
        "is_destructive": is_destructive,
        "files": files or [],
        "decisions": decisions or [],
    }
    if parent:
        rec["parent"] = parent
    if is_unverified:
        rec["unverified"] = True

    with open(_s._track_file(clean_track), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    if is_destructive:
        with contextlib.suppress(OSError):
            with open(_s._track_file(_s._ALERTS_TRACK), "a", encoding="utf-8") as af:
                af.write(json.dumps(rec) + "\n")

    is_milestone = "milestone" in message.lower() or any("milestone" in str(d).lower() for d in (decisions or []))
    if is_milestone:
        try:
            from wake_driver import WakeEvent, wake_chat
            ev = WakeEvent(
                kind="milestone",
                chat_id=clean_track,
                text=message[:500],
                data={"track": clean_track, "files": files or [], "decisions": decisions or []}
            )
            wake_chat(clean_track, ev)
        except Exception:
            pass

    file_note = f" (files: {', '.join(files)})" if files else ""
    sess_id = rec["session_id"]
    stat_id = rec["station_id"]
    sess_note = f" [session:{sess_id}]" if sess_id and sess_id != stat_id else ""
    parent_note = f" (parent: {parent})" if parent else ""
    unv_note = " [unverified]" if is_unverified else ""
    return f"[BROADCAST] Posted to track '{clean_track}' by `{stat_id}`{sess_note}{parent_note}{unv_note}: {full_message}{file_note}"


def t_broadcast_track(message: str, track: str = "main", files: Optional[List[str]] = None,
                      decisions: Optional[List[str]] = None, station_id: str = "",
                      session_id: str = "") -> str:
    """Broadcast a decision, commitment, or status update to a shared multi-agent track."""
    if not message:
        return "Error: message is required."
    clean_track = _s._clean_track(track)
    if clean_track == _s._ALERTS_TRACK:
        return f"Error: '{_s._ALERTS_TRACK}' is reserved for the destructive-action audit log."

    parent = None
    is_unverified = False

    det = _s._detected_chat()
    if det is not None:
        det_id = det["id"]
        if station_id and not _s._id_matches(station_id, det_id):
            return f"Error: caller is '{det_id}', cannot broadcast as '{station_id}'."
        if session_id and not _s._id_matches(session_id, det_id):
            return f"Error: caller is '{det_id}', cannot broadcast as '{session_id}'."

        if not station_id:
            station_id = _s._short_id(det_id)
        if not session_id:
            session_id = det_id
        if not parent:
            w = _s._load_worker(det_id)
            if w and w.get("launched_by"):
                parent = w.get("launched_by")
    else:
        station_id = station_id or session_id or _s._short_id(_s._self_id())
        session_id = session_id or station_id
        is_unverified = True

    return _s._broadcast_track_internal(
        message=message, track=track, files=files, decisions=decisions,
        station_id=station_id, session_id=session_id, parent=parent,
        is_unverified=is_unverified
    )


def read_track_entries(track: str = "main", limit: int = 50, since_timestamp: float = 0.0) -> List[Dict[str, Any]]:
    """Return raw structured entries for a track with session attribution."""
    tf = _s._track_file(track)
    entries: List[Dict[str, Any]] = []
    if not os.path.exists(tf):
        return entries
    with open(tf, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("timestamp", 0) >= since_timestamp:
                entries.append(e)
    return entries[-max(1, int(limit)):]


def t_read_track(track: str = "main", limit: int = 15, since_timestamp: float = 0.0) -> str:
    """Read the latest synchronized decisions and status updates from a shared track."""
    return _s.poll_guard_check("read_track", track, _t_read_track_impl(track, limit, since_timestamp))


def _t_read_track_impl(track: str = "main", limit: int = 15, since_timestamp: float = 0.0) -> str:
    clean_track = _s._clean_track(track)
    recent = _s.read_track_entries(clean_track, limit, since_timestamp)
    if not recent:
        if since_timestamp:
            return f"No entries in track '{clean_track}' since timestamp {since_timestamp}."
        return f"Track '{clean_track}' is currently empty. Broadcast decisions with broadcast_track()."

    from tables import table, truncate_text
    header = f"# Shared Context Track: '{clean_track}' ({len(recent)} recent dispatches)"
    cols = ["time", "station", "message", "files", "decisions"]
    rows = []
    for e in recent:
        iso = e.get("iso") or _dt.datetime.fromtimestamp(e.get("timestamp", time.time())).strftime("%H:%M:%S")
        st = e.get("station_id", "unknown")
        sess = e.get("session_id")
        sess_str = f" [session:{sess}]" if sess and sess != st else ""
        parent_str = f" (parent: {e.get('parent')})" if e.get("parent") else ""
        unv_str = " [unverified]" if e.get("unverified") else ""
        station = f"{st}{sess_str}{parent_str}{unv_str}"
        msg = truncate_text(e.get("message", ""), 40)
        files = truncate_text(", ".join(e.get("files") or []), 40)
        decisions = truncate_text("; ".join(e.get("decisions") or []), 40)
        rows.append([iso, truncate_text(station, 40), msg, files, decisions])
    return f"{header}\n{table(f'dispatches {len(recent)}', cols, rows)}"
