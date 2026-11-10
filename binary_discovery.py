"""Dynamic binary discovery for Subway provider CLIs.

Extracted from server.py. Functions read HOME from the live ``server`` module
at call time so tests/patches that affect server.HOME remain effective.
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
from typing import Optional


def find_claude_binary() -> Optional[str]:
    """Dynamically locate Claude Code binary across system PATH, user bins, or IDE extension bundles."""
    import server
    HOME = server.HOME
    env_bin = os.environ.get("CLAUDE_BIN")
    if env_bin and (os.path.exists(env_bin) or os.path.lexists(env_bin)):
        return env_bin
    p = shutil.which("claude") or (shutil.which("claude.cmd") if sys.platform == "win32" else None)
    if p:
        return p
    candidates = [
        os.path.join(HOME, ".local", "bin", "claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
        *glob.glob(os.path.join(HOME, ".nvm", "versions", "node", "*", "bin", "claude")),
        *glob.glob(os.path.join(HOME, ".npm-global", "bin", "claude")),
        *sorted(glob.glob(os.path.join(HOME, ".vscode", "extensions", "anthropic.claude-code-*", "resources", "native-binary", "claude")), key=os.path.getmtime, reverse=True),
        *sorted(glob.glob(os.path.join(HOME, ".antigravity-ide", "extensions", "anthropic.claude-code-*", "resources", "native-binary", "claude")), key=os.path.getmtime, reverse=True),
        os.path.join(HOME, "Library", "Application Support", "Claude", "claude"),
    ]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if appdata:
            candidates.append(os.path.join(appdata, "npm", "claude.cmd"))
            candidates.append(os.path.join(appdata, "npm", "claude"))
        if localappdata:
            candidates.extend(glob.glob(os.path.join(localappdata, "Programs", "**", "claude.cmd"), recursive=True))
            candidates.extend(glob.glob(os.path.join(localappdata, "Programs", "**", "claude.exe"), recursive=True))
    for c in candidates:
        if os.path.exists(c) or os.path.lexists(c):
            return c
    return None


def find_opencode_binary() -> Optional[str]:
    """Dynamically locate OpenCode / OpenCode2 binary across PATH, nvm, or local bins."""
    import server
    HOME = server.HOME
    env_bin = os.environ.get("OPENCODE_BIN")
    if env_bin and (os.path.exists(env_bin) or os.path.lexists(env_bin)):
        return env_bin
    for name in ("opencode", "opencode2"):
        p = shutil.which(name) or (shutil.which(f"{name}.cmd") if sys.platform == "win32" else None)
        if p:
            return p
    candidates = [
        *glob.glob(os.path.join(HOME, ".nvm", "versions", "node", "*", "bin", "opencode*")),
        *glob.glob(os.path.join(HOME, ".local", "bin", "opencode*")),
        "/usr/local/bin/opencode",
        "/usr/local/bin/opencode2",
        "/opt/homebrew/bin/opencode",
        "/opt/homebrew/bin/opencode2",
    ]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if appdata:
            for name in ("opencode", "opencode2"):
                candidates.append(os.path.join(appdata, "npm", f"{name}.cmd"))
                candidates.append(os.path.join(appdata, "npm", name))
        if localappdata:
            candidates.extend(glob.glob(os.path.join(localappdata, "Programs", "**", "opencode*.cmd"), recursive=True))
            candidates.extend(glob.glob(os.path.join(localappdata, "Programs", "**", "opencode*.exe"), recursive=True))
    for c in candidates:
        if os.path.exists(c) or os.path.lexists(c):
            return c
    return None


def find_agy_binary() -> Optional[str]:
    """Locate the Antigravity CLI, which runs headless workers with `agy --print`."""
    import server
    HOME = server.HOME
    env_bin = os.environ.get("AGY_BIN")
    if env_bin and os.path.exists(env_bin):
        return env_bin
    p = shutil.which("agy") or (shutil.which("agy.cmd") if sys.platform == "win32" else None)
    if p:
        return p
    candidates = [os.path.join(HOME, ".gemini", "bin", "agy")]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if appdata:
            candidates.append(os.path.join(appdata, "npm", "agy.cmd"))
            candidates.append(os.path.join(appdata, "npm", "agy"))
        if localappdata:
            candidates.extend(glob.glob(os.path.join(localappdata, "Programs", "**", "agy*.cmd"), recursive=True))
            candidates.extend(glob.glob(os.path.join(localappdata, "Programs", "**", "agy*.exe"), recursive=True))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def find_copilot_binary() -> Optional[str]:
    """Dynamically locate GitHub Copilot CLI binary."""
    import server
    HOME = server.HOME
    env_bin = os.environ.get("COPILOT_BIN")
    if env_bin and (os.path.exists(env_bin) or os.path.lexists(env_bin)):
        return env_bin
    p = shutil.which("copilot") or (shutil.which("copilot.cmd") if sys.platform == "win32" else None)
    if p:
        return p
    candidates = [
        os.path.join(HOME, ".local", "bin", "copilot"),
        "/usr/local/bin/copilot",
        "/opt/homebrew/bin/copilot",
    ]
    for c in candidates:
        if os.path.exists(c) or os.path.lexists(c):
            return c
    return None


def _pi_wrapper() -> Optional[str]:
    """The repo's headless launcher, which pins PI_CODING_AGENT_DIR so the deepai/* models resolve."""
    w = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "pi-deep")
    return w if os.path.isfile(w) and os.access(w, os.X_OK) else None


def _is_app_bundle(path: str) -> bool:
    """The DeepAI Code GUI rejects -p/--mode, so a path inside a macOS .app is never the CLI."""
    return ".app/Contents/" in path or path.rstrip("/").endswith(".app")


def find_pi_binary() -> Optional[str]:
    """Locate a headless pi CLI: PI_BIN, then the repo wrapper, then PATH, then known install dirs."""
    import server
    HOME = server.HOME
    env_bin = os.environ.get("PI_BIN")
    if env_bin and not _is_app_bundle(env_bin) and (os.path.exists(env_bin) or os.path.lexists(env_bin)):
        return env_bin
    wrapper = _pi_wrapper()
    if wrapper:
        return wrapper
    for name in ("pi", "deepai") + (("pi.cmd",) if sys.platform == "win32" else ()):
        p = shutil.which(name)
        if p and not _is_app_bundle(p):
            return p
    candidates = [
        os.path.join(HOME, ".local", "bin", "pi"),
        "/usr/local/bin/pi",
        "/opt/homebrew/bin/pi",
    ]
    for c in candidates:
        if os.path.exists(c) or os.path.lexists(c):
            return c
    return None


def find_cursor_binary() -> Optional[str]:
    """Locate Cursor CLI binary."""
    import server
    HOME = server.HOME
    env_bin = os.environ.get("CURSOR_BIN")
    if env_bin and (os.path.exists(env_bin) or os.path.lexists(env_bin)):
        return env_bin
    p = shutil.which("cursor") or (shutil.which("cursor.cmd") if sys.platform == "win32" else None)
    if p:
        return p
    candidates = [
        "/Applications/Cursor.app/Contents/Resources/app/bin/cursor",
        os.path.join(HOME, "Applications", "Cursor.app", "Contents", "Resources", "app", "bin", "cursor"),
        "/usr/local/bin/cursor",
        os.path.join(HOME, ".local", "bin", "cursor"),
    ]
    if sys.platform == "win32":
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            candidates.extend([
                os.path.join(localappdata, "Programs", "cursor", "Cursor.exe"),
                os.path.join(localappdata, "cursor", "bin", "cursor.cmd"),
            ])
    for c in candidates:
        if os.path.exists(c) or os.path.lexists(c):
            return c
    return None


def find_devin_binary() -> Optional[str]:
    """Locate Devin CLI / desktop launcher binary."""
    import server
    HOME = server.HOME
    env_bin = os.environ.get("DEVIN_BIN")
    if env_bin and (os.path.exists(env_bin) or os.path.lexists(env_bin)):
        return env_bin
    for name in ("devin-desktop", "devin"):
        p = shutil.which(name) or (shutil.which(f"{name}.cmd") if sys.platform == "win32" else None)
        if p:
            return p
    candidates = [
        "/Applications/Devin.app/Contents/Resources/app/bin/devin-desktop",
        os.path.join(HOME, "Applications", "Devin.app", "Contents", "Resources", "app", "bin", "devin-desktop"),
        "/Applications/Devin.app/Contents/MacOS/Devin",
        "/usr/local/bin/devin-desktop",
        os.path.join(HOME, ".local", "bin", "devin-desktop"),
    ]
    if sys.platform == "win32":
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            candidates.append(os.path.join(localappdata, "Programs", "devin", "Devin.exe"))
    for c in candidates:
        if os.path.exists(c) or os.path.lexists(c):
            return c
    return None
