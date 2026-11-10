"""Universal proactive chat wakeup driver (Roadmap Item 47).

Spools and delivers wakeup events across AI coding clients (OpenCode, Claude Code, Antigravity).
"""
import os
import json
import re
import time
import sqlite3
import threading
import contextlib
try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List
import server as _s

@dataclass
class WakeEvent:
    kind: str
    worker_id: str = ""
    chat_id: str = ""
    text: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

def get_spool_dir() -> str:
    spool_dir = os.path.join(_s.SUBWAY_DIR, "wake_spools")
    os.makedirs(spool_dir, exist_ok=True)
    return spool_dir

def wake_spool_enqueue(chat_id: str, event: WakeEvent) -> bool:
    if not chat_id:
        return False
    safe_id = "".join(c for c in chat_id if c.isalnum() or c in ("-", "_", "@", "."))
    spool_file = os.path.join(get_spool_dir(), f"{safe_id}.jsonl")
    payload = asdict(event)
    payload["timestamp"] = payload.get("timestamp") or time.time()
    try:
        with open(spool_file, "a", encoding="utf-8") as f:
            if fcntl:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(json.dumps(payload) + "\n")
            if fcntl:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return True
    except Exception:
        return False

def wake_spool_drain(chat_id: str) -> List[Dict[str, Any]]:
    if not chat_id:
        return []
    safe_id = "".join(c for c in chat_id if c.isalnum() or c in ("-", "_", "@", "."))
    spool_file = os.path.join(get_spool_dir(), f"{safe_id}.jsonl")
    if not os.path.exists(spool_file):
        return []
    events = []
    try:
        with open(spool_file, "r+", encoding="utf-8") as f:
            if fcntl:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        pass
            f.seek(0)
            f.truncate(0)
            if fcntl:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    return events

def wake_spool_depth(chat_id: str) -> int:
    if not chat_id:
        return 0
    safe_id = "".join(c for c in chat_id if c.isalnum() or c in ("-", "_", "@", "."))
    spool_file = os.path.join(get_spool_dir(), f"{safe_id}.jsonl")
    if not os.path.exists(spool_file):
        return 0
    count = 0
    try:
        with open(spool_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
    except Exception:
        pass
    return count

# One worker event reaches a chat from two places: outcomes._update_worker_status wakes with
# kind "worker_<status>", and worker_supervision._wake_worker_milestone wakes again with
# "worker_milestone". Nothing deduplicated them, so a single worker ending produced two
# bubbles in the same chat (and, in one report, 7 events for 3 workers).
WAKE_DEDUPE_SECONDS = 90.0
_recent_wakes: Dict[tuple, float] = {}
_recent_wakes_lock = threading.Lock()


def _wake_dedupe_key(chat_id: str, event: WakeEvent) -> Optional[tuple]:
    """Identity of the underlying event, not of the message that describes it."""
    wid = str(getattr(event, "worker_id", "") or "")
    if not wid:
        return None
    status = str((getattr(event, "data", None) or {}).get("status") or "")
    if not status:
        kind = str(getattr(event, "kind", "") or "")
        status = kind[len("worker_"):] if kind.startswith("worker_") else kind
    return (str(chat_id or ""), wid, status)


def _already_woke_recently(key: Optional[tuple]) -> bool:
    if not key:
        return False
    now = time.time()
    with _recent_wakes_lock:
        cutoff = now - WAKE_DEDUPE_SECONDS
        for stale in [k for k, ts in _recent_wakes.items() if ts < cutoff]:
            _recent_wakes.pop(stale, None)
        if key in _recent_wakes:
            return True
        _recent_wakes[key] = now
    return False


AGY_WAKE_COOLDOWN_SECONDS = float(os.environ.get("SUBWAY_AGY_WAKE_COOLDOWN", "5.0"))
_last_agy_wake: Dict[str, float] = {}
last_agy_wake_lock = threading.Lock()


def agy_wake_allowed(chat_id: str, now: Optional[float] = None) -> bool:
    """At most one real Antigravity turn per chat per cooldown.

    A wake starts a turn, that turn can spawn workers, and their completions wake again.
    Without this a busy chain would drive an unbounded loop of paid turns.
    """
    now = time.time() if now is None else now
    with last_agy_wake_lock:
        previous = _last_agy_wake.get(chat_id, 0.0)
        if previous and now - previous < AGY_WAKE_COOLDOWN_SECONDS:
            return False
        _last_agy_wake[chat_id] = now
        return True


AGY_SUMMARIES_DB = os.path.expanduser("~/.gemini/antigravity/conversation_summaries.db")
AGY_DAEMON_DIR = os.path.expanduser("~/.gemini/antigravity/daemon")
_AGY_CID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def agy_conversation_known(cid: str, db_path: Optional[str] = None) -> bool:
    """True when this id is an Antigravity conversation id.

    A worker launched from an Antigravity chat records `launched_by` as the conversation
    UUID itself (80 of ~190 worker records here), not `agy-<pid>`. Recognising the UUID is
    what makes a per-event wake fire at all for the common case.
    """
    cid = (cid or "").strip().lower()
    if not _AGY_CID_RE.match(cid):
        return False
    path = db_path if db_path is not None else AGY_SUMMARIES_DB
    if not path or not os.path.exists(path):
        return False
    for uri in (f"file:{path}?mode=ro", f"file:{path}?immutable=1"):
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            try:
                return bool(conn.execute(
                    "SELECT 1 FROM conversation_summaries WHERE conversation_id = ? LIMIT 1",
                    (cid,)).fetchone())
            finally:
                conn.close()
        except Exception:
            continue
    return False


def is_agy_chat_id(chat_id: str, db_path: Optional[str] = None) -> bool:
    """Is this chat an Antigravity chat we can wake with `agy --conversation`?"""
    t = (chat_id or "").strip().lstrip("@")
    if not t:
        return False
    if t.startswith("agy-") or "antigravity" in t.lower():
        return True
    return agy_conversation_known(t, db_path=db_path)


def resolve_agy_conversation(project_dir: str = "", db_path: Optional[str] = None, allow_busy: bool = False) -> str:
    """Best conversation id for a project, or "" when there is nothing safe to resume.

    There is no pid->conversation mapping in Antigravity's data, so this picks the most
    recently modified conversation for that workspace. Rows whose source is the CLI are
    Subway's own worker conversations: they are skipped when any non-CLI row exists in the
    same workspace, otherwise a wake would resume a worker instead of the user's chat.
    When allow_busy is True (e.g. for ConnectRPC wakes), active turns are not filtered out.
    """
    # rules, in order:
    #  1. path = <HOME>/.gemini/antigravity/conversation_summaries.db ; if missing return ""
    #  2. select conversation_id, workspace_uris, source, not_fully_idle, last_modified_time
    #  3. if project_dir: keep rows whose workspace_uris contains project_dir, else keep all
    #  4. drop rows where not_fully_idle is truthy (a turn is already running) unless allow_busy is True
    #  5. prefer rows whose source is NULL/empty; if none, fall back to CLI rows
    #  6. order by the max of last_modified_time (text ISO sorts correctly) desc, take 1
    #  7. any exception -> return "" (never raise)
    try:
        path = db_path if db_path is not None else AGY_SUMMARIES_DB
        if not path or not os.path.exists(path):
            return ""

        rows = None
        for uri in (f"file:{path}?mode=ro", f"file:{path}?immutable=1"):
            try:
                conn = sqlite3.connect(uri, uri=True, timeout=5)
                try:
                    rows = conn.execute(
                        "SELECT conversation_id, workspace_uris, source, not_fully_idle, last_modified_time "
                        "FROM conversation_summaries"
                    ).fetchall()
                    break
                finally:
                    conn.close()
            except Exception:
                continue

        if not rows:
            return ""

        # 3. if project_dir: keep rows whose workspace_uris contains project_dir, else keep all
        # Bound the match: '/x/subway-context' must not match '/x/subway-context-refactor'.
        if project_dir:
            p_strip = project_dir.rstrip("/")
            matched = []
            for r in rows:
                ws_str = str(r[1] or "")
                if not p_strip:
                    continue
                if f'{p_strip}"' in ws_str or f"{p_strip}/" in ws_str:
                    matched.append(r)
            rows = matched

        if not rows:
            return ""

        # 4. drop rows where not_fully_idle is truthy (a turn is already running) unless allow_busy is True
        if not allow_busy:
            def _is_busy(val: Any) -> bool:
                if not val:
                    return False
                if isinstance(val, (int, float)):
                    return val != 0
                if isinstance(val, str):
                    return val.strip().lower() not in ("", "0", "false", "null")
                return bool(val)

            rows = [r for r in rows if not _is_busy(r[3])]
            if not rows:
                return ""

        # 5. prefer rows whose source is NULL/empty; if none, fall back to CLI rows
        gui_rows = [r for r in rows if not (r[2] and str(r[2]).strip())]
        if gui_rows:
            rows = gui_rows

        # 6. order by the max of last_modified_time (text ISO sorts correctly) desc, take 1
        best = max(rows, key=lambda r: str(r[4] or ""))
        return str(best[0] or "").strip()
    except Exception:
        return ""


def agy_conversation_id() -> str:
    """`agy --conversation` needs a conversation UUID.

    A woken Antigravity chat is keyed `agy-<pid>` (a window id), so the spool key is never
    a valid argument here. Only an explicit ANTIGRAVITY_CONVERSATION_ID can be used.
    """
    return os.environ.get("ANTIGRAVITY_CONVERSATION_ID", "").strip()


def antigravity_resume(message: str, chat_id: str, project_dir: str = "") -> bool:
    """Wake an Antigravity chat with a real headless turn, the way a user message would.

    `agy --print` with `--conversation`/`--continue` appends a USER_INPUT turn to the
    stored conversation and runs the model on it. Verified on this station: the turn
    lands in brain/<cid>/.system_generated/logs/transcript.jsonl as
    USER_INPUT -> SYSTEM_MESSAGE -> PLANNER_RESPONSE.

    Deliberately not `--output-format stream-json`: that form requires --input-format.
    """
    try:
        import binary_discovery
        agy = binary_discovery.find_agy_binary()
    except Exception:
        return False
    if not agy:
        return False
    cmd = [agy, "--print", message, "--output-format", "json", "--print-timeout", "0"]
    # A chat id that already IS a conversation id needs no guessing at all.
    cid = chat_id.strip().lstrip("@") if agy_conversation_known(chat_id) else ""
    cid = cid or agy_conversation_id() or resolve_agy_conversation(project_dir)
    if cid:
        cmd += ["--conversation", cid]
    else:
        # No UUID for this window, so resume the most recently active conversation.
        cmd += ["--continue"]
    try:
        import subprocess
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:
        return False


def agy_window_registered(pid: int) -> bool:
    """True when an Antigravity window has published a heartbeat for this pid."""
    try:
        path_ = os.path.join(_s.SUBWAY_DIR, "agy_windows", f"{int(pid)}.json")
        if not os.path.exists(path_):
            return False
        age = time.time() - os.path.getmtime(path_)
        return age < 120  # the extension rewrites it every second
    except (OSError, ValueError):
        return False


def agy_window_pid(chat_id: str) -> Optional[int]:
    """`agy-<pid>` names a GUI window, which is the only id the extension can claim."""
    m = re.match(r"^agy-(\d+)$", str(chat_id or "").strip())
    return int(m.group(1)) if m else None


def agy_gui_wake(chat_id: str, text: str) -> bool:
    """Spool text for the Antigravity GUI extension, which calls the app's chat command.

    `agy --print --conversation` writes to ~/.gemini/antigravity-cli/brain, a different
    store from the GUI's ~/.gemini/antigravity/brain, so the CLI cannot reach a GUI chat.
    The GUI can only be reached from inside itself, via
    antigravity.sendPromptToAgentPanel; the extension watches this spool and calls it.
    """
    pid = agy_window_pid(chat_id)
    if not pid or not str(text or "").strip():
        return False
    # Only spool when a window has actually registered. Otherwise the extension is not
    # running (not installed, not reloaded, or the window closed), nothing would read the
    # file, and claiming delivery would be the same silent lie this path replaced.
    if not agy_window_registered(pid):
        return False
    try:
        directory = os.path.join(_s.SUBWAY_DIR, "agy_wake")
        os.makedirs(directory, exist_ok=True)
        path_ = os.path.join(directory, f"{pid}-events.jsonl")
        with open(path_, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"text": str(text), "at": time.time()}) + "\n")
        return True
    except OSError:
        return False


def antigravity_connectrpc_wake(
    chat_id: str,
    message: str,
    project_dir: str = "",
    daemon_dir: Optional[str] = None,
    sender_id: str = "subway",
) -> bool:
    """Wake an Antigravity chat via the local language server daemon ConnectRPC endpoint.

    Pushes directly into CascadeState.PendingAgentMessages via SendAgentMessage, triggering
    a native reactive wake without fake keystrokes or CLI trajectory mismatches.
    """
    clean = (chat_id or "").strip().lstrip("@")
    cid = clean if _AGY_CID_RE.match(clean) and agy_conversation_known(clean) else ""
    if not cid and _AGY_CID_RE.match(clean):
        cid = clean
    if not cid:
        cid = agy_conversation_id()
    if not cid and project_dir:
        cid = resolve_agy_conversation(project_dir, allow_busy=True)
    if not cid and chat_id:
        with contextlib.suppress(Exception):
            hit = _s.resolve(chat_id)
            if hit and hit.get("source") == "antigravity":
                candidate = str(hit.get("id") or "").strip()
                if _AGY_CID_RE.match(candidate):
                    cid = candidate
    if not cid:
        cid = resolve_agy_conversation(os.getcwd(), allow_busy=True)
    if not cid or not _AGY_CID_RE.match(cid):
        return False

    ddir = daemon_dir if daemon_dir is not None else AGY_DAEMON_DIR
    hubs = _s._agy_hubs(daemon_dir=ddir)
    if not hubs:
        return False

    target_pid = agy_window_pid(chat_id)
    if target_pid:
        hubs.sort(key=lambda h: 0 if int(h.get("pid") or 0) == target_pid else 1)

    for hub in hubs:
        try:
            _s._agy_rpc(
                hub,
                "SendAgentMessage",
                data={
                    "recipient": cid,
                    "senderId": sender_id,
                    "content": message,
                },
                timeout=3.0,
            )
            return True
        except Exception:
            continue
    return False


# Event categorization mapping
WAKE_EVENT_KINDS = {
    "iteration_done": "success",
    "worker_done": "success",
    "worker_failed": "failure",
    "worker_stopped": "failure",
    "worker_timed_out": "failure",
    "worker_budget_stopped": "failure",
    "iteration_round": "progress",
    "worker_milestone": "progress",
    "milestone": "progress",
    "permission": "permission",
    "permission_request": "permission",
    "worker_permission": "permission",
    "approval_needed": "approval",
}

WAKE_POLICIES = ("all", "outcomes", "failures", "off")

WAKE_EVENT_RULES = {
    "worker_ended":      {"wake": True,  "actions": ["open", "review", "dismiss"]},
    "worker_failed":     {"wake": True,  "actions": ["review", "rebrief", "dismiss"]},
    "budget_stopped":    {"wake": True,  "actions": ["review", "rebrief", "dismiss"]},
    "permission_needed": {"wake": True,  "actions": ["allow", "deny", "open_hud"]},
    "approval_needed":   {"wake": True,  "actions": ["approve", "reject"]},
    "mail_fired":        {"wake": True,  "actions": ["open"]},
    "minor":             {"wake": False, "actions": []},
}
LOCKED_RULES = {"permission_needed"}  # never overridable: the user always decides


def rule_key_for(kind: str, status: str = "") -> str:
    """Map an event kind/status to a WAKE_EVENT_RULES key."""
    k = str(kind or "").strip().lower()
    s = str(status or "").strip().lower()
    if "approval" in k:
        return "approval_needed"
    if "permission" in k:
        return "permission_needed"
    if "budget" in k or "budget" in s:
        return "budget_stopped"
    if "composer_mail" in k or "mail" in k:
        return "mail_fired"
    category = classify_wake_event(k)
    if "error" in k or category == "failure":
        return "worker_failed"
    if category == "success" or "complet" in k:
        return "worker_ended"
    if category == "permission":
        return "permission_needed"
    if "composer_mail" in k or "mail" in k:
        return "mail_fired"
    return "minor"


def resolve_wake_rule(kind: str, status: str = "", provider: str = "",
                      thread_id: str = "", config: Optional[Dict[str, Any]] = None,
                      thread_settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve a wake rule with precedence: thread > provider > universal > default.

    Partial overrides merge key-by-key over the previous level.
    """
    key = rule_key_for(kind, status)
    if key in LOCKED_RULES:
        rule: Dict[str, Any] = dict(WAKE_EVENT_RULES[key])
        rule.update({"key": key, "source": "default"})
        return rule
    rule = dict(WAKE_EVENT_RULES[key])
    source = "default"

    def _merge(overlay: Any, level: str) -> None:
        nonlocal rule, source
        if isinstance(overlay, dict):
            changed = False
            for kk, vv in overlay.items():
                if kk in ("wake", "actions"):
                    rule[kk] = vv
                    changed = True
            if changed:
                source = level

    if config is None:
        try:
            config = _s._load_json("config.json")
        except Exception:
            config = {}
    if isinstance(config, dict):
        wr = config.get("wake_rules")
        if isinstance(wr, dict):
            _merge(wr.get(key), "universal")
        wbp = config.get("wake_rules_by_provider")
        if isinstance(wbp, dict) and provider:
            prov = wbp.get(str(provider))
            if isinstance(prov, dict):
                _merge(prov.get(key), "provider")

    if thread_id and thread_settings is None:
        try:
            from thread_state import get_thread_settings
            thread_settings = get_thread_settings(thread_id)
        except Exception:
            thread_settings = None
    if isinstance(thread_settings, dict):
        twr = thread_settings.get("wake_rules")
        if isinstance(twr, dict):
            _merge(twr.get(key), "thread")

    rule["key"] = key
    rule["source"] = source
    return rule


def classify_wake_event(kind: str) -> str:
    k = str(kind or "").strip().lower()
    if "permission" in k:
        return "permission"
    return WAKE_EVENT_KINDS.get(k, "progress")


def should_wake_for_policy(policy: str, category: str) -> bool:
    pol = str(policy or "all").strip().lower()
    if pol not in WAKE_POLICIES:
        pol = "all"
    if category == "permission":
        return True
    if pol == "all":
        return True
    if pol == "outcomes":
        return category in ("success", "failure")
    if pol == "failures":
        return category == "failure"
    if pol == "off":
        return False
    return True


def get_wake_policy(chat_id: str = "", config: Optional[Dict[str, Any]] = None) -> str:
    # Per-chat override in thread_settings.json -> config.json key wake_events -> "all"
    if chat_id:
        try:
            ts = _s._load_json("thread_settings.json")
            if isinstance(ts, dict):
                chat_entry = ts.get(chat_id)
                if isinstance(chat_entry, dict):
                    val = chat_entry.get("wake_events")
                    if val and str(val).strip().lower() in WAKE_POLICIES:
                        return str(val).strip().lower()
        except Exception:
            pass
    if config is not None and isinstance(config, dict):
        val = config.get("wake_events")
        if val and str(val).strip().lower() in WAKE_POLICIES:
            return str(val).strip().lower()
    try:
        cfg = _s._load_json("config.json")
        if isinstance(cfg, dict):
            val = cfg.get("wake_events")
            if val and str(val).strip().lower() in WAKE_POLICIES:
                return str(val).strip().lower()
    except Exception:
        pass
    return "all"


def wake_chat(chat_id: str, event: WakeEvent, config: Optional[Dict[str, Any]] = None, project_dir: str = "") -> Dict[str, Any]:
    key = _wake_dedupe_key(chat_id, event)
    if _already_woke_recently(key):
        # Same worker, same status, already delivered: say so rather than waking twice.
        return {"ok": True, "deduped": True, "spooled": False, "delivered": False, "method": "dedupe"}

    policy = get_wake_policy(chat_id, config)
    category = classify_wake_event(getattr(event, "kind", ""))
    if not should_wake_for_policy(policy, category):
        return {"ok": False, "chat_id": chat_id, "spooled": False, "delivered": False, "method": "policy_filtered", "dispatch": {}}

    # Rule layer: fine-grained wake/actions overrides, threaded into notification data
    # so adapters can render action buttons. The policy gate above stays the kill switch.
    rule = resolve_wake_rule(getattr(event, "kind", ""), getattr(event, "status", ""),
                             getattr(event, "provider", ""), chat_id, config=config)

    # Anything queued by an earlier throttled wake is still here; nothing drains the spool,
    # so tell the woken turn how many it is standing in for.
    pending_before = wake_spool_depth(chat_id)
    spooled = wake_spool_enqueue(chat_id, event)
    if not rule["wake"]:
        # Quiet event: it is spooled for the next woken turn, but do not wake anyone now.
        return {"ok": False, "chat_id": chat_id, "spooled": spooled, "delivered": False,
                "method": "policy_filtered", "reason": "rule", "dispatch": {}}
    message_for_agy = event.text or f"Worker {event.worker_id} event: {event.kind}"
    gui_woken = agy_gui_wake(chat_id, message_for_agy) if agy_window_pid(chat_id) else False

    cfg = config
    if cfg is None:
        try:
            cfg = _s._load_json("config.json") if hasattr(_s, "_load_json") else {}
        except Exception:
            cfg = {}

    title = f"Subway Context: {event.kind.replace('_', ' ').title()}"
    message = event.text or f"Worker {event.worker_id} event: {event.kind}"
    if pending_before:
        message = f"{message} (+{pending_before} earlier event(s) queued while this chat was throttled)"
    event_dict: Dict[str, Any] = asdict(event)
    event_dict["chat_id"] = chat_id
    event_dict["rule_key"] = rule["key"]
    event_dict["actions"] = list(rule["actions"])
    event_dict["rule_source"] = rule["source"]

    dispatch_res: Dict[str, bool] = {}
    try:
        from notify_driver import dispatch_notification
        dispatch_res = dispatch_notification(
            title=title,
            message=message,
            kind=event.kind,
            data=event_dict,
            config=cfg
        )
    except Exception:
        pass

    delivered = any(v for k, v in dispatch_res.items() if k != "deduped")
    method = "spool_and_notify"
    if gui_woken:
        delivered = True
        method = "spool_and_notify+agy_gui"

    if chat_id:
        target = chat_id.strip()
        if target.startswith("@claude") or "claude" in target.lower():
            claude_bin = getattr(_s, "find_claude_binary", lambda: None)()
            if claude_bin:
                try:
                    import subprocess
                    wake_prompt = f"[SUBWAY EVENT] {message}"
                    subprocess.Popen([claude_bin, "-p", wake_prompt],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
                    delivered = True
                    method = "claude_resume"
                except Exception:
                    pass
        elif agy_window_pid(target) and gui_woken:
            # A GUI window: the extension delivers it to the agent panel. Do not also run
            # `agy --print --conversation` — that writes to the CLI store, and a GUI
            # conversation id is not a trajectory there ("trajectory not found").
            method = "agy_gui"
        elif is_agy_chat_id(target) or agy_window_pid(target):
            if not agy_wake_allowed(chat_id):
                # Event is already spooled above; it is not lost, just not another paid turn.
                method = "antigravity_throttled"
            elif antigravity_connectrpc_wake(chat_id, f"[SUBWAY EVENT] {message}", project_dir=project_dir, daemon_dir=AGY_DAEMON_DIR):
                delivered = True
                method = "antigravity_connectrpc"
            elif antigravity_resume(f"[SUBWAY EVENT] {message}", chat_id, project_dir=project_dir):
                delivered = True
                method = "antigravity_resume"
            else:
                # No agy binary, or the spawn failed. Say so instead of leaving it looking
                # like a plain notification, so a broken path is visible rather than silent.
                method = "antigravity_failed"

    return {
        "chat_id": chat_id,
        "spooled": spooled,
        "delivered": delivered,
        "method": method,
        "dispatch": dispatch_res
    }
