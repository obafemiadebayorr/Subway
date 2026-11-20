"""Environment for provider CLIs that Subway runs headless (continue, new chat, fork/transplant)."""
import os

_EDITOR_VARS = ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")


def headless_env(base=None):
    """Copy of the environment marked SUBWAY_HEADLESS=1, without the editor identity it may have inherited.

    The HUD is often started from a VS Code terminal, so its env says "claude-vscode". A headless run that
    inherits that looks like an open tab, and the wake hook then holds `claude -p` open waiting for events."""
    env = dict(os.environ if base is None else base)
    for k in list(env):
        if k in _EDITOR_VARS or k.startswith("VSCODE_"):
            env.pop(k, None)
    env["SUBWAY_HEADLESS"] = "1"
    return env
