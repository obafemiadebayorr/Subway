"""Install/remove native Claude Code wake hooks for Subway."""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
from typing import Any, Dict, Optional, Tuple


def _settings_path(settings_path: Optional[str] = None) -> str:
    if settings_path:
        return os.path.expanduser(settings_path)
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def _wake_wait_path() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "wake_wait.py"))


def _hook_entry() -> Dict[str, Any]:
    return {
        "type": "command",
        "command": f"{shlex.quote(sys.executable)} {shlex.quote(_wake_wait_path())}",
        "asyncRewake": True,
        "timeout": 86400,
        "rewakeSummary": "Subway event",
    }


def _is_ours(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    cmd = entry.get("command", "")
    return isinstance(cmd, str) and "wake_wait.py" in cmd


def _filter_ours(group: Any) -> Any:
    if not isinstance(group, dict):
        return group
    hooks = group.get("hooks")
    if not isinstance(hooks, list):
        return group
    kept = [h for h in hooks if not _is_ours(h)]
    if kept == hooks:
        return group
    out = dict(group)
    out["hooks"] = kept
    return out


def _install_into(data: Dict[str, Any]) -> Dict[str, Any]:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    hooks = dict(hooks)
    ours = _hook_entry()
    for event in ("SessionStart", "Stop"):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            groups = []
        # remove existing ours from any group
        new_groups = []
        for g in groups:
            if not isinstance(g, dict):
                new_groups.append(g)
                continue
            hs = g.get("hooks")
            if isinstance(hs, list):
                kept = [h for h in hs if not _is_ours(h)]
                if not kept and len(hs) != len(kept):
                    # drop empty group that only contained ours
                    continue
                if kept != hs:
                    ng = dict(g)
                    ng["hooks"] = kept
                    new_groups.append(ng)
                    continue
            new_groups.append(g)
        new_groups.append({"hooks": [ours]})
        hooks[event] = new_groups
    data = dict(data)
    data["hooks"] = hooks
    return data


def _remove_from(data: Dict[str, Any]) -> Dict[str, Any]:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return data
    hooks = dict(hooks)
    for event in ("SessionStart", "Stop"):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        new_groups = []
        for g in groups:
            if not isinstance(g, dict):
                new_groups.append(g)
                continue
            hs = g.get("hooks")
            if isinstance(hs, list):
                kept = [h for h in hs if not _is_ours(h)]
                if len(kept) != len(hs):
                    if kept:
                        ng = dict(g)
                        ng["hooks"] = kept
                        new_groups.append(ng)
                    continue
            new_groups.append(g)
        hooks[event] = new_groups
    data = dict(data)
    data["hooks"] = hooks
    return data


def _load_settings(path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not os.path.exists(path):
        return {}, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        return None, f"Could not read settings: {e}"
    if not raw.strip():
        return {}, None
    try:
        data = json.loads(raw)
    except Exception:
        return None, "Existing settings JSON is invalid; refusing to write."
    if not isinstance(data, dict):
        return None, "Existing settings JSON must be an object; refusing to write."
    return data, None


def _atomic_write(path: str, data: Dict[str, Any]) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _backup(path: str) -> None:
    if os.path.exists(path):
        bak = path + ".subway-bak"
        with open(path, "rb") as src, open(bak, "wb") as dst:
            dst.write(src.read())


def install(settings_path: Optional[str] = None):
    path = _settings_path(settings_path)
    data, err = _load_settings(path)
    if err:
        return {"ok": False, "error": err, "path": path}
    new = _install_into(data or {})
    try:
        _backup(path)
        _atomic_write(path, new)
    except Exception as e:
        return {"ok": False, "error": str(e), "path": path}
    return {"ok": True, "installed": True, "path": path}


def remove(settings_path: Optional[str] = None):
    path = _settings_path(settings_path)
    data, err = _load_settings(path)
    if err:
        return {"ok": False, "error": err, "path": path}
    new = _remove_from(data or {})
    try:
        _backup(path)
        _atomic_write(path, new)
    except Exception as e:
        return {"ok": False, "error": str(e), "path": path}
    return {"ok": True, "installed": False, "path": path}


def status(settings_path: Optional[str] = None):
    path = _settings_path(settings_path)
    data, err = _load_settings(path)
    if err or not isinstance(data, dict):
        return {"installed": False, "path": path, "error": err}
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return {"installed": False, "path": path}
    found = False
    for event in ("SessionStart", "Stop"):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        for g in groups:
            if not isinstance(g, dict):
                continue
            hs = g.get("hooks")
            if isinstance(hs, list) and any(_is_ours(h) for h in hs):
                found = True
    return {"installed": found, "path": path}
