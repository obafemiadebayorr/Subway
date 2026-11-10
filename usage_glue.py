"""Real Usage Sources & Budget Guard glue (_SOURCES registration and fetch wiring).

Extracted from server.py. Bodies/values unchanged. Re-export every moved name
from server.py so ``server.X`` keeps working.
"""
from __future__ import annotations

import concurrent.futures
import os
import re
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

HOME = os.path.expanduser("~")

_provider_5h_cache: Dict[str, Tuple[float, int]] = {}

QUOTA_ERROR_PATTERNS: Dict[str, List[str]] = {
    "opencode": [
        r"free\s+usage\s+limit\s+reached",
        r"usage\s+limit\s+reached",
        r"rate\s+limit\s+exceeded",
        r"resource_exhausted",
        r"authentication\s+required",
        r"provider\s+authentication\s+required",
    ],
    "antigravity": [
        r"usage\s+limit\s+reached",
        r"rate\s+limit\s+exceeded",
        r"resource_exhausted",
    ],
    "claude": [
        r"usage\s+limit\s+reached",
        r"rate\s+limit\s+exceeded",
    ],
    "copilot": [
        r"usage\s+limit\s+reached",
        r"rate\s+limit\s+exceeded",
        r"is\s+not\s+available",
    ],
    "pi": [
        r"theos_insufficient",
        r"theos_exhausted",
        r"usage\s+limit\s+reached",
        r"rate\s+limit\s+exceeded",
        r"resource_exhausted",
        r"quota",
        r"insufficient",
    ],
}

# --------------------------------------------------------------------------
# Feature B.2: Real Usage Sources & Budget Guard
# --------------------------------------------------------------------------

LIVE_SECONDS = {"antigravity": 60, "claude": 180}
APPLICATIONS_DIRS = ["/Applications", os.path.join(HOME, "Applications")]
FIVE_H = 5 * 3600
SEVEN_D = 7 * 86400

_usage: Dict[str, Dict[str, Any]] = {}
_usage_lock = threading.Lock()
_viewer_seen = 0.0

_RESET_RE = re.compile(r"resets\s+(?:([A-Z][a-z]{2})\s+(\d{1,2})\s+at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:\(([^)]+)\))?", re.I)

_ledger_files: Dict[str, Tuple[Tuple[float, int], List[Tuple[float, int, int, int]]]] = {}

_ledger_cache: Dict[Any, Dict[str, Any]] = {}

_usage_tails: Dict[str, Tuple[int, Dict[str, Tuple[float, int, int, int]]]] = {}

GB = 1024 ** 3

_ollama_caps: Dict[str, List[str]] = {}
# Model details from the same /api/show reply, keyed by digest so one call fills both.
_ollama_details: Dict[str, Dict[str, Any]] = {}

# Bits per weight for Ollama's quantization labels. A derived model (a new window over a parent) uses its
# parent's measured size, since one dictionary guess missed a 3.5 GB model by three times.
_BPW = {"Q4_0": 4.5, "Q4_K_M": 4.8, "Q4_K_S": 4.6, "Q5_K_M": 5.7, "Q6_K": 6.6, "Q8_0": 8.5, "F16": 16, "BF16": 16}

from usage import (_pi_usage, 
    _fetch_agy_limits,
    _fetch_agy_tokens,
    _fetch_claude_live,
    _fetch_claude_cached,
    _fetch_claude_tokens,
    _fetch_opencode_usage,
)
from local_models import _fetch_local

SOURCE_DEADLINE: float = float(os.environ.get("SUBWAY_SOURCE_DEADLINE", "1.5"))

_SOURCES: Dict[str, Tuple[Callable[[], Dict[str, Any]], float]] = {
    "agy_limits": (_fetch_agy_limits, 10.0),
    "agy_tokens": (_fetch_agy_tokens, 30.0),
    "claude_live": (_fetch_claude_live, 60.0),
    "claude_cached": (_fetch_claude_cached, 3.0),
    "claude_tokens": (_fetch_claude_tokens, 30.0),
    "opencode": (_fetch_opencode_usage, 5.0),
    "local": (_fetch_local, 5.0),
    "pi_tokens": (_pi_usage, 60.0),
}
_SHARED_SOURCES = ("agy_limits", "claude_live")  # Reused across processes, and kept as last-known values.

_source_pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="subway-source")
_source_in_flight: Dict[str, Tuple[concurrent.futures.Future, Any]] = {}
_source_in_flight_lock = threading.Lock()

_other_sessions_cache: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}
_other_sessions_refreshing: set[str] = set()
_other_sessions_refresh_lock = threading.Lock()

_KIMI_TOKEN_DIR = ".kimi-code/credentials"

_last_low_limit_check: float = 0.0

_limit_checked = 0.0
