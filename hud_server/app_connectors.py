"""macOS Reminders and Calendar connection layer (roadmap #65, first pass)."""
from __future__ import annotations

import configparser
import os
import plistlib
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import server
import sse_stream

_APPS_STATE = "apps.json"

APPS = [
    {
        "id": "reminders",
        "name": "Reminders",
        "icon": "icons/reminders.png",
        "platform": "macos",
        "roadmap": "#65",
        "probe": 'tell application "Reminders" to get name of every list',
    },
    {
        "id": "calendar",
        "name": "Calendar",
        "icon": "icons/apple-calendar.png",
        "platform": "macos",
        "roadmap": "#65",
        "probe": 'tell application "Calendar" to get name of every calendar',
    },
]

_BY_ID = {a["id"]: a for a in APPS}
_UNSUPPORTED_REASON = "Needs macOS (Reminders and Calendar use EventKit)"


def _load_state() -> Dict[str, Any]:
    state = server._load_json(_APPS_STATE)
    return state if isinstance(state, dict) else {}


def _save_state(state: Dict[str, Any]) -> None:
    server._save_json(_APPS_STATE, state)


def _app_view(app: Dict[str, Any], entry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    view = {
        "id": app["id"],
        "name": app["name"],
        "icon": app["icon"],
        "platform": app["platform"],
        "roadmap": app["roadmap"],
    }
    if sys.platform != "darwin":
        view["status"] = "unsupported"
        view["reason"] = _UNSUPPORTED_REASON
        view["connected_at"] = None
        return view
    if entry and entry.get("connected_at"):
        view["status"] = "connected"
        view["reason"] = ""
        view["connected_at"] = entry.get("connected_at")
    else:
        view["status"] = "not_connected"
        view["reason"] = (entry or {}).get("reason", "")
        view["connected_at"] = None
    return view


def discover_apps(platform: Optional[str] = None) -> List[Dict[str, Any]]:
    plat = platform or sys.platform
    state = _load_state()
    items: List[Dict[str, Any]] = []

    if plat == "darwin":
        darwin_dirs = [
            Path("/Applications"),
            Path(os.path.expanduser("~/Applications")),
            Path("/System/Applications"),
        ]
        if os.environ.get("SUBWAY_APPLICATIONS_DIR"):
            darwin_dirs.append(Path(os.environ["SUBWAY_APPLICATIONS_DIR"]))

        found_apps = []
        for d in darwin_dirs:
            if not d.is_dir():
                continue
            try:
                for app_path in d.glob("*.app"):
                    found_apps.append(app_path)
                for app_path in d.glob("*/*.app"):
                    found_apps.append(app_path)
            except Exception:
                continue

        for app_path in found_apps:
            plist_path = app_path / "Contents" / "Info.plist"
            name = ""
            if plist_path.is_file():
                try:
                    with open(plist_path, "rb") as f:
                        plist = plistlib.load(f)
                    if isinstance(plist, dict):
                        name = plist.get("CFBundleDisplayName") or plist.get("CFBundleName")
                except Exception:
                    pass
            if not name:
                name = app_path.stem
            name = str(name).strip()
            if not name:
                continue
            app_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            if not app_id:
                app_id = re.sub(r"[^a-z0-9]+", "-", app_path.stem.lower()).strip("-")
            if not app_id:
                continue
            if app_id in _BY_ID:
                items.append(_app_view(_BY_ID[app_id], state.get(app_id)))
            else:
                items.append({
                    "id": app_id,
                    "name": name,
                    "icon": "",
                    "platform": "darwin",
                    "status": "available",
                    "reason": "",
                    "connected_at": None,
                })

        seen_ids = {x["id"] for x in items}
        for aid, ainfo in _BY_ID.items():
            if aid not in seen_ids:
                items.append(_app_view(ainfo, state.get(aid)))

    elif plat == "win32":
        win_dirs = []
        for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
            val = os.environ.get(env_key)
            if val:
                win_dirs.append(Path(val))
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            win_dirs.append(Path(local_appdata) / "Programs")

        for d in win_dirs:
            if not d.is_dir():
                continue
            try:
                for entry in d.iterdir():
                    if entry.is_file() and entry.suffix.lower() == ".exe":
                        stem = entry.stem
                        app_id = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
                        if app_id:
                            items.append({
                                "id": app_id,
                                "name": stem,
                                "icon": "",
                                "platform": "win32",
                                "status": "available",
                                "reason": "",
                                "connected_at": None,
                            })
                    elif entry.is_dir():
                        stem = entry.name
                        app_id = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
                        if app_id:
                            items.append({
                                "id": app_id,
                                "name": stem,
                                "icon": "",
                                "platform": "win32",
                                "status": "available",
                                "reason": "",
                                "connected_at": None,
                            })
            except Exception:
                continue

        appdata = os.environ.get("APPDATA", "")
        if appdata:
            start_menu = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if start_menu.is_dir():
                try:
                    for ext in ("*.lnk", "*.url"):
                        for f in start_menu.rglob(ext):
                            stem = f.stem
                            app_id = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
                            if app_id:
                                items.append({
                                    "id": app_id,
                                    "name": stem,
                                    "icon": "",
                                    "platform": "win32",
                                    "status": "available",
                                    "reason": "",
                                    "connected_at": None,
                                })
                except Exception:
                    pass

    else:
        linux_dirs = [
            Path(os.path.expanduser("~/.local/share/applications")),
            Path("/usr/share/applications"),
        ]
        if os.environ.get("SUBWAY_LINUX_APPS_DIR"):
            linux_dirs.append(Path(os.environ["SUBWAY_LINUX_APPS_DIR"]))

        for d in linux_dirs:
            if not d.is_dir():
                continue
            try:
                desktop_files = list(d.glob("*.desktop")) + list(d.glob("*/*.desktop"))
            except Exception:
                desktop_files = []
            for df in desktop_files:
                name = ""
                icon = ""
                nodisplay = False
                hidden = False
                try:
                    parser = configparser.RawConfigParser(interpolation=None)
                    parser.read(str(df), encoding="utf-8")
                    if parser.has_section("Desktop Entry"):
                        sec = parser["Desktop Entry"]
                        nodisplay = sec.get("NoDisplay", "").strip().lower() == "true"
                        hidden = sec.get("Hidden", "").strip().lower() == "true"
                        name = sec.get("Name", "").strip()
                        icon = sec.get("Icon", "").strip()
                except Exception:
                    pass
                if not name and not nodisplay and not hidden:
                    try:
                        in_de = False
                        with open(df, "r", encoding="utf-8", errors="replace") as f:
                            for line in f:
                                line = line.strip()
                                if line.startswith("[") and line.endswith("]"):
                                    in_de = (line == "[Desktop Entry]")
                                    continue
                                if not in_de or "=" not in line:
                                    continue
                                k, v = line.split("=", 1)
                                k, v = k.strip(), v.strip()
                                if k == "NoDisplay" and v.lower() == "true":
                                    nodisplay = True
                                elif k == "Hidden" and v.lower() == "true":
                                    hidden = True
                                elif k == "Name" and not name:
                                    name = v
                                elif k == "Icon" and not icon:
                                    icon = v
                    except Exception:
                        continue
                if nodisplay or hidden or not name:
                    continue
                app_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                if not app_id:
                    continue
                items.append({
                    "id": app_id,
                    "name": name,
                    "icon": icon,
                    "platform": "linux",
                    "status": "available",
                    "reason": "",
                    "connected_at": None,
                })

    dedup: Dict[str, Dict[str, Any]] = {}
    for app in items:
        aid = app.get("id")
        if not aid:
            continue
        if aid not in dedup:
            dedup[aid] = app
        else:
            if app.get("status") == "connected" and dedup[aid].get("status") != "connected":
                dedup[aid] = app

    return sorted(dedup.values(), key=lambda a: (a.get("name") or "").lower())


def list_apps() -> list:
    apps = discover_apps()
    if sys.platform != "darwin":
        existing = {x["id"] for x in apps}
        for a in APPS:
            if a["id"] not in existing:
                apps.append(_app_view(a))
        apps = sorted(apps, key=lambda a: (a.get("name") or "").lower())
    return apps


def connect(app_id: str) -> Dict[str, Any]:
    app = _BY_ID.get(app_id)
    if not app:
        return {"ok": False, "error": f"unknown app: {app_id}"}
    if sys.platform != "darwin":
        view = _app_view(app)
        return {"ok": False, "error": _UNSUPPORTED_REASON, "app": view}

    probe = app["probe"]
    try:
        proc = subprocess.run(
            ["osascript", "-e", probe],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except Exception as e:
        err = str(e).strip() or "failed to run probe"
        state = _load_state()
        entry = state.get(app_id) or {}
        entry["reason"] = err
        entry.pop("connected_at", None)
        state[app_id] = entry
        _save_state(state)
        view = _app_view(app, entry)
        return {"ok": False, "error": err, "app": view}

    if proc.returncode == 0:
        state = _load_state()
        entry = {"connected_at": time.time()}
        state[app_id] = entry
        _save_state(state)
        view = _app_view(app, entry)
        sse_stream.publish("apps", {"id": app_id, "status": view["status"]})
        return {"ok": True, "app": view}

    err = (proc.stderr or proc.stdout or "connection failed").strip().splitlines()[0]
    state = _load_state()
    entry = state.get(app_id) or {}
    entry["reason"] = err
    entry.pop("connected_at", None)
    state[app_id] = entry
    _save_state(state)
    view = _app_view(app, entry)
    return {"ok": False, "error": err, "app": view}


def disconnect(app_id: str) -> Dict[str, Any]:
    app = _BY_ID.get(app_id)
    if not app:
        return {"ok": False, "error": f"unknown app: {app_id}"}
    state = _load_state()
    state.pop(app_id, None)
    _save_state(state)
    view = _app_view(app, None)
    sse_stream.publish("apps", {"id": app_id, "status": view["status"]})
    return {"ok": True, "app": view}


def app_state(app_id: str) -> tuple[bool, str]:
    """Return (available, reason) for a macOS app connector."""
    app = _BY_ID.get(app_id)
    if not app:
        return False, f"unknown app: {app_id}"
    if sys.platform != "darwin":
        return False, _UNSUPPORTED_REASON
    entry = _load_state().get(app_id)
    if isinstance(entry, dict) and entry.get("connected_at"):
        return True, ""
    return False, f"Connect {app['name']} in the HUD's Apps list"


def _run_jxa(script: str, timeout: float = 15.0) -> list[dict]:
    """Run a JXA osascript returning JSON array; returns [] on empty output."""
    import json
    proc = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "osascript failed").strip().splitlines()[0]
        raise RuntimeError(err)
    raw = (proc.stdout or "").strip()
    if not raw:
        return []
    data = json.loads(raw)
    return data if isinstance(data, list) else []


def check_reminder_due(cond: Dict[str, Any], now: Optional[float] = None) -> tuple[bool, str, Dict[str, Any]]:
    """Check macOS Reminders for an incomplete reminder whose due date <= now."""
    if sys.platform != "darwin":
        return False, f"unsupported: {_UNSUPPORTED_REASON}", {"unsupported": True}
    t_now = time.time() if now is None else float(now)
    list_name = str(cond.get("list_name") or "").strip().lower()
    title_pat = str(cond.get("title_contains") or "").strip().lower()
    script = (
        'const app = Application("Reminders");'
        'const nowMs = Date.now();'
        'const out = [];'
        'for (const lst of app.lists()) {'
        '  const lname = lst.name();'
        '  for (const r of lst.reminders.whose({completed: false})()) {'
        '    const dd = r.dueDate();'
        '    if (!dd) continue;'
        '    const ms = dd.getTime();'
        '    if (ms <= nowMs) out.push({id: String(r.id()), name: String(r.name() || ""), list: String(lname || ""), due_ts: ms / 1000});'
        '  }'
        '}'
        'JSON.stringify(out);'
    )
    items = _run_jxa(script)
    for it in items:
        if not isinstance(it, dict):
            continue
        due_ts = it.get("due_ts")
        if due_ts is not None and float(due_ts) > t_now:
            continue
        lname = str(it.get("list") or "")
        if list_name and list_name not in lname.lower():
            continue
        title = str(it.get("name") or "")
        if title_pat and title_pat not in title.lower():
            continue
        return True, f"Reminder due: {title or 'Untitled'}", {"reminder": it}
    return False, "No due reminders matching filter", {}


def check_calendar_event_starts(cond: Dict[str, Any], now: Optional[float] = None) -> tuple[bool, str, Dict[str, Any]]:
    """Check macOS Calendar for an event starting within lead_minutes of now."""
    if sys.platform != "darwin":
        return False, f"unsupported: {_UNSUPPORTED_REASON}", {"unsupported": True}
    t_now = time.time() if now is None else float(now)
    cal_name = str(cond.get("calendar_name") or "").strip().lower()
    title_pat = str(cond.get("title_contains") or "").strip().lower()
    try:
        lead_s = max(0.0, float(cond.get("lead_minutes") if cond.get("lead_minutes") is not None else 5.0)) * 60.0
    except Exception:
        lead_s = 300.0
    try:
        interval_s = max(15.0, float(cond.get("interval_s") or 60.0))
    except Exception:
        interval_s = 60.0
    script = (
        'const app = Application("Calendar");'
        'const nowMs = Date.now();'
        'const winStart = new Date(nowMs - 300000);'
        'const winEnd = new Date(nowMs + 86400000);'
        'const out = [];'
        'for (const cal of app.calendars()) {'
        '  const cname = cal.name();'
        '  for (const ev of cal.events.whose({_and: [{startDate: {_greaterThanEquals: winStart}}, {startDate: {_lessThanEquals: winEnd}}]})()) {'
        '    const sd = ev.startDate();'
        '    if (!sd) continue;'
        '    out.push({id: String(ev.uid()), summary: String(ev.summary() || ""), calendar: String(cname || ""), start_ts: sd.getTime() / 1000});'
        '  }'
        '}'
        'JSON.stringify(out);'
    )
    events = _run_jxa(script)
    for ev in events:
        if not isinstance(ev, dict):
            continue
        start_ts = ev.get("start_ts")
        if start_ts is None:
            continue
        delta = float(start_ts) - t_now
        if delta > lead_s or delta < -interval_s:
            continue
        cname = str(ev.get("calendar") or "")
        if cal_name and cal_name not in cname.lower():
            continue
        summary = str(ev.get("summary") or "")
        if title_pat and title_pat not in summary.lower():
            continue
        return True, f"Calendar event starting: {summary or 'Untitled'}", {"event": ev}
    return False, "No upcoming calendar events matching filter", {}


def run_action(app_id: str, action: str = "run", script: str = "", title: str = "", date: str = "", notes: str = "", list_name: str = "") -> Dict[str, Any]:
    """Run a headless action against a discovered app (Subway app_action MCP tool).

    Governed by config.json -> apps_headless_mode (off | connected_only | on).
    Shell execution from inside an AppleScript snippet is always blocked.
    """
    try:
        cfg = server._load_json("config.json")
        mode = cfg.get("apps_headless_mode", "connected_only") if isinstance(cfg, dict) else "connected_only"
    except Exception:
        mode = "connected_only"
    if mode == "off":
        return {"ok": False, "error": "headless app actions disabled (apps_headless_mode=off)"}

    try:
        apps = discover_apps()
    except Exception as exc:
        return {"ok": False, "error": f"could not discover apps: {exc}"}

    needle = str(app_id or "").strip().lower()
    app = None
    for candidate in apps:
        if not isinstance(candidate, dict):
            continue
        cid = str(candidate.get("id") or "").lower()
        cname = str(candidate.get("name") or "").lower()
        if needle and needle in (cid, cname):
            app = candidate
            break
    if app is None:
        return {"ok": False, "error": f"app not found: {app_id}"}

    if mode == "connected_only" and app.get("status") != "connected":
        return {"ok": False, "error": f"{app['name']} is not connected (connect it in Connect apps, or set apps_headless_mode=on)"}

    if script and re.search(r"\bdo\s+shell\s+script\b", script, re.I):
        return {"ok": False, "error": "shell execution inside app_action script is not allowed"}

    def _esc(value: Any) -> str:
        return str(value or "").replace("\\", "\\\\").replace('"', '\\"')

    def _run(argv: List[str]) -> Dict[str, Any]:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        except Exception as exc:
            return {"ok": False, "error": f"failed to run app action: {exc}"}
        ok = proc.returncode == 0
        result: Dict[str, Any] = {
            "ok": ok,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "returncode": proc.returncode,
        }
        if not ok and result["stderr"]:
            result["error"] = result["stderr"]
        return result

    if sys.platform == "darwin":
        if action == "open" and not script:
            return _run(["open", "-a", app["name"]])
        if app.get("id") == "calendar" and action in ("create_event", "add_event") and title and not script:
            start_expr = ('date "' + _esc(date) + '"') if date else "current date"
            ap = "\n".join([
                'set evTitle to "' + _esc(title) + '"',
                'set evNotes to "' + _esc(notes) + '"',
                "set startDate to " + start_expr,
                "set endDate to startDate + 1 * hours",
                'tell application "Calendar"',
                "  make new event with properties {summary:evTitle, description:evNotes, start date:startDate, end date:endDate}",
                "end tell",
            ])
            return _run(["osascript", "-e", ap])
        if app.get("id") == "reminders" and action in ("create_reminder", "add_reminder") and title and not script:
            target = ('list "' + _esc(list_name) + '"') if list_name else "default list"
            ap = "\n".join([
                'set rmTitle to "' + _esc(title) + '"',
                'set rmNotes to "' + _esc(notes) + '"',
                'tell application "Reminders"',
                "  make new reminder at " + target + " with properties {name:rmTitle, body:rmNotes}",
                "end tell",
            ])
            return _run(["osascript", "-e", ap])
        if script:
            return _run(["osascript", "-e", script])
        return {"ok": False, "error": "provide action='open'|'create_event'|'create_reminder' or an AppleScript snippet"}

    if action == "open":
        if sys.platform.startswith("linux"):
            return _run(["xdg-open", app["name"]])
        if os.name == "nt":
            return _run(["cmd", "/c", "start", "", app["name"]])
        return {"ok": False, "error": f"app actions not supported on platform: {sys.platform}"}
    return {"ok": False, "error": "app actions only support action='open' on non-macOS platforms"}
