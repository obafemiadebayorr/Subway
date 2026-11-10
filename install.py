#!/usr/bin/env python3
"""Subway Context — Universal Interactive Installer & Connector.

Connects Subway Context across coding agent environments with full user control:
- Auto-detects installed coding agents.
- Shows disclaimer and detected paths before making any changes.
- Supports installing on ALL detected agents or SELECTING specific ones (e.g. just 2 of 3).
- Supports granular removal/disconnect per agent.

Usage:
    python3 install.py                         # Interactive install (select all or specific)
    python3 install.py --targets claude,gemini # Install only to Claude Code and Antigravity
    python3 install.py --skip opencode         # Install to all except OpenCode
    python3 install.py --yes                   # Non-interactive: connect all detected
    python3 install.py --status                # Show connection status per agent
    python3 install.py --remove opencode       # Disconnect Subway from only OpenCode
    python3 install.py --remove all            # Disconnect Subway from all agents
"""

import glob
import json
import os
import select
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    dirname = os.path.dirname(os.path.abspath(path))
    if dirname:
        os.makedirs(dirname, exist_ok=True)

    if os.path.exists(path):
        bak_path = path + ".subway-bak"
        shutil.copy2(path, bak_path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
    else:
        mode = 0o600

    fd, tmp_path = tempfile.mkstemp(dir=dirname, prefix=".subway_tmp_")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def _load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"Root of JSON in {path} must be an object, got {type(data).__name__}")
        return data
    except Exception as err:
        if isinstance(err, ValueError):
            raise
        raise ValueError(f"Could not read or parse {path}: {err}") from err


def _safe_write_marked_file(path: str, content: str, marker: str = "<!-- subway-managed -->") -> bool:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = f.read()
            if marker not in existing:
                print(f"  Note: Skipping {path} (missing '{marker}' marker)")
                return False
        except Exception as e:
            print(f"  Note: Skipping {path} (could not read: {e})")
            return False

    content = content.rstrip() + "\n" + marker + "\n"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except Exception as e:
        print(f"  Note: Failed to write {path}: {e}")
        return False


def get_default_paths() -> Tuple[str, str]:
    server_dir = os.path.dirname(os.path.abspath(__file__))
    server_py = os.path.join(server_dir, "server.py")
    py_bin = sys.executable or shutil.which("python3") or "/usr/bin/python3"
    return server_py, py_bin


def server_command(server_py: str, py_bin: str) -> List[str]:
    if "/_npx/" in server_py.replace("\\", "/"):
        npx = "npx.cmd" if sys.platform == "win32" else "npx"
        return [npx, "-y", "subway-context"]
    return [py_bin, server_py]


def detect_environments() -> Dict[str, Dict[str, Any]]:
    """Probes system to see which supported coding agent environments exist."""
    c_config = os.path.expanduser("~/.claude.json")
    c_bin = shutil.which("claude")
    c_projects = os.path.expanduser("~/.claude/projects")
    claude_detected = False
    claude_evidence = ""
    if c_bin:
        claude_detected = True
        claude_evidence = f"claude binary on PATH ({c_bin})"
    elif os.path.exists(c_projects):
        claude_detected = True
        claude_evidence = f"projects folder exists ({c_projects})"
    else:
        claude_evidence = "neither claude binary on PATH nor ~/.claude/projects found"

    a_config = os.path.expanduser("~/.gemini/config/mcp_config.json")
    agy_bin = os.path.expanduser("~/.gemini/bin/agy")
    agy_which = shutil.which("agy")
    agy_app = "/Applications/Antigravity IDE.app"
    agy_brain = os.path.expanduser("~/.gemini/antigravity/brain")
    agy_detected = False
    agy_evidence = ""
    if os.path.exists(agy_bin):
        agy_detected = True
        agy_evidence = f"binary exists ({agy_bin})"
    elif agy_which:
        agy_detected = True
        agy_evidence = f"agy on PATH ({agy_which})"
    elif os.path.exists(agy_app):
        agy_detected = True
        agy_evidence = f"application exists ({agy_app})"
    elif os.path.exists(agy_brain):
        agy_detected = True
        agy_evidence = f"brain directory exists ({agy_brain})"
    else:
        agy_evidence = "none of ~/.gemini/bin/agy, agy on PATH, /Applications/Antigravity IDE.app, ~/.gemini/antigravity/brain found"

    o_config = os.path.expanduser("~/.config/opencode/opencode.json")
    o_bin2 = shutil.which("opencode2")
    o_bin = shutil.which("opencode")
    o_db = os.path.expanduser("~/.local/share/opencode/opencode.db")
    opencode_detected = False
    opencode_evidence = ""
    if o_bin2:
        opencode_detected = True
        opencode_evidence = f"opencode2 on PATH ({o_bin2})"
    elif o_bin:
        opencode_detected = True
        opencode_evidence = f"opencode on PATH ({o_bin})"
    elif os.path.exists(o_db):
        opencode_detected = True
        opencode_evidence = f"database exists ({o_db})"
    else:
        opencode_evidence = "neither opencode2/opencode on PATH nor ~/.local/share/opencode/opencode.db found"

    pi_config = os.path.expanduser("~/.pi/agent/mcp.json")
    pi_which = shutil.which("pi") or shutil.which("deepai")
    pi_app = "/Applications/DeepAI Code.app"
    pi_dir = os.path.expanduser("~/.pi")
    pi_agent_dir = os.path.expanduser("~/.pi/agent")
    pi_detected = False
    pi_evidence = ""
    if pi_which:
        pi_detected = True
        pi_evidence = f"binary on PATH ({pi_which})"
    elif os.path.exists(pi_app):
        pi_detected = True
        pi_evidence = f"application exists ({pi_app})"
    elif os.path.exists(pi_dir) or os.path.exists(pi_agent_dir):
        pi_detected = True
        pi_evidence = f"directory exists ({pi_dir if os.path.exists(pi_dir) else pi_agent_dir})"
    else:
        pi_evidence = "neither pi/deepai binary on PATH, /Applications/DeepAI Code.app, nor ~/.pi directory found"

    cursor_config = os.path.expanduser("~/.cursor/mcp.json")
    cursor_app = "/Applications/Cursor.app"
    cursor_dir = os.path.expanduser("~/.cursor")
    cursor_detected = False
    cursor_evidence = ""
    if shutil.which("cursor"):
        cursor_detected = True
        cursor_evidence = f"cursor on PATH ({shutil.which('cursor')})"
    elif os.path.exists(cursor_app):
        cursor_detected = True
        cursor_evidence = f"application exists ({cursor_app})"
    elif os.path.exists(cursor_dir):
        cursor_detected = True
        cursor_evidence = f"~/.cursor directory exists ({cursor_dir})"
    else:
        cursor_evidence = "neither cursor CLI nor /Applications/Cursor.app nor ~/.cursor found"

    devin_config = os.path.expanduser("~/.codeium/windsurf/mcp_config.json")
    devin_app = "/Applications/Devin.app"
    devin_dir = os.path.expanduser("~/.devin")
    devin_user = os.path.expanduser("~/Library/Application Support/Devin")
    devin_detected = False
    devin_evidence = ""
    if os.path.exists(devin_app):
        devin_detected = True
        devin_evidence = f"application exists ({devin_app})"
    elif os.path.exists(devin_dir):
        devin_detected = True
        devin_evidence = f"~/.devin directory exists ({devin_dir})"
    elif os.path.exists(devin_user):
        devin_detected = True
        devin_evidence = f"Devin user data exists ({devin_user})"
    else:
        devin_evidence = "neither /Applications/Devin.app nor ~/.devin found"

    copilot_config = os.path.expanduser("~/.copilot/mcp-config.json")
    copilot_dir = os.path.expanduser("~/.copilot")
    copilot_which = shutil.which("copilot")
    copilot_detected = False
    copilot_evidence = ""
    if os.path.exists(copilot_dir):
        copilot_detected = True
        copilot_evidence = f"~/.copilot directory exists ({copilot_dir})"
    elif copilot_which:
        copilot_detected = True
        copilot_evidence = f"copilot on PATH ({copilot_which})"
    else:
        copilot_evidence = "neither ~/.copilot directory nor copilot on PATH found"

    envs = {
        "claude": {
            "name": "Claude Code",
            "detected": claude_detected,
            "evidence": claude_evidence,
            "config": c_config,
        },
        "antigravity": {
            "name": "Antigravity (Gemini)",
            "detected": agy_detected,
            "evidence": agy_evidence,
            "config": a_config,
        },
        "opencode": {
            "name": "OpenCode (Terminal)",
            "detected": opencode_detected,
            "evidence": opencode_evidence,
            "config": o_config,
        },
        "pi": {
            "name": "pi (DeepAI Code)",
            "detected": pi_detected,
            "evidence": pi_evidence,
            "config": pi_config,
        },
        "cursor": {
            "name": "Cursor",
            "detected": cursor_detected,
            "evidence": cursor_evidence,
            "config": cursor_config,
        },
        "devin": {
            "name": "Devin",
            "detected": devin_detected,
            "evidence": devin_evidence,
            "config": devin_config,
        },
        "copilot-cli": {
            "name": "Copilot CLI",
            "detected": copilot_detected,
            "evidence": copilot_evidence,
            "config": copilot_config,
        },
    }

    for v in envs.values():
        status = "Detected" if v["detected"] else "Not found"
        print(f"  {v['name']}: {status} — evidence: {v['evidence']}")

    return envs


def install_governor_adapters(repo_dir: Optional[str] = None) -> bool:
    """Install governor adapters for OpenCode (plugin) and Antigravity (hooks.json).

    Idempotent and non-destructive: merges into existing configuration.
    """
    if repo_dir is None:
        repo_dir = os.path.dirname(os.path.abspath(__file__))

    # 1. OpenCode plugin: copy or link into ~/.config/opencode/plugin/ when ~/.config/opencode exists
    opencode_dir = os.path.expanduser("~/.config/opencode")
    if os.path.exists(opencode_dir):
        plugin_dir = os.path.join(opencode_dir, "plugin")
        os.makedirs(plugin_dir, exist_ok=True)
        src_plugin = os.path.join(repo_dir, "adapters", "opencode-governor.js")
        dst_plugin = os.path.join(plugin_dir, "opencode-governor.js")
        if os.path.exists(src_plugin):
            try:
                shutil.copy2(src_plugin, dst_plugin)
            except Exception as e:
                print(f"  Warning: failed to copy opencode governor plugin: {e}")

    # 2. Antigravity hook: add PreToolUse entry (matcher ".*") running
    #    python3 <repo>/adapters/antigravity_governor.py to ~/.gemini/config/hooks.json
    #    when that file's directory exists.
    gemini_config_dir = os.path.expanduser("~/.gemini/config")
    if os.path.exists(gemini_config_dir):
        hooks_path = os.path.join(gemini_config_dir, "hooks.json")
        try:
            hooks_data = _load_json(hooks_path)
        except Exception:
            hooks_data = {}
        if not isinstance(hooks_data, dict):
            hooks_data = {}

        agy_adapter = os.path.join(repo_dir, "adapters", "antigravity_governor.py")
        hook_cmd = f"python3 {shlex.quote(agy_adapter)}"

        governor_entry = hooks_data.get("subway-governor")
        if not isinstance(governor_entry, dict):
            governor_entry = {}

        pre_tool_list = governor_entry.get("PreToolUse")
        if not isinstance(pre_tool_list, list):
            pre_tool_list = []

        already_present = False
        for item in pre_tool_list:
            if isinstance(item, dict) and item.get("matcher") == ".*":
                for h in item.get("hooks", []):
                    if isinstance(h, dict) and "antigravity_governor.py" in h.get("command", ""):
                        h["command"] = hook_cmd
                        already_present = True
                        break

        if not already_present:
            pre_tool_list.append({
                "matcher": ".*",
                "hooks": [{"type": "command", "command": hook_cmd}],
            })

        governor_entry["PreToolUse"] = pre_tool_list
        hooks_data["subway-governor"] = governor_entry
        _atomic_write_json(hooks_path, hooks_data)

    return True


def install_claude(server_py: str, py_bin: str) -> Tuple[bool, str]:
    config_path = os.path.expanduser("~/.claude.json")
    try:
        data = _load_json(config_path)
    except ValueError as err:
        reason = str(err)
        print(f"  Error reading {config_path}: {reason}")
        return False, reason

    settings_path = os.path.expanduser("~/.claude/settings.json")
    try:
        settings_data = _load_json(settings_path)
    except ValueError as err:
        reason = str(err)
        print(f"  Error reading {settings_path}: {reason}")
        return False, reason

    base_cmd = server_command(server_py, py_bin)
    servers = data.setdefault("mcpServers", {})
    servers["subway-context"] = {
        "command": base_cmd[0],
        "args": base_cmd[1:]
    }
    _atomic_write_json(config_path, data)

    # Hook in ~/.claude/settings.json
    hooks = settings_data.setdefault("hooks", {})
    if isinstance(hooks, dict):
        if "/_npx/" in server_py.replace("\\", "/"):
            npx = "npx.cmd" if sys.platform == "win32" else "npx"
            hook_cmd = f"{shlex.quote(npx)} -y subway-context hook-guard"
            hook_prompt_cmd = f"{shlex.quote(npx)} -y subway-context hook-prompt"
            hook_precompact_cmd = f"{shlex.quote(npx)} -y subway-context hook-precompact"
        else:
            hook_cmd = f"{shlex.quote(py_bin)} {shlex.quote(server_py)} hook-guard"
            hook_prompt_cmd = f"{shlex.quote(py_bin)} {shlex.quote(server_py)} hook-prompt"
            hook_precompact_cmd = f"{shlex.quote(py_bin)} {shlex.quote(server_py)} hook-precompact"

        # Remove existing subway hooks and drop entries left empty
        for event, entries in list(hooks.items()):
            if not isinstance(entries, list):
                continue
            cleaned = []
            for entry in entries:
                if isinstance(entry, dict) and "hooks" in entry and isinstance(entry["hooks"], list):
                    remaining_hooks = [
                        h for h in entry["hooks"]
                        if isinstance(h, dict) and not (
                            "hook-guard" in h.get("command", "") or "hook-prompt" in h.get("command", "") or "hook-precompact" in h.get("command", "")
                        )
                    ]
                    if remaining_hooks:
                        entry_copy = dict(entry)
                        entry_copy["hooks"] = remaining_hooks
                        cleaned.append(entry_copy)
                elif isinstance(entry, dict):
                    cmd = entry.get("command", "")
                    if not ("hook-guard" in cmd or "hook-prompt" in cmd or "hook-precompact" in cmd):
                        cleaned.append(entry)
                else:
                    cleaned.append(entry)
            hooks[event] = cleaned

        pre_tool = hooks.setdefault("PreToolUse", [])
        if isinstance(pre_tool, list):
            pre_tool.append({
                "matcher": "*",
                "hooks": [{"type": "command", "command": hook_cmd}],
            })

        user_prompt = hooks.setdefault("UserPromptSubmit", [])
        if isinstance(user_prompt, list):
            user_prompt.append({
                "hooks": [{"type": "command", "command": hook_prompt_cmd}],
            })

        pre_compact = hooks.setdefault("PreCompact", [])
        if isinstance(pre_compact, list):
            pre_compact.append({
                "hooks": [{"type": "command", "command": hook_precompact_cmd}],
            })

        _atomic_write_json(settings_path, settings_data)
    install_governor_adapters()

    # Keyboard / slash command listeners (~/.claude/commands/)
    cmds_dir = os.path.expanduser("~/.claude/commands")
    _safe_write_marked_file(
        os.path.join(cmds_dir, "opencode.md"),
        "Read the latest decisions and files modified by OpenCode terminal agent via Subway Context:\n"
        "Call MCP tool `read_chat(chat_id=\"@opencode\", mode=\"decisions\", since=\"last\")` and summarize the results.\n"
    )
    _safe_write_marked_file(
        os.path.join(cmds_dir, "gemini.md"),
        "Read the latest decisions and files modified by the Antigravity/Gemini session via Subway Context:\n"
        "Call MCP tool `read_chat(chat_id=\"@agy-latest\", mode=\"decisions\", since=\"last\")` and summarize the results.\n"
    )
    _safe_write_marked_file(
        os.path.join(cmds_dir, "subway.md"),
        "List all active agent stations and highlights across Claude Code, Antigravity, and OpenCode:\n"
        "Call MCP tool `list_chats()` and present the active lines.\n"
    )

    return True, f"Connected in {config_path} & commands synced"


def install_antigravity(server_py: str, py_bin: str) -> Tuple[bool, str]:
    config_path = os.path.expanduser("~/.gemini/config/mcp_config.json")
    try:
        data = _load_json(config_path)
    except ValueError as err:
        reason = str(err)
        print(f"  Error reading {config_path}: {reason}")
        return False, reason

    base_cmd = server_command(server_py, py_bin)
    servers = data.setdefault("mcpServers", {})
    servers["subway-context"] = {
        "command": base_cmd[0],
        "args": base_cmd[1:]
    }
    _atomic_write_json(config_path, data)

    # Antigravity visual @ skills (~/.gemini/config/skills/)
    skills_base = os.path.expanduser("~/.gemini/config/skills")
    for s_name, s_desc, s_call in (
        ("opencode", "Subway Context connector to OpenCode terminal agent.", 'read_chat(chat_id="@opencode", mode="decisions", since="last")'),
        ("claude", "Subway Context connector to Claude Code.", 'read_chat(chat_id="@claude-latest", mode="decisions", since="last")'),
        ("subway", "Subway Context multi-window agent transit system.", "list_chats()"),
    ):
        s_file = os.path.join(skills_base, s_name, "SKILL.md")
        s_content = (
            f"---\nname: {s_name}\ndescription: {s_desc}\n---\n\n"
            f"# {s_name.capitalize()} Transit Connector\n\n"
            f"When mentioned or invoked with `@{s_name}`, call Subway Context MCP tool `{s_call}`.\n"
        )
        _safe_write_marked_file(s_file, s_content)

    # Lifecycle hook: guard & read-budget gate
    npx = shutil.which("npx")
    if npx and os.path.exists(os.path.join(os.path.dirname(server_py), "bin", "subway-context.js")):
        hook_cmd = f"{shlex.quote(npx)} -y subway-context hook-guard"
    else:
        hook_cmd = f"{shlex.quote(py_bin)} {shlex.quote(server_py)} hook-guard"

    hooks_path = os.path.expanduser("~/.gemini/config/hooks.json")
    try:
        hooks_data = _load_json(hooks_path)
    except Exception:
        hooks_data = {}
    if not isinstance(hooks_data, dict):
        hooks_data = {}
    hooks_data["subway-guard"] = {
        "PreToolUse": [
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": hook_cmd}],
            }
        ]
    }
    _atomic_write_json(hooks_path, hooks_data)

    return True, f"Connected in {config_path}, @ skills synced & guard hook registered"


def install_opencode(server_py: str, py_bin: str) -> Tuple[bool, str]:
    jsonc_path = os.path.expanduser("~/.config/opencode/opencode.jsonc")
    if os.path.exists(jsonc_path):
        print(f"  Note: {jsonc_path} exists; leaving it unmodified.")

    config_path = os.path.expanduser("~/.config/opencode/opencode.json")
    try:
        data = _load_json(config_path)
    except ValueError as err:
        reason = str(err)
        print(f"  Error reading {config_path}: {reason}")
        return False, reason

    base_cmd = server_command(server_py, py_bin)
    mcp_obj = data.setdefault("mcp", {})
    servers = mcp_obj.setdefault("servers", {})
    servers["subway-context"] = {
        "type": "local",
        "command": base_cmd
    }
    _atomic_write_json(config_path, data)

    # Sync initial agent stubs
    try:
        sys.path.insert(0, os.path.dirname(server_py))
        from server import _sync_opencode_agent_stubs
        _sync_opencode_agent_stubs()
    except Exception:
        pass

    return True, f"Connected in {config_path} & stubs synced"


def install_copilot_cli(server_py: str, py_bin: str) -> Tuple[bool, str]:
    config_path = os.path.expanduser("~/.copilot/mcp-config.json")
    try:
        data = _load_json(config_path)
    except ValueError as err:
        reason = str(err)
        print(f"  Error reading {config_path}: {reason}")
        return False, reason

    cmd = server_command(server_py, py_bin)
    mcp_servers = data.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
        data["mcpServers"] = mcp_servers
    mcp_servers["subway-context"] = {
        "type": "local",
        "command": cmd[0],
        "args": cmd[1:],
        "tools": ["*"],
    }
    _atomic_write_json(config_path, data)
    return True, config_path


def _sync_deepai_models(agent_dirs: List[str]) -> str:
    """Configures direct upstream routing for DeepAI Code models in models.json.

    DeepAI Code's local Electron proxy (http://127.0.0.1:<port>) can fail on Wi-Fi
    due to Node.js 18+ dual-stack timeouts. Syncing the authenticated API key from
    ~/.deepai-code/userdata/secrets/deepai-account.bin directly into models.json
    allows the headless pi CLI to route directly to https://api.deepai.org/deepai_code.
    """
    home = os.path.expanduser("~")
    secret_candidates = glob.glob(os.path.join(home, ".deepai-code", "userdata", "*", "deepai-account.bin")) + \
                        glob.glob(os.path.join(home, ".deepai-code", "userdata", "secrets", "deepai-account.bin"))
    api_key = ""
    for sc in secret_candidates:
        if os.path.isfile(sc):
            try:
                with open(sc, "r", encoding="utf-8") as f:
                    sec_data = json.load(f)
                    if isinstance(sec_data, dict) and sec_data.get("apiKey"):
                        api_key = str(sec_data["apiKey"]).strip()
                        break
            except Exception:
                continue

    if not api_key:
        return ""

    subway_pi = os.path.join(home, ".subway-context", "pi")
    os.makedirs(subway_pi, exist_ok=True)
    all_dirs = [subway_pi] + list(agent_dirs)

    # Seed subway_pi models.json from agent dirs if missing
    subway_models = os.path.join(subway_pi, "models.json")
    if not os.path.isfile(subway_models):
        for d in agent_dirs:
            src = os.path.join(d, "models.json")
            if os.path.isfile(src):
                try:
                    shutil.copy2(src, subway_models)
                    break
                except Exception:
                    pass

    updated = []
    for d in all_dirs:
        mpath = os.path.join(d, "models.json")
        if not os.path.isfile(mpath):
            continue
        try:
            with open(mpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            provs = data.get("providers") or {}
            deepai_prov = provs.get("deepai")
            if isinstance(deepai_prov, dict):
                headers = {"api-key": api_key, "X-DeepAI-Code-Theos": "4"}
                deepai_prov["baseUrl"] = "https://api.deepai.org/deepai_code"
                deepai_prov["headers"] = headers
                for m in deepai_prov.get("models") or []:
                    if isinstance(m, dict):
                        m["headers"] = headers
                _atomic_write_json(mpath, data)
                updated.append(mpath)
        except Exception:
            continue

    return f"; synced direct DeepAI credentials to {', '.join(updated)}" if updated else ""


def _pi_budget_guard_config_path() -> str:
    subway_dir = os.environ.get("SUBWAY_DIR") or os.path.join(os.path.expanduser("~"), ".subway-context")
    return os.path.join(subway_dir, "pi_budget_guard.json")


def install_pi(server_py: str, py_bin: str) -> Tuple[bool, str]:
    config_path = os.path.expanduser("~/.pi/agent/mcp.json")
    try:
        data = _load_json(config_path)
    except ValueError as err:
        reason = str(err)
        print(f"  Error reading {config_path}: {reason}")
        return False, reason

    base_cmd = server_command(server_py, py_bin)
    servers = data.setdefault("mcpServers", {})
    servers["subway-context"] = {
        "command": base_cmd[0],
        "args": base_cmd[1:]
    }
    _atomic_write_json(config_path, data)

    # Register subway-wake extension wherever the running pi discovers extensions.
    # extensions.md names ~/.pi/agent/extensions, but that is only the OSS default:
    # extension discovery follows the agent dir, and DeepAI Code relocates it via
    # PI_CODING_AGENT_DIR (~/.deepai-code/userdata/<app>/pi/<app>). Installing only
    # into the OSS path meant the extension was never loaded by the app at all — no
    # wake, no budget gate. Install into every plausible agent dir; a stray copy in the
    # OSS path is harmless, a missing copy in the real one breaks the feature silently.
    ext_src = os.path.join(os.path.dirname(os.path.abspath(server_py)), "extensions", "subway-wake.js")
    ext_dst_dirs = [os.path.expanduser("~/.pi/agent/extensions")]
    override = os.environ.get("PI_CODING_AGENT_DIR", "").strip()
    if override:
        ext_dst_dirs.insert(0, os.path.join(os.path.expanduser(override), "extensions"))
    try:
        from local_models import pi_agent_dir

        discovered = os.path.join(pi_agent_dir(), "extensions")
        if discovered not in ext_dst_dirs:
            ext_dst_dirs.insert(0, discovered)
    except Exception:
        pass
    ext_msg = ""
    written = []
    if os.path.exists(ext_src):
        for ext_dst_dir in ext_dst_dirs:
            try:
                os.makedirs(ext_dst_dir, exist_ok=True)
                ext_dst = os.path.join(ext_dst_dir, "subway-wake.js")
                shutil.copy2(ext_src, ext_dst)
                written.append(ext_dst)
            except OSError:
                continue
        ext_msg = (f"; wake extension registered at {', '.join(written)}" if written
                   else "; couldn't write the wake extension")
    else:
        ext_msg = f"; wake extension source not found at {ext_src}"

    # Budget guard config for the pi extension (read by subway-wake.js handlers).
    # Idempotent: overwrites with current server/py/subway paths.
    budget_msg = ""
    try:
        budget_cfg_path = _pi_budget_guard_config_path()
        budget_cfg = {
            "server_py": os.path.abspath(server_py),
            "py_bin": py_bin,
            "subway_dir": os.environ.get("SUBWAY_DIR") or os.path.join(os.path.expanduser("~"), ".subway-context"),
        }
        _atomic_write_json(budget_cfg_path, budget_cfg)
        budget_msg = f"; budget guard config written to {budget_cfg_path}"
    except Exception as e:
        budget_msg = f"; budget guard config failed: {e}"

    # Sync authenticated DeepAI credentials to bypass local proxy timeouts on Wi-Fi.
    deepai_msg = ""
    try:
        agent_dirs = [os.path.dirname(d) for d in ext_dst_dirs]
        deepai_msg = _sync_deepai_models(agent_dirs)
    except Exception as e:
        deepai_msg = f"; deepai sync failed: {e}"

    # Terminal capture hook (termishare parity): installs the shell rc hook that
    # reports command events to the HUD. Idempotent (terminals.install_hook strips
    # and re-adds its marked block). install_claude/install_opencode in install.py
    # do not currently call terminals.install_hook; install_pi adds it for
    # DeepAI/pi parity as specified.
    hook_msg = ""
    try:
        import terminals as _terminals

        hook_msg = f"; terminal hook: {_terminals.install_hook()}"
    except Exception as e:
        hook_msg = f"; terminal hook install failed: {e}"

    return True, f"Connected in {config_path}{ext_msg}{budget_msg}{deepai_msg}{hook_msg}"


def install_cursor(server_py: str, py_bin: str) -> Tuple[bool, str]:
    config_path = os.path.expanduser("~/.cursor/mcp.json")
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        data = _load_json(config_path)
    except ValueError as err:
        reason = str(err)
        print(f"  Error reading {config_path}: {reason}")
        return False, reason

    base_cmd = server_command(server_py, py_bin)
    servers = data.setdefault("mcpServers", {})
    servers["subway-context"] = {
        "command": base_cmd[0],
        "args": base_cmd[1:]
    }
    _atomic_write_json(config_path, data)

    # Write Subway skill in ~/.cursor/skills-cursor if directory exists
    skills_dir = os.path.expanduser("~/.cursor/skills-cursor")
    if os.path.exists(skills_dir):
        subway_skill = os.path.join(skills_dir, "subway", "SKILL.md")
        content = (
            "---\nname: subway\ndescription: Subway Context multi-window agent transit system.\n---\n\n"
            "# Subway Context Connector\n\n"
            "Use Subway Context to read decisions and changes from other coding agents:\n"
            "- `read_chat(chat_id=\"@claude-latest\", mode=\"decisions\")`\n"
            "- `read_chat(chat_id=\"@agy-latest\", mode=\"decisions\")`\n"
            "- `list_chats()` to list active sessions across agents.\n"
        )
        _safe_write_marked_file(subway_skill, content)

    return True, f"Connected in {config_path}"


def install_devin(server_py: str, py_bin: str) -> Tuple[bool, str]:
    config_path = os.path.expanduser("~/.codeium/windsurf/mcp_config.json")
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        data = _load_json(config_path)
    except ValueError as err:
        reason = str(err)
        print(f"  Error reading {config_path}: {reason}")
        return False, reason

    base_cmd = server_command(server_py, py_bin)
    servers = data.setdefault("mcpServers", {})
    servers["subway-context"] = {
        "command": base_cmd[0],
        "args": base_cmd[1:]
    }
    _atomic_write_json(config_path, data)
    return True, f"Connected in {config_path}"


def install_all() -> int:
    server_py, py_bin = get_default_paths()
    envs = detect_environments()
    detected_keys = [k for k, v in envs.items() if v["detected"]]
    installers = {
        "claude": install_claude,
        "antigravity": install_antigravity,
        "opencode": install_opencode,
        "pi": install_pi,
        "copilot-cli": install_copilot_cli,
    }
    all_ok = True
    for k in detected_keys:
        installer = installers.get(k)
        if installer:
            success, _ = installer(server_py, py_bin)
            if not success:
                all_ok = False
    return 0 if all_ok else 1


def remove_all() -> bool:
    return remove_target("all")


def remove_target(target: str) -> bool:
    target = target.lower()
    removed = False

    if target in ("claude", "all"):
        c_path = os.path.expanduser("~/.claude.json")
        if os.path.exists(c_path):
            try:
                d = _load_json(c_path)
                if "mcpServers" in d and "subway-context" in d["mcpServers"]:
                    del d["mcpServers"]["subway-context"]
                    _atomic_write_json(c_path, d)
                    print("  ✓ Claude Code: Disconnected from ~/.claude.json")
                    removed = True
            except Exception as e:
                print(f"  Error updating {c_path}: {e}")

        s_path = os.path.expanduser("~/.claude/settings.json")
        if os.path.exists(s_path):
            try:
                s_data = _load_json(s_path)
                hooks = s_data.get("hooks", {})
                pre_tool = hooks.get("PreToolUse", [])
                if isinstance(pre_tool, list):
                    new_pre = []
                    hook_found = False
                    for entry in pre_tool:
                        if isinstance(entry, dict) and entry.get("matcher") in ("Edit|Write|MultiEdit|NotebookEdit", "*"):
                            sub_hooks = entry.get("hooks", [])
                            filtered = [h for h in sub_hooks if isinstance(h, dict) and "hook-guard" not in h.get("command", "")]
                            if len(filtered) != len(sub_hooks):
                                hook_found = True
                            if filtered:
                                entry["hooks"] = filtered
                                new_pre.append(entry)
                            else:
                                hook_found = True
                        else:
                            new_pre.append(entry)
                    if hook_found:
                        hooks["PreToolUse"] = new_pre
                        _atomic_write_json(s_path, s_data)
                        print("  ✓ Claude Code: Removed Subway hook from ~/.claude/settings.json")
                        removed = True

                user_prompt = hooks.get("UserPromptSubmit", [])
                if isinstance(user_prompt, list):
                    new_up = []
                    up_found = False
                    for entry in user_prompt:
                        if isinstance(entry, dict):
                            sub_hooks = entry.get("hooks", [])
                            filtered = [h for h in sub_hooks if isinstance(h, dict) and "hook-prompt" not in h.get("command", "")]
                            if len(filtered) != len(sub_hooks):
                                up_found = True
                            if filtered:
                                entry["hooks"] = filtered
                                new_up.append(entry)
                            else:
                                up_found = True
                        else:
                            new_up.append(entry)
                    if up_found:
                        hooks["UserPromptSubmit"] = new_up
                        _atomic_write_json(s_path, s_data)
                        removed = True

                pre_compact = hooks.get("PreCompact", [])
                if isinstance(pre_compact, list):
                    new_pc = []
                    pc_found = False
                    for entry in pre_compact:
                        if isinstance(entry, dict):
                            sub_hooks = entry.get("hooks", [])
                            filtered = [h for h in sub_hooks if isinstance(h, dict) and "hook-precompact" not in h.get("command", "")]
                            if len(filtered) != len(sub_hooks):
                                pc_found = True
                            if filtered:
                                entry["hooks"] = filtered
                                new_pc.append(entry)
                            else:
                                pc_found = True
                        else:
                            new_pc.append(entry)
                    if pc_found:
                        hooks["PreCompact"] = new_pc
                        _atomic_write_json(s_path, s_data)
                        removed = True
            except Exception as e:
                print(f"  Error updating {s_path}: {e}")

        c_cmds = os.path.expanduser("~/.claude/commands")
        if os.path.isdir(c_cmds):
            for fname in os.listdir(c_cmds):
                fpath = os.path.join(c_cmds, fname)
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read()
                        if "<!-- subway-managed -->" in content:
                            os.remove(fpath)
                            print(f"  ✓ Claude Code: Removed managed file {fpath}")
                            removed = True
                    except Exception:
                        pass

    if target in ("antigravity", "gemini", "agy", "all"):
        a_path = os.path.expanduser("~/.gemini/config/mcp_config.json")
        if os.path.exists(a_path):
            try:
                d = _load_json(a_path)
                if "mcpServers" in d and "subway-context" in d["mcpServers"]:
                    del d["mcpServers"]["subway-context"]
                    _atomic_write_json(a_path, d)
                    print("  ✓ Antigravity: Disconnected from ~/.gemini/config/mcp_config.json")
                    removed = True
            except Exception as e:
                print(f"  Error updating {a_path}: {e}")

        hooks_base = os.path.expanduser("~/.gemini/config/hooks.json")
        if os.path.exists(hooks_base):
            try:
                hd = _load_json(hooks_base)
                if isinstance(hd, dict) and "subway-guard" in hd:
                    del hd["subway-guard"]
                    if hd:
                        _atomic_write_json(hooks_base, hd)
                    else:
                        os.remove(hooks_base)
                    print("  ✓ Antigravity: Removed Subway hook from ~/.gemini/config/hooks.json")
                    removed = True
            except Exception as e:
                print(f"  Error updating {hooks_base}: {e}")

        skills_base = os.path.expanduser("~/.gemini/config/skills")
        if os.path.isdir(skills_base):
            for root, _, files in os.walk(skills_base, topdown=False):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read()
                        if "<!-- subway-managed -->" in content:
                            os.remove(fpath)
                            print(f"  ✓ Antigravity: Removed managed file {fpath}")
                            removed = True
                    except Exception:
                        pass
                try:
                    if not os.listdir(root):
                        os.rmdir(root)
                except OSError:
                    pass

    if target in ("opencode", "opencode2", "all"):
        o_path = os.path.expanduser("~/.config/opencode/opencode.json")
        if os.path.exists(o_path):
            try:
                d = _load_json(o_path)
                if "mcp" in d and "servers" in d["mcp"] and "subway-context" in d["mcp"]["servers"]:
                    del d["mcp"]["servers"]["subway-context"]
                    _atomic_write_json(o_path, d)
                    print("  ✓ OpenCode: Disconnected from ~/.config/opencode/opencode.json")
                    removed = True
            except Exception as e:
                print(f"  Error updating {o_path}: {e}")

        agents_dir = os.path.expanduser("~/.config/opencode/agents")
        if os.path.isdir(agents_dir):
            for fname in os.listdir(agents_dir):
                fpath = os.path.join(agents_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read()
                        if "<!-- subway-auto-stub -->" in content:
                            os.remove(fpath)
                            print(f"  ✓ OpenCode: Removed stub {fpath}")
                            removed = True
                    except Exception:
                        pass

    if target in ("pi", "deepai", "all"):
        p_path = os.path.expanduser("~/.pi/agent/mcp.json")
        if os.path.exists(p_path):
            try:
                d = _load_json(p_path)
                if "mcpServers" in d and "subway-context" in d["mcpServers"]:
                    del d["mcpServers"]["subway-context"]
                    _atomic_write_json(p_path, d)
                    print(f"  ✓ pi (DeepAI Code): Disconnected from {p_path}")
                    removed = True
            except Exception as e:
                print(f"  Error updating {p_path}: {e}")

    if target in ("copilot-cli", "copilot_cli", "all"):
        cp_path = os.path.expanduser("~/.copilot/mcp-config.json")
        if os.path.exists(cp_path):
            try:
                d = _load_json(cp_path)
                if "mcpServers" in d and isinstance(d["mcpServers"], dict) and "subway-context" in d["mcpServers"]:
                    del d["mcpServers"]["subway-context"]
                    _atomic_write_json(cp_path, d)
                    print(f"  ✓ Copilot CLI: Disconnected from {cp_path}")
                    removed = True
            except Exception as e:
                print(f"  Error updating {cp_path}: {e}")

    return removed


def _check_command_exists(full_cmd: List[str]) -> bool:
    if not full_cmd:
        return False
    exe = full_cmd[0]
    if not (os.path.exists(exe) or shutil.which(exe)):
        return False
    if len(full_cmd) > 1 and full_cmd[1].endswith(".py"):
        if not os.path.exists(full_cmd[1]):
            return False
    return True


def _probe_mcp_server(full_cmd: List[str], timeout: float = 10.0) -> Tuple[bool, str]:
    if not full_cmd:
        return False, "No command configured"

    exe = full_cmd[0]
    if not (os.path.exists(exe) or shutil.which(exe)):
        return False, f"Command executable not found: {exe}"

    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "subway-status", "version": "1.0.0"}
        }
    }
    tools_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    }

    try:
        proc = subprocess.Popen(
            full_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except Exception as err:
        return False, f"Failed to spawn process: {err}"

    deadline = time.time() + timeout

    def send_line(msg_dict: Dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(msg_dict) + "\n")
        proc.stdin.flush()

    def read_response(expected_id: int) -> Optional[Dict[str, Any]]:
        while time.time() < deadline:
            remaining = max(0.05, deadline - time.time())
            if proc.stdout is None:
                return None
            rlist, _, _ = select.select([proc.stdout], [], [], remaining)
            if not rlist:
                if proc.poll() is not None:
                    return None
                continue
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    return None
                continue
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict) and parsed.get("id") == expected_id:
                    return parsed
            except Exception:
                continue
        return None

    try:
        send_line(init_req)
        init_resp = read_response(expected_id=1)
        if not init_resp:
            err_msg = ""
            if proc.stderr:
                try:
                    rlist, _, _ = select.select([proc.stderr], [], [], 0.1)
                    if rlist:
                        err_msg = proc.stderr.read()
                except Exception:
                    pass
            proc.kill()
            return False, f"Timeout or no initialize response{f': {err_msg.strip()[:200]}' if err_msg.strip() else ''}"

        send_line({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send_line(tools_req)

        tools_resp = read_response(expected_id=2)
        proc.terminate()
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()

        if not tools_resp:
            return False, "Timeout or no tools/list response"

        if "error" in tools_resp:
            return False, f"tools/list error: {tools_resp['error']}"

        result = tools_resp.get("result", {})
        tools = result.get("tools", [])
        return True, f"{len(tools)}"

    except Exception as err:
        try:
            proc.kill()
        except Exception:
            pass
        return False, f"Communication error: {err}"


def check_status() -> None:
    print("🚇 Subway Context Connection Status:\n")
    targets = [
        ("Claude Code", os.path.expanduser("~/.claude.json"), "claude"),
        ("Antigravity", os.path.expanduser("~/.gemini/config/mcp_config.json"), "antigravity"),
        ("OpenCode", os.path.expanduser("~/.config/opencode/opencode.json"), "opencode"),
        ("pi (DeepAI Code)", os.path.expanduser("~/.pi/agent/mcp.json"), "pi"),
    ]

    for name, config_path, key in targets:
        print(f"[{name}] ({config_path})")
        try:
            data = _load_json(config_path)
        except ValueError as err:
            print("  Registered: no")
            print("  Command exists: no")
            print(f"  Failure: Unreadable config ({err})\n")
            continue

        if key in ("claude", "antigravity", "pi"):
            servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
            entry = servers.get("subway-context")
        else:
            mcp = data.get("mcp", {}) if isinstance(data, dict) else {}
            servers = mcp.get("servers", {}) if isinstance(mcp, dict) else {}
            entry = servers.get("subway-context")

        if not entry:
            print("  Registered: no")
            print("  Command exists: no")
            print("  Failure: Not registered\n")
            continue

        print("  Registered: yes")
        cmd = entry.get("command")
        args = entry.get("args", [])
        if isinstance(cmd, list):
            full_cmd = list(cmd) + (args if isinstance(args, list) else [])
        elif isinstance(cmd, str):
            full_cmd = [cmd] + (args if isinstance(args, list) else [])
        else:
            full_cmd = []

        cmd_exists = _check_command_exists(full_cmd)
        print(f"  Command exists: {'yes' if cmd_exists else 'no'}")

        if not cmd_exists:
            cmd_str = " ".join(full_cmd) if full_cmd else "<none>"
            print(f"  Failure: Registered command does not exist ({cmd_str})\n")
            continue

        ok, result = _probe_mcp_server(full_cmd, timeout=10.0)
        if ok:
            print(f"  Tool count: {result}\n")
        else:
            print(f"  Failure: {result}\n")


def parse_cli_targets(args: List[str], detected_keys: List[str]) -> Optional[List[str]]:
    """Parse command line flags like --targets claude,gemini or --skip opencode."""
    for i, a in enumerate(args):
        if a in ("--targets", "--only") and i + 1 < len(args):
            raw = args[i + 1]
            return _normalize_selection(raw, detected_keys)
        if a.startswith("--targets=") or a.startswith("--only="):
            raw = a.split("=", 1)[1]
            return _normalize_selection(raw, detected_keys)

    for i, a in enumerate(args):
        if a == "--skip" and i + 1 < len(args):
            skip_targets = _normalize_selection(args[i + 1], detected_keys)
            return [k for k in detected_keys if k not in skip_targets]
        if a.startswith("--skip="):
            skip_targets = _normalize_selection(a.split("=", 1)[1], detected_keys)
            return [k for k in detected_keys if k not in skip_targets]

    individual = []
    if "--claude" in args:
        individual.append("claude")
    if "--gemini" in args or "--antigravity" in args or "--agy" in args:
        individual.append("antigravity")
    if "--opencode" in args or "--open" in args:
        individual.append("opencode")
    if "--pi" in args or "--deepai" in args:
        individual.append("pi")

    if individual:
        return list(dict.fromkeys(individual))
    return None


def _normalize_selection(raw: str, detected_keys: List[str]) -> List[str]:
    raw = raw.strip().strip("'\"[]() ").lower()
    if raw in ("a", "all", "both"):
        return detected_keys
    chosen = []
    num_map = {str(i + 1): k for i, k in enumerate(detected_keys)}
    for part in raw.replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if part in num_map:
            chosen.append(num_map[part])
        elif part in detected_keys:
            chosen.append(part)
        elif part in ("gemini", "agy") and "antigravity" in detected_keys:
            chosen.append("antigravity")
        elif part in ("open", "opencode2") and "opencode" in detected_keys:
            chosen.append("opencode")
        elif part in ("pi", "deepai") and "pi" in detected_keys:
            chosen.append("pi")
    return list(dict.fromkeys(chosen))


def ensure_notifier(interactive: bool, auto_yes: bool) -> str:
    """Offer or verify desktop alert integration so clicking an alert opens the HUD. Always says what it did."""
    if sys.platform.startswith("win"):
        print("  ✓ Desktop alerts: Windows native toasts need nothing extra; clicking an alert opens the HUD.")
        return "present"
    if sys.platform.startswith("linux"):
        if shutil.which("notify-send"):
            has_action = False
            try:
                proc = subprocess.run(["notify-send", "--help"], capture_output=True, text=True, timeout=2)
                has_action = "--action" in ((proc.stdout or "") + (proc.stderr or ""))
            except Exception:
                has_action = False
            if has_action:
                print("  ✓ Desktop alerts: notify-send found with action support; clicking an alert opens the HUD.")
                return "present"
            print("  ! Desktop alerts: notify-send found. Alerts work, but can't open the HUD on click.")
            return "no-action"

        why = ("Subway sends desktop alerts when a worker needs you. "
               "With action support, clicking one opens the HUD.")
        pkg_cmd = ""
        if shutil.which("apt") or shutil.which("apt-get"):
            pkg_cmd = "sudo apt install libnotify-bin"
        elif shutil.which("dnf"):
            pkg_cmd = "sudo dnf install libnotify"
        elif shutil.which("pacman"):
            pkg_cmd = "sudo pacman -S libnotify"

        if pkg_cmd:
            print(f"\n  Optional: {why}\n  To add desktop alerts, run: {pkg_cmd}")
        else:
            print(f"\n  Optional: {why}\n  To add desktop alerts, install libnotify (notify-send).")
        return "missing"
    if sys.platform != "darwin":
        return "skipped"
    if shutil.which("terminal-notifier"):
        print("  ✓ Desktop alerts: terminal-notifier found, so clicking an alert opens the HUD.")
        return "present"
    why = ("Subway sends desktop alerts when a worker needs you. With terminal-notifier, clicking one opens the HUD; "
           "without it alerts still show, but macOS opens Script Editor when you click them.")
    brew = shutil.which("brew")
    if not brew:
        print(f"\n  Optional: {why}\n  Install Homebrew, then run: brew install terminal-notifier")
        return "no-brew"
    if not interactive and not auto_yes:
        print(f"\n  Optional: {why}\n  To add it: brew install terminal-notifier")
        return "hint"
    if interactive and not auto_yes:
        print(f"\n  {why}")
        answer = input("  Install terminal-notifier with Homebrew now? [Y/n]: ").strip().lower()
        if answer not in ("", "y", "yes"):
            print("  Skipped. You can add it any time: brew install terminal-notifier")
            return "declined"
    print("  Installing terminal-notifier with Homebrew (brew install terminal-notifier)...")
    try:
        done = subprocess.run([brew, "install", "terminal-notifier"], timeout=600).returncode == 0
    except (OSError, subprocess.SubprocessError):
        done = False
    if done:
        print("  ✓ terminal-notifier installed: clicking a Subway alert now opens the HUD.")
        return "installed"
    print("  ✗ Couldn't install it. Alerts still work. To try again: brew install terminal-notifier")
    return "failed"


def ensure_editor_extension(interactive: bool, auto_yes: bool) -> List[str]:
    """Offer or install Subway HUD editor extension in detected editors."""
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    vsix_path = os.path.join(pkg_dir, "vscode", "subway-context.vsix")

    agy_ide = os.path.expanduser("~/.antigravity-ide/antigravity-ide/bin/agy-ide")
    devin_cli = shutil.which("devin") or (
        "/Applications/Devin.app/Contents/Resources/app/bin/code"
        if os.path.exists("/Applications/Devin.app/Contents/Resources/app/bin/code") else None
    )
    candidates = [
        ("code", shutil.which("code")),
        ("cursor", shutil.which("cursor") or (
            "/Applications/Cursor.app/Contents/Resources/app/bin/cursor"
            if os.path.exists("/Applications/Cursor.app/Contents/Resources/app/bin/cursor") else None
        )),
        ("devin", devin_cli),
        ("windsurf", shutil.which("windsurf")),
        ("agy-ide", agy_ide if os.path.exists(agy_ide) else shutil.which("agy-ide")),
    ]

    results = []
    why = "Subway HUD extension integrates the dashboard into your editor."

    for name, cli_path in candidates:
        if not cli_path:
            continue

        try:
            proc = subprocess.run([cli_path, "--list-extensions"], capture_output=True, text=True, timeout=5)
            has_ext = proc.returncode == 0 and "subway.subway-context" in (proc.stdout or "").lower()
        except Exception:
            has_ext = False

        if has_ext:
            print(f"  ✓ Editor extension: {name} already has subway.subway-context installed.")
            results.append("installed")
            continue

        if not os.path.exists(vsix_path):
            print(f"\n  ! Editor extension for {name}: {vsix_path} not found.")
            print("    To build it: npm run build:vsix")
            print("    (or: cd vscode && npx -y @vscode/vsce@3 package --allow-missing-repository --skip-license -o subway-context.vsix)")
            results.append("missing-vsix")
            continue

        if not interactive and not auto_yes:
            print(f"\n  Optional: {why}")
            print(f"  To install in {name}: {cli_path} --install-extension {vsix_path}")
            results.append("hint")
            continue

        if interactive and not auto_yes:
            print(f"\n  {why}")
            answer = input(f"  Install Subway extension in {name} now? [Y/n]: ").strip().lower()
            if answer not in ("", "y", "yes"):
                print(f"  Skipped. You can install it any time: {cli_path} --install-extension {vsix_path}")
                results.append("declined")
                continue

        print(f"  Installing Subway extension in {name} ({cli_path} --install-extension {vsix_path})...")
        try:
            done = subprocess.run([cli_path, "--install-extension", vsix_path], timeout=60).returncode == 0
        except (OSError, subprocess.SubprocessError):
            done = False

        if done:
            print(f"  ✓ Subway extension installed in {name}.")
            results.append("installed-now")
        else:
            print(f"  ✗ Couldn't install in {name}. To try manually: {cli_path} --install-extension {vsix_path}")
            results.append("failed")

    return results


def main() -> None:
    args = sys.argv[1:]

    # Status check
    if "--status" in args:
        check_status()
        return

    # Disconnect / Removal
    if "--remove" in args or "--uninstall" in args:
        target = "all"
        for i, a in enumerate(args):
            if a in ("--remove", "--uninstall") and i + 1 < len(args) and not args[i + 1].startswith("-"):
                target = args[i + 1]
                break
        print(f"🚇 Disconnecting Subway Context ({target})...\n")
        if target == "all":
            remove_all()
        else:
            remove_target(target)
        print("\n✓ Completed.")
        return

    server_py, py_bin = get_default_paths()
    print("🚇 Subway Context Universal Connector\n")
    print("Environment Detection:")
    envs = detect_environments()
    detected_keys = [k for k, v in envs.items() if v["detected"]]

    if not detected_keys:
        print("\n⚠️  No supported agent environments (Claude, Antigravity, OpenCode, pi) were detected.")
        print("Please install at least one supported tool before connecting Subway Context.")
        return

    print("\nDisclaimer & Privacy:")
    print("  Subway runs locally and serves MCP over stdio.")
    print("  The optional dashboard listens on 127.0.0.1 only.")
    print("  It writes generated stubs to ~/.config/opencode/agents.\n")

    # Determine targets
    cli_targets = parse_cli_targets(args, detected_keys)
    auto_yes = "--yes" in args or "-y" in args or "-f" in args or "--force" in args

    if cli_targets is not None:
        selected_keys = cli_targets
    elif auto_yes or not sys.stdin.isatty():
        selected_keys = detected_keys
    else:
        print("Select environments to connect:")
        for i, k in enumerate(detected_keys):
            print(f"  [{i + 1}] {envs[k]['name']}")
        print("  [A] All detected environments (Default)")
        print("  [Q] Cancel / Quit")

        try:
            choice = input("\nEnter choice (e.g. 1,2 or 'A') [A]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nInstallation aborted.")
            return

        if choice.lower() in ("q", "quit", "exit", "n", "no"):
            print("Installation cancelled. No changes made.")
            return

        selected_keys = _normalize_selection(choice if choice else "all", detected_keys)

    if not selected_keys:
        print("No environments selected. Exiting without changes.")
        return

    print(f"\nProceeding to connect Subway Context to {len(selected_keys)} environment(s):")
    installers = {
        "claude": install_claude,
        "antigravity": install_antigravity,
        "opencode": install_opencode,
        "pi": install_pi,
    }

    connected_count = 0
    for k in selected_keys:
        installer = installers.get(k)
        if installer:
            success, msg = installer(server_py, py_bin)
            icon = "✓" if success else "✗"
            print(f"  {icon} {envs[k]['name']:<22}: {msg}")
            if success:
                connected_count += 1

    failed = len(selected_keys) - connected_count
    if failed:
        print(f"\n✗ {connected_count} connected, {failed} failed. Nothing was changed for the failed ones.")
        sys.exit(1)
    print(f"\n✓ Completed. {connected_count} agent line(s) connected.")
    print("You can now bridge chats across your selected windows.")
    ensure_notifier(interactive=sys.stdin.isatty(), auto_yes=auto_yes)
    ensure_editor_extension(interactive=sys.stdin.isatty(), auto_yes=auto_yes)


if __name__ == "__main__":
    main()
