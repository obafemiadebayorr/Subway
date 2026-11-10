"""File/touch patterns, station/checkpoint constants, decision heuristics, and redaction patterns.

Extracted from server.py. Pattern/constant objects are re-exported by server.py
so other modules reading server.* see the same values.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------
# File extraction & touch tracking
# --------------------------------------------------------------------------

FILE_PATH_RE = re.compile(
    r"""(?:\b|['"`])((?:[\w.-]+/)*[\w.-]+\.(?:py|js|ts|tsx|jsx|html|css|json|md|sql|yaml|yml|sh|toml|go|rs|env|pbtxt))\b"""
)

# --------------------------------------------------------------------------
# Stations & Checkpoints (~/.subway-context/)
# --------------------------------------------------------------------------

ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_STUB_MARKER = "<!-- subway-auto-stub -->"
_STUB_SYNC_INTERVAL = 60.0

# Comprehensive decision & intent heuristics
DECISION_PATTERNS = [
    r"\b(?:decided|decision|deciding|concluded|conclusion|verdict)\b",
    r"\b(?:agreed|agreed upon|accepted|rule:|rules:)\b",
    r"\b(?:plan|action plan|strategy|roadmap|step \d+:)\b",
    r"\b(?:we will|i will|let's|let us|going forward|instead of|rather than|choose|chosen|switching to)\b",
    r"\b(?:refactor|implement|build|remove|deprecate|architect|architecture)\b",
]
DECISION_RE = re.compile("|".join(DECISION_PATTERNS), re.IGNORECASE)

# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

_SECRET_PATTERN = re.compile(
    r"(?i)\b(?:bearer\s+[A-Za-z0-9_\-\.=]+|gh[pousr]_[A-Za-z0-9_]{16,}|sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
    r"|(?:token|password|passwd|secret|api_?key|auth)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.\/+=@!#$%^&*]{6,})['\"]?"
)
