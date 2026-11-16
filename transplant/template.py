"""Placeholder rendering for transplant templates.

Providers are data: nothing here knows about any specific tool's session
storage. Templates are plain strings containing ``{name}`` placeholders.
"""

import datetime
import re
import uuid

# Matches a lone placeholder, an escaped brace pair, or a placeholder within
# a longer string. Captures the placeholder name.
_PATTERN = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")
_EXACT = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def new_uuid():
    return str(uuid.uuid4())


def now_iso():
    dt = datetime.datetime.now(datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def now_ms():
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)


def render(obj, ctx):
    """Deep-walk obj; substitute ``{name}`` placeholders from ctx.

    A string that is exactly one placeholder returns the ctx value with its
    type preserved (an int stays an int). Unknown placeholders raise
    KeyError naming the missing key. ``{{`` and ``}}`` render as literal
    braces. No eval and no str.format on untrusted input: substitution is
    done with re.sub and a plain function.
    """
    if isinstance(obj, dict):
        return {key: render(value, ctx) for key, value in obj.items()}
    if isinstance(obj, list):
        return [render(item, ctx) for item in obj]
    if isinstance(obj, str):
        exact = _EXACT.match(obj)
        if exact:
            name = exact.group(1)
            if name in ctx:
                return ctx[name]
            raise KeyError(name)
        return _substitute(obj, ctx)
    return obj


def _substitute(text, ctx):
    def _repl(match):
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        name = match.group(1)
        if name not in ctx:
            raise KeyError(name)
        return str(ctx[name])

    return _PATTERN.sub(_repl, text)
