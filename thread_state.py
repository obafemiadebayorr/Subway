"""Per-thread metadata the user sets from the HUD: pin, settle, snooze, archive, rename, tag.

Kept apart from the append-only log (threads.py) and the provider transcript. It is a small JSON
map at <base_dir>/thread_state.json, written atomically under an flock so the HUD, the CLI and any
other process can read and update it without losing an edit. Standard library only.
"""
from __future__ import annotations

import contextlib
import subway_home
import glob
import json
import os
import time
from typing import Any, Dict, List, Optional

try:
    import fcntl
except ImportError:  # Windows: atomic replace still protects readers; the lock is best-effort.
    fcntl = None  # type: ignore[assignment]

ACTIONS = {"pin", "settle", "snooze", "archive", "rename", "tag", "delete", "principal", "set_principal", "auto_handoff"}
_META_KEYS = (
    "pinned",
    "settled",
    "archived",
    "snoozed_until",
    "title",
    "tags",
    "deleted",
    "updated_at",
    "principal_id",
    "initiator_id",
    "principal_history",
    "auto_handoff",
    "fallback_principal",
    "flagged",
    "rate_limited",
)


def _base_dir(base_dir: Optional[str] = None) -> str:
    if base_dir:
        return base_dir
    try:
        import server as _s
        return str(_s.SUBWAY_DIR)
    except Exception:
        return subway_home.subway_dir()


def _path(base_dir: str) -> str:
    return os.path.join(base_dir, "thread_state.json")


def _lock_path(base_dir: str) -> str:
    return os.path.join(base_dir, ".thread_state.lock")


@contextlib.contextmanager
def _locked(base_dir: str):
    os.makedirs(base_dir, exist_ok=True)
    fd = os.open(_lock_path(base_dir), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        if fcntl is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read(base_dir: str) -> Dict[str, Any]:
    try:
        with open(_path(base_dir), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write(base_dir: str, data: Dict[str, Any]) -> None:
    os.makedirs(base_dir, exist_ok=True)
    tmp = f"{_path(base_dir)}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _path(base_dir))
    except OSError:
        with contextlib.suppress(OSError):
            if os.path.exists(tmp):
                os.remove(tmp)


def _copy(entry: Any) -> Dict[str, Any]:
    return dict(entry) if isinstance(entry, dict) else {}


def public(entry: Any) -> Dict[str, Any]:
    """Only the fields a client needs, with stable defaults so the page never has to guess."""
    e = _copy(entry)
    return {
        "pinned": bool(e.get("pinned")),
        "settled": bool(e.get("settled")),
        "archived": bool(e.get("archived")),
        "deleted": bool(e.get("deleted")),
        "snoozed_until": float(e.get("snoozed_until") or 0),
        "title": str(e.get("title") or ""),
        "tags": [str(t) for t in (e.get("tags") or []) if str(t).strip()][:20],
        "principal_id": str(e.get("principal_id") or ""),
        "initiator_id": str(e.get("initiator_id") or ""),
        "initiator_history": list(e.get("initiator_history") or []),
        "principal_history": list(e.get("principal_history") or []),
        "auto_handoff": bool(e.get("auto_handoff")),
        "fallback_principal": str(e.get("fallback_principal") or ""),
        "flagged": bool(e.get("flagged")),
        "rate_limited": bool(e.get("rate_limited")),
        "updated_at": float(e.get("updated_at") or 0),
    }


def load_all(base_dir: str) -> Dict[str, Dict[str, Any]]:
    """Every thread's metadata, keyed by thread id, as public dicts."""
    return {str(k): public(v) for k, v in _read(base_dir).items()}


def get(base_dir: str, thread_id: str) -> Dict[str, Any]:
    return public(_read(base_dir).get(str(thread_id or "")))


def get_thread_settings(thread_id: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve thread settings and state for a thread root."""
    bdir = base_dir
    if not bdir:
        try:
            import server as _s
            bdir = getattr(_s, "SUBWAY_DIR", None)
        except ImportError:
            bdir = None
    if not bdir:
        bdir = subway_home.subway_dir()
    res = dict(get(bdir, thread_id))
    try:
        import server as _s
        overrides = _s._load_json("thread_settings.json")
        if isinstance(overrides, dict) and thread_id in overrides:
            res["settings"] = overrides[thread_id]
            for k, v in overrides[thread_id].items():
                if k not in res or not res[k]:
                    res[k] = v
    except Exception:
        pass
    return res



def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _secs(value: Any) -> int:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(n, 30 * 24 * 3600))


def _tags(value: Any, existing: List[str]) -> List[str]:
    if value is None:
        return existing
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
    elif isinstance(value, (list, tuple)):
        parts = [str(p).strip() for p in value]
    else:
        parts = []
    out = [p for p in parts if p]
    seen: List[str] = []
    for t in out:
        if t not in seen:
            seen.append(t)
    return seen[:20]


def _rebind_thread_wake_spools(base_dir: str, thread_id: str, old_principal: str, new_principal: str) -> List[str]:
    """Move 1: Rebind wake spools and worker principal pointers."""
    modified_workers: List[str] = []
    workers_dir = os.path.join(base_dir, "workers")
    if os.path.isdir(workers_dir):
        for fname in os.listdir(workers_dir):
            if fname.endswith(".json"):
                fpath = os.path.join(workers_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        wdata = json.load(f)
                    if isinstance(wdata, dict):
                        w_thread = str(wdata.get("thread_root") or wdata.get("thread") or "")
                        if w_thread == thread_id or fname[:-5] == thread_id:
                            wdata["launched_by"] = new_principal
                            wdata["principal_id"] = new_principal
                            tmp = f"{fpath}.{os.getpid()}.{time.time_ns()}.tmp"
                            with open(tmp, "w", encoding="utf-8") as f:
                                json.dump(wdata, f, indent=2, ensure_ascii=False)
                            os.replace(tmp, fpath)
                            wid = str(wdata.get("id") or fname[:-5])
                            modified_workers.append(wid)
                except Exception:
                    continue

    for sdir_name in ("wakes", "wake_spools"):
        spools_dir = os.path.join(base_dir, sdir_name)
        if not os.path.isdir(spools_dir):
            continue
        old_clean = (old_principal or "").lstrip("@").strip()
        new_clean = (new_principal or "").lstrip("@").strip()
        if old_clean and new_clean:
            old_file = os.path.join(spools_dir, f"{old_clean}.jsonl")
            new_file = os.path.join(spools_dir, f"{new_clean}.jsonl")
            if os.path.isfile(old_file):
                retained_lines: List[str] = []
                moved_lines: List[str] = []
                try:
                    with open(old_file, "r", encoding="utf-8") as f:
                        for line in f:
                            raw = line.strip()
                            if not raw:
                                continue
                            try:
                                item = json.loads(raw)
                                if str(item.get("thread_id") or item.get("thread") or "") == thread_id:
                                    item["target"] = new_principal
                                    moved_lines.append(json.dumps(item))
                                else:
                                    retained_lines.append(raw)
                            except Exception:
                                retained_lines.append(raw)
                    if moved_lines:
                        with open(new_file, "a", encoding="utf-8") as f:
                            for ml in moved_lines:
                                f.write(ml + "\n")
                        with open(old_file, "w", encoding="utf-8") as f:
                            for rl in retained_lines:
                                f.write(rl + "\n")
                except Exception:
                    pass
    return modified_workers


def _reroute_thread_permissions(base_dir: str, thread_id: str, old_principal: str, new_principal: str) -> List[str]:
    """Move 2: Reroute open permission requests for this thread to new_principal."""
    rerouted: List[str] = []
    perms_dir = os.path.join(base_dir, "permissions")
    if not os.path.isdir(perms_dir):
        return rerouted

    for fname in os.listdir(perms_dir):
        if fname.startswith("perm_") and fname.endswith(".json"):
            fpath = os.path.join(perms_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    rec = json.load(f)
                if not isinstance(rec, dict):
                    continue
                if rec.get("status") != "pending":
                    continue
                r_thread = str(rec.get("thread_id") or rec.get("thread_root") or rec.get("thread") or "")
                r_launcher = str(rec.get("launched_by") or "")
                belongs = (r_thread == thread_id) or (old_principal and r_launcher == old_principal)
                if not belongs and rec.get("worker_id"):
                    wpath = os.path.join(base_dir, "workers", f"{rec['worker_id']}.json")
                    if os.path.isfile(wpath):
                        with open(wpath, "r", encoding="utf-8") as wf:
                            wrec = json.load(wf)
                        if str(wrec.get("thread_root") or wrec.get("thread") or "") == thread_id:
                            belongs = True
                if belongs:
                    rec["launched_by"] = new_principal
                    rec["principal_id"] = new_principal
                    tmp = f"{fpath}.{os.getpid()}.{time.time_ns()}.tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(rec, f, indent=2, ensure_ascii=False)
                    os.replace(tmp, fpath)
                    rerouted.append(rec.get("id") or fname[5:-5])
            except Exception:
                continue
    return rerouted


def _migrate_thread_file_leases(base_dir: str, thread_id: str, old_principal: str, new_principal: str) -> List[str]:
    """Move 3: Migrate active workspace file leases held by old_principal to new_principal."""
    migrated: List[str] = []
    locks_file = os.path.join(base_dir, "locks.json")
    if not os.path.isfile(locks_file):
        return migrated

    try:
        import server as _s
        short_new = _s._short_id(new_principal)
    except Exception:
        short_new = new_principal.lstrip("@")[:12]

    now = time.time()
    try:
        with open(locks_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return migrated
        locks = data.get("locks", {})
        if not isinstance(locks, dict):
            return migrated

        dirty = False
        for path, info in list(locks.items()):
            if not isinstance(info, dict):
                continue
            if info.get("expires_at", 0) <= now:
                continue
            h_id = str(info.get("holder_id") or "")
            holder = str(info.get("holder") or "")
            w_thread = str(info.get("thread_id") or "")
            is_match = (
                (old_principal and (h_id == old_principal or holder == old_principal))
                or (w_thread and w_thread == thread_id)
            )
            if is_match:
                info["holder"] = short_new
                info["holder_id"] = new_principal
                locks[path] = info
                authors = data.setdefault("authors", {})
                authors[path] = short_new
                migrated.append(path)
                dirty = True

        if dirty:
            tmp = f"{locks_file}.{os.getpid()}.{time.time_ns()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, locks_file)
            with contextlib.suppress(Exception):
                import server as _s
                _s._broadcast_track_internal("main", f"Migrated {len(migrated)} file lease(s) to {new_principal}",
                                             files=migrated, decisions=[f"Lease handoff to {new_principal}"])
    except Exception:
        pass
    return migrated


def _send_principal_delta_briefing(
    base_dir: str,
    thread_id: str,
    old_principal: str,
    new_principal: str,
    note: str = "",
    workers: Optional[List[str]] = None,
    leases: Optional[List[str]] = None,
    permissions: Optional[List[str]] = None,
) -> bool:
    """Move 4: Deliver a concise handoff briefing note (≤ 400 tokens / 1600 chars) via t_send_note."""
    if not new_principal:
        return False
    w_list = workers or []
    l_list = leases or []
    p_list = permissions or []

    brief_lines = [
        f"👑 [SUBWAY PRINCIPAL HANDOFF] You are now the Principal for thread {thread_id}.",
        f"Previous: {old_principal or 'none'} | Reason: {note or 'Manual transfer'}",
    ]
    if w_list:
        brief_lines.append(f"Active Workers ({len(w_list)}): {', '.join(w_list[:5])}")
    if l_list:
        brief_lines.append(f"Active Leases ({len(l_list)}): {', '.join(l_list[:5])}")
    if p_list:
        brief_lines.append(f"Pending Permissions ({len(p_list)}): {', '.join(p_list[:5])}")

    briefing = "\n".join(brief_lines)[:1600]

    try:
        import server as _s
        _s.t_send_note(to=new_principal, text=briefing)
        return True
    except Exception:
        # Fallback to direct inbox file write if server tool dispatch isn't available
        try:
            inbox_dir = os.path.join(base_dir, "inbox")
            os.makedirs(inbox_dir, exist_ok=True)
            clean_to = new_principal.lstrip("@").strip()
            inbox_file = os.path.join(inbox_dir, f"{clean_to}.jsonl")
            rec = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "from": "subway-composer",
                "text": briefing,
            }
            with open(inbox_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            return True
        except Exception:
            return False


def set_principal(
    thread_id: str,
    principal_id: str,
    note: str = "",
    auto_handoff: Optional[bool] = None,
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Transfer principal baton to principal_id for thread_id, executing the Four Moves."""
    tid = str(thread_id or "").strip()
    pid = str(principal_id or "").strip()
    if not tid:
        raise ValueError("thread id is required")

    bdir = _base_dir(base_dir)
    now = time.time()
    old_principal = ""

    with _locked(bdir):
        data = _read(bdir)
        entry = _copy(data.get(tid))
        old_principal = str(entry.get("principal_id") or "")
        entry["principal_id"] = pid
        if auto_handoff is not None:
            entry["auto_handoff"] = bool(auto_handoff)

        history = list(entry.get("principal_history") or [])
        history.append({
            "from_principal": old_principal,
            "to_principal": pid,
            "at": now,
            "note": str(note or ""),
            "reason": str(note or ("Manual transfer" if old_principal else "Initial assignment")),
        })
        entry["principal_history"] = history
        entry["updated_at"] = now
        data[tid] = entry
        _write(bdir, data)

    # Move 1: Rebind wake spools
    rebound_workers = _rebind_thread_wake_spools(bdir, tid, old_principal, pid)

    # Move 2: Reroute pending permissions
    rerouted_perms = _reroute_thread_permissions(bdir, tid, old_principal, pid)

    # Move 3: Migrate file leases
    migrated_leases = _migrate_thread_file_leases(bdir, tid, old_principal, pid)

    # Move 4: Deliver delta briefing
    briefing_sent = _send_principal_delta_briefing(
        bdir, tid, old_principal, pid, note=note,
        workers=rebound_workers, leases=migrated_leases, permissions=rerouted_perms,
    )

    result = f"Principal set to {pid}" if pid else "Principal cleared"
    return {
        "ok": True,
        "result": result,
        "thread_id": tid,
        "principal_id": pid,
        "previous_principal": old_principal,
        "auto_handoff": bool(entry.get("auto_handoff", False)),
        "principal_history": history,
        "rebound_workers": rebound_workers,
        "rerouted_permissions": rerouted_perms,
        "migrated_leases": migrated_leases,
        "briefing_sent": briefing_sent,
        "meta": public(entry),
    }


def set_initiator(
    thread_id: str,
    initiator_id: str,
    note: str = "",
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Set the initiator (lineage root) override for thread_id."""
    tid = str(thread_id or "").strip()
    iid = str(initiator_id or "").strip()
    if not tid:
        raise ValueError("thread id is required")
    if not iid:
        raise ValueError("initiator_id is required")

    bdir = _base_dir(base_dir)
    now = time.time()
    old_initiator = ""

    with _locked(bdir):
        data = _read(bdir)
        entry = _copy(data.get(tid))
        old_initiator = str(entry.get("initiator_id") or "")
        entry["initiator_id"] = iid

        history = list(entry.get("initiator_history") or [])
        history.append({
            "from_initiator": old_initiator,
            "to_initiator": iid,
            "at": now,
            "note": str(note or ""),
            "reason": str(note or ("Manual transfer" if old_initiator else "Initial assignment")),
        })
        entry["initiator_history"] = history
        entry["updated_at"] = now
        data[tid] = entry
        _write(bdir, data)

    result = f"Initiator set to {iid}" if iid else "Initiator cleared"
    return {
        "ok": True,
        "result": result,
        "thread": tid,
        "old": old_initiator,
        "new": iid,
        "initiator_history": history,
        "meta": public(entry),
    }


def t_set_initiator(thread_id: str, initiator_id: str, note: str = "") -> str:
    """MCP tool wrapper for set_initiator."""
    res = set_initiator(thread_id, initiator_id, note=note)
    if res.get("error"):
        return "Error: " + str(res["error"])
    out = f"[INITIATOR] {res.get('thread')} -> {res.get('new')}"
    if note:
        out += f" ({note})"
    return out


def t_set_principal(thread_id: str, principal_id: str, note: str = "", auto_handoff: Optional[bool] = None) -> str:
    """MCP tool wrapper for set_principal."""
    res = set_principal(thread_id, principal_id, note=note, auto_handoff=auto_handoff)
    if res.get("error"):
        return "Error: " + str(res["error"])
    out = f"[PRINCIPAL] {res.get('thread_id')} -> {res.get('principal_id')}"
    if note:
        out += f" ({note})"
    return out


def _provider_for_principal(principal_id: str) -> str:
    """Derive provider from principal alias, id, or resolved chat."""
    pid = (principal_id or "").lower().strip()
    try:
        import server as _s
        chat = _s.resolve(principal_id, strict=False)
        if chat and isinstance(chat, dict) and chat.get("source"):
            return str(chat["source"]).lower()
    except Exception:
        pass
    if "claude" in pid:
        return "claude"
    if "agy" in pid or "gemini" in pid or "deep" in pid or "antigravity" in pid:
        return "antigravity"
    if "opencode" in pid:
        return "opencode"
    if "copilot" in pid:
        return "copilot"
    return "claude"


def check_auto_handoff(thread_id: str, base_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Check if thread needs auto-handoff due to flagging/rate-limiting or quota < 15%."""
    tid = str(thread_id or "").strip()
    if not tid:
        return None

    bdir = _base_dir(base_dir)
    entry = get(bdir, tid)
    if not entry.get("auto_handoff"):
        return None

    curr_principal = str(entry.get("principal_id") or "")
    if not curr_principal:
        return None

    trigger_reason = ""
    if entry.get("rate_limited"):
        trigger_reason = "Thread is rate limited"
    elif entry.get("flagged"):
        trigger_reason = "Thread is flagged"
    else:
        # Check live quota from _s._live_provider_quotas() or load_quotas
        quotas: Dict[str, float] = {}
        try:
            import server as _s
            if hasattr(_s, "_live_provider_quotas"):
                quotas = _s._live_provider_quotas()
            else:
                data = _s.load_quotas(refresh=False)
                for p in data.get("providers", []):
                    pname = str(p.get("id") or p.get("name") or "").lower()
                    for pool in p.get("pools") or []:
                        for w in pool.get("windows") or []:
                            if not w.get("disabled") and w.get("left_pct") is not None:
                                val = float(w["left_pct"])
                                if pname not in quotas or val < quotas[pname]:
                                    quotas[pname] = val
        except Exception:
            pass

        provider = _provider_for_principal(curr_principal)
        if provider in quotas and quotas[provider] < 15.0:
            trigger_reason = f"Provider '{provider}' quota low ({quotas[provider]}% < 15%)"

    if not trigger_reason:
        return None

    # Determine fallback principal
    fallback = str(entry.get("fallback_principal") or "").strip()
    if not fallback or fallback == curr_principal:
        # Select another active / connected provider station
        candidates = ["@deep", "@claude-main", "@opencode-latest", "@agy-live", "hud-user"]
        fallback = next((c for c in candidates if c != curr_principal), "hud-user")

    return set_principal(
        thread_id=tid,
        principal_id=fallback,
        note=f"Auto-handoff: {trigger_reason}",
        auto_handoff=True,
        base_dir=bdir,
    )


def apply_action(base_dir: str, thread_id: str, action: str, value: Any = None) -> Dict[str, Any]:
    """Apply one user action and return {'ok', 'result', 'meta'}. Raises ValueError on bad input."""
    tid = str(thread_id or "").strip()
    if not tid:
        raise ValueError("thread id is required")
    kind = str(action or "").strip().lower()
    if kind not in ACTIONS:
        raise ValueError(f"unknown action '{action}'")
    now = time.time()

    if kind in ("principal", "set_principal"):
        pid = str(value or "").strip()
        handoff_res = set_principal(thread_id=tid, principal_id=pid, base_dir=base_dir)
        return {"ok": True, "result": handoff_res["result"], "meta": handoff_res["meta"]}

    with _locked(base_dir):
        data = _read(base_dir)
        entry = _copy(data.get(tid))
        result = ""
        if kind == "pin":
            entry["pinned"] = _truthy(value)
            result = "Pinned" if entry["pinned"] else "Unpinned"
        elif kind == "settle":
            entry["settled"] = _truthy(value)
            result = "Settled" if entry["settled"] else "Reopened"
        elif kind == "archive":
            entry["archived"] = _truthy(value)
            result = "Archived" if entry["archived"] else "Unarchived"
        elif kind == "snooze":
            secs = _secs(value)
            entry["snoozed_until"] = now + secs if secs else 0
            result = f"Snoozed for {secs // 60}m" if secs else "Unsnoozed"
        elif kind == "rename":
            name = str(value or "").strip()[:200]
            if name:
                entry["title"] = name
            else:
                entry.pop("title", None)
            result = "Renamed"
        elif kind == "tag":
            entry["tags"] = _tags(value, [str(t) for t in (entry.get("tags") or [])])
            result = "Tags updated"
        elif kind == "delete":
            entry["deleted"] = _truthy(value)
            result = "Deleted" if entry["deleted"] else "Restored"
        elif kind == "set_principal":
            pid = str(value or "").strip()
            entry["principal_id"] = pid
            result = f"Principal set to {pid}" if pid else "Principal cleared"
        elif kind == "auto_handoff":
            entry["auto_handoff"] = _truthy(value)
            result = "Auto-handoff enabled" if entry["auto_handoff"] else "Auto-handoff disabled"
        entry["updated_at"] = now
        active_flags = ("pinned", "settled", "archived", "deleted", "snoozed_until", "title", "principal_id", "auto_handoff")
        if all(not entry.get(k) for k in active_flags) and not entry.get("tags"):
            data.pop(tid, None)
        else:
            data[tid] = entry
        _write(base_dir, data)
        return {"ok": True, "result": result, "meta": public(entry)}
