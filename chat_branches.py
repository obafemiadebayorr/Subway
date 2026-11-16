"""Chat branches (roadmap 62b): a chat that edits gets its own branch and worktree.

Workers and units already run isolated; chats edit the user's tree directly, so two chats can trample
each other. chat_branch(action="start") gives the calling chat a branch and a worktree under
SUBWAY_DIR/worktrees; it edits there and land_chat() lands it through the same path as workers
(serialized, 3-way merge, overlaps recorded for a resolver). In Claude Code the guard hook enforces it:
a chat with an open branch can't edit the original tree, and with `chat_branch_auto` on, a second chat
editing a tree another chat is editing must take a branch first.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any, Dict, Optional

import server as _s

EDITOR_WINDOW_S = 600  # another chat's edit within this window counts as "editing this tree"


def _path() -> str:
    return os.path.join(_s.SUBWAY_DIR, "chat_branches.json")


def _load() -> Dict[str, Any]:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("branches"), dict):
        data["branches"] = {}
    if not isinstance(data.get("editors"), dict):
        data["editors"] = {}
    return data


def _save(data: Dict[str, Any]) -> None:
    os.makedirs(_s.SUBWAY_DIR, exist_ok=True)
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, _path())


def _git(repo: str, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=60)
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or f"git {args[0]} failed").strip().splitlines()[0])
    return p.stdout.strip()


def _toplevel(path: str) -> str:
    d = path if os.path.isdir(path) else os.path.dirname(path)
    while d and not os.path.isdir(d):
        d = os.path.dirname(d)
    if not d:
        return ""
    try:
        return os.path.realpath(_git(d, "rev-parse", "--show-toplevel"))
    except Exception:
        return ""


def _worktrees_root() -> str:
    return os.path.realpath(os.path.join(_s.SUBWAY_DIR, "worktrees"))


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def auto_enabled() -> bool:
    try:
        from hud_server import settings_api
        return bool(settings_api.value("chat_branch_auto"))
    except Exception:
        return False


def start(station: str, repo_hint: str) -> str:
    if not station or station.startswith("host-"):
        return "Error: Subway can't identify this chat, so it can't give it a branch."
    data = _load()
    mine = data["branches"].get(station)
    if mine:
        return status_text(mine)
    repo = _toplevel(os.path.abspath(repo_hint or os.getcwd()))
    if not repo:
        return f"Error: {repo_hint or os.getcwd()} is not inside a git repository."
    short = re.sub(r"[^A-Za-z0-9]", "", station)[:8] or "chat"
    branch = f"subway/chat-{short}-{int(time.time())}"
    wt = os.path.join(_s.SUBWAY_DIR, "worktrees", f"chat-{short}")
    if os.path.exists(wt):
        return f"Error: {wt} already exists; drop the old chat branch first."
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "worktree", "add", "-q", "-b", branch, wt, base)
    mine = {"repo": repo, "branch": branch, "worktree": wt, "base": base, "created": int(time.time())}
    data["branches"][station] = mine
    _save(data)
    return status_text(mine) + ("\nUncommitted edits in the original tree were not copied; commit or redo them in the worktree."
                                if _git(repo, "status", "--porcelain", "--untracked-files=no", check=False) else "")


def status_text(mine: Dict[str, Any]) -> str:
    return (f"Chat branch {mine['branch']} is open at {mine['worktree']}. Edit files there (same relative paths as "
            f"{mine['repo']}), commit in that worktree, then call land_chat().")


def land(station: str) -> str:
    data = _load()
    mine = data["branches"].get(station)
    if not mine:
        return "Error: this chat has no open chat branch. Start one with chat_branch(action='start')."
    wt, repo, branch, base = mine["worktree"], mine["repo"], mine["branch"], mine["base"]
    if _git(wt, "status", "--porcelain", check=False):
        return (f"Commit your changes in {wt} first: git -C {wt} add <files> && git -C {wt} commit -m \"...\". "
                "Then call land_chat() again.")
    if not _git(repo, "rev-list", f"{base}..{branch}", check=False):
        _close(data, station, mine)
        return f"Nothing to land on {branch}; the chat branch is closed."
    from workers.spawn import _merge_worker_branch
    ok, msg = _merge_worker_branch(repo, branch, base, "chat branch", station)
    if not ok:
        return msg + " The chat branch stays open: fix it there and call land_chat() again, or resolve it from the Landing view."
    _close(data, station, mine)
    return msg + " Chat branch closed."


def drop(station: str) -> str:
    data = _load()
    mine = data["branches"].get(station)
    if not mine:
        return "This chat has no open chat branch."
    if _git(mine["worktree"], "status", "--porcelain", check=False):
        return f"Error: {mine['worktree']} has uncommitted changes. Commit or remove them, then drop again."
    _git(mine["repo"], "worktree", "remove", mine["worktree"], check=False)
    data["branches"].pop(station, None)
    _save(data)
    return f"Chat branch closed. The branch {mine['branch']} is kept; delete it with git when you no longer need it."


def _close(data: Dict[str, Any], station: str, mine: Dict[str, Any]) -> None:
    _git(mine["repo"], "worktree", "remove", "--force", mine["worktree"], check=False)
    _git(mine["repo"], "branch", "-D", mine["branch"], check=False)
    data["branches"].pop(station, None)
    _save(data)


def guard_edit(station: str, path: str) -> str:
    """"" to allow, else the reason to refuse an edit of `path` by `station`."""
    real = os.path.realpath(path)
    data = _load()
    mine = data["branches"].get(station)
    if mine:
        wt, repo = os.path.realpath(mine["worktree"]), os.path.realpath(mine["repo"])
        if _inside(real, wt) or not _inside(real, repo):
            return ""
        target = os.path.join(mine["worktree"], os.path.relpath(real, repo))
        return f"This chat is on chat branch {mine['branch']}: edit {target} instead, then call land_chat()."
    if _inside(real, _worktrees_root()):
        return ""
    repo = _toplevel(real)
    if not repo:
        return ""
    now = time.time()
    editors = {s: t for s, t in (data["editors"].get(repo) or {}).items() if now - float(t) < EDITOR_WINDOW_S}
    if auto_enabled():
        others = [s for s in editors if s != station and s not in data["branches"]]
        if others:
            return (f"Another chat ({others[0][:8]}) is editing this tree. Call chat_branch(action='start') and edit in "
                    "its worktree, then land_chat(). (Settings > Git > Require a chat branch.)")
    editors[station] = now
    data["editors"][repo] = editors
    _save(data)
    return ""


def t_chat_branch(action: str = "start", project_dir: str = "") -> str:
    """Give this chat its own branch and worktree (start), show it (status), or close it (drop)."""
    station = str(_s._self_id() or "")
    act = str(action or "start").strip().lower()
    try:
        if act == "start":
            return start(station, project_dir)
        if act == "status":
            mine = _load()["branches"].get(station)
            return status_text(mine) if mine else "This chat has no open chat branch."
        if act == "drop":
            return drop(station)
    except Exception as e:
        return f"Error: {e}"
    return "Error: action must be start, status or drop."


def t_land_chat() -> str:
    """Land this chat's branch into its repository (3-way merge; overlaps go to the Landing view)."""
    try:
        return land(str(_s._self_id() or ""))
    except Exception as e:
        return f"Error: {e}"
