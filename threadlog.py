"""Worker spawner helper data and thread recording state.

Extracted from server.py. DEFAULT_MODELS stays here (re-exported by server.py).
"""
from __future__ import annotations

import contextvars
import subway_home
import re
from typing import Any, Dict, List, Optional, Tuple

_WORD_CACHE: Dict[str, "re.Pattern[str]"] = {}

_SCALE_RE = re.compile(r"\b(\d[\d,]*)\s*\+?\s*(?:[a-z-]+\s+)?(?:documents?|docs?|files?|records?|rows?|pages?|items?|"
                       r"repos?|repositories|modules?|services?|urls?|images?|tables?|emails?|entries)\b")

# Specific phrases only: words like "private" also appear in ordinary engineering tasks. private=True is the real switch.
# Phrases that say the content itself must stay on this machine. Topic words such as "personal data" or
# "api key" turn up in ordinary research and code, so they no longer force a task local: pass private=True for that.
PRIVATE_WORDS = ["confidential", "local only", "on-device", "do not upload", "don't upload", "keep this private",
                 "must stay local"]
BULK_WORDS = ["extract", "extraction", "classify", "summarize", "summarise", "label", "tag", "translate", "categorize",
              "categorise", "dedupe", "normalize", "normalise"]

# Strength per skill, 1 to 5. These are rankings, not published scores: the owner's benchmark view sets the top
# (Opus 5.5 beats Gemini 3.8 at coding, Argon matches Opus but has less allowance). Override any of them in models.json.
DEFAULT_MODELS: List[Dict[str, Any]] = [
    {"provider": "claude", "model": "opus", "coding": 5, "reasoning": 5, "writing": 4},
    {"provider": "antigravity", "model": "gemini-4-argon-high", "coding": 5, "reasoning": 5, "writing": 4, "scarce": True},
    {"provider": "antigravity", "model": "gemini-4-argon-medium", "coding": 4, "reasoning": 4, "writing": 4},
    {"provider": "antigravity", "model": "gemini-4-argon-low", "coding": 4, "reasoning": 4, "writing": 4},
    {"provider": "claude", "model": "sonnet", "coding": 4, "reasoning": 4, "writing": 4},
    {"provider": "antigravity", "model": "gemini-3.8-flash-high", "coding": 3, "reasoning": 3, "writing": 3},
    {"provider": "antigravity", "model": "gemini-3.8-flash-medium", "coding": 3, "reasoning": 3, "writing": 3},
    {"provider": "claude", "model": "haiku", "coding": 2, "reasoning": 2, "writing": 3},
    {"provider": "antigravity", "model": "gemini-3.8-flash-low", "coding": 2, "reasoning": 2, "writing": 2},
    {"provider": "antigravity", "model": "claude-opus-5-5-high", "coding": 5, "reasoning": 5, "writing": 4, "scarce": True},
    {"provider": "antigravity", "model": "claude-opus-5-5-medium", "coding": 5, "reasoning": 5, "writing": 4, "scarce": True},
    {"provider": "antigravity", "model": "claude-opus-5-5-low", "coding": 5, "reasoning": 5, "writing": 4, "scarce": True},
    {"provider": "antigravity", "model": "claude-sonnet-5-5-high", "coding": 4, "reasoning": 4, "writing": 4},
    {"provider": "antigravity", "model": "claude-sonnet-5-5-medium", "coding": 4, "reasoning": 4, "writing": 4},
    {"provider": "antigravity", "model": "claude-sonnet-5-5-low", "coding": 4, "reasoning": 4, "writing": 4},
    {"provider": "antigravity", "model": "gpt-oss-120b-medium", "coding": 3, "reasoning": 3, "writing": 3},
    {"provider": "opencode", "model": "", "coding": 2, "reasoning": 2, "writing": 2},
    {"provider": "local", "model": "", "coding": 1, "reasoning": 1, "writing": 2},
]
ROLE_SKILLS = [
    ("coding", r"cod(er|ing)|engineer|develop|programmer|architect|refactor|debug|implement|test"),
    ("reasoning", r"review|audit|security|research|analy|plann|strateg"),
    ("writing", r"writ|doc|editor|copy|translat"),
]
TASK_SKILLS = [
    ("coding", ["bug", "code", "function", "refactor", "implement", "endpoint", "test", "tests", "parser", "api", "compile"]),
    ("writing", ["document", "readme", "docs", "write", "rewrite", "summarize", "summarise", "translate", "copy"]),
    ("reasoning", ["review", "audit", "analyze", "analyse", "plan", "compare", "why", "research", "design"]),
]
TIER_STRENGTH = {"low": 2, "medium": 3, "high": 4, "frontier": 5}

# --------------------------------------------------------------------------
# Thread recording (roadmap item 7): every spawn, follow-up, reroute, approval,
# permission, note, terminal run and finish appends one record to a thread log,
# so the thread graph is built from the records, never guessed.
# --------------------------------------------------------------------------

_THREAD_PARENT: "contextvars.ContextVar[Optional[Tuple[str, str, Optional[Dict[str, Any]]]]]" = \
    contextvars.ContextVar("subway_thread_parent", default=None)

WORKER_RAM = 300 * 1024 * 1024  # Measured: a cloud agent process uses about 130-250 MB.

import threads

KINDS = threads.KINDS
RECORD_KINDS = threads.RECORD_KINDS
EDGE_KINDS = threads.EDGE_KINDS
record = threads.record
record_edge = threads.record_edge


def record_event(base_dir: str = "", thread_id: str = "", kind: str = "", detail: Optional[Dict[str, Any]] = None,
                 node: str = "", peer: str = "", at: Optional[float] = None, **kwargs: Any) -> None:
    """Record an event in the thread log."""
    if not base_dir:
        import server as _s
        base_dir = str(_s.SUBWAY_DIR)
    threads.record(base_dir=base_dir, thread_id=thread_id, kind=kind, node=node or thread_id, peer=peer, data=detail, at=at)


def thread_log_read(thread: str, base_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read all records for a thread from the threads ledger."""
    import os
    import threads
    bdir = base_dir
    if not bdir:
        try:
            import server as _s
            bdir = getattr(_s, "SUBWAY_DIR", None)
        except ImportError:
            bdir = None
    if not bdir:
        bdir = subway_home.subway_dir()
    try:
        data = threads.read_thread(bdir, thread)
        return list(data.get("records") or [])
    except Exception:
        return []

