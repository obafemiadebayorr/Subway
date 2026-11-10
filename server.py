#!/usr/bin/env python3
"""Subway: an on-demand, multi-window context transit system for AI coding agents.

Connects Antigravity, Claude Code, OpenCode (terminal), and any MCP-compatible agent.
Named after a subway transit system: jump between windows, lines, and stations on demand
without serial numbers or full re-reading penalties.

Features:
  1. Multi-Client Support:
     - Claude Code (~/.claude/projects/ & ~/.claude/sessions/)
     - Antigravity (~/.gemini/antigravity/brain/ & conversation_summaries.db)
     - OpenCode (~/.local/share/opencode/opencode.db)
  2. Quote / Anchor Search: search across any agent chat by exact words or code snippet.
  3. Incremental Stations / Watermarks: since="last", since="bridge -N" syncs only the delta.
  4. Decision & Rationale Extraction: mode="decisions" extracts instructions, plans,
     architectural choices, and execution commitments.
  5. Touch Manifest: auto-detects and lists all project files referenced or changed.
  6. Sticky Station Aliases: tag_chat("claude-main") so you don't need IDs.
  7. Cross-Agent Mailbox: send_note / check_notes for inter-agent handoffs.

Data sources (read-only):
  * Claude Code:  ~/.claude/projects/*/*.jsonl, ~/.claude/sessions/*.json
  * Antigravity:  ~/.gemini/antigravity/brain/<id>/.system_generated/logs/transcript.jsonl
                  & conversation_summaries.db
  * OpenCode:     ~/.local/share/opencode/opencode.db
  * GitHub Copilot: ~/.copilot/session-state/*/events.jsonl and VS Code User chatSessions/*.jsonl
Writes:
  * ~/.subway/ (aliases, checkpoints, note inboxes, leases, tracks, workers, HUD token)
  * ~/.config/opencode/agents/ (generated @alias stubs, marked <!-- subway-auto-stub -->)
"""
from __future__ import annotations

import atexit
import subway_home
import base64
import concurrent.futures
import contextlib
import contextvars
import datetime as _dt
import difflib
import glob
import hashlib
import hmac
import http.server
import json
import math
import os
import re
import secrets
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
sys.modules.setdefault("server", sys.modules[__name__])  # one module object when run as a script
import tempfile
import threading
try:
    import ctypes
except ImportError:
    ctypes = None  # type: ignore[assignment]
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

import terminals
import native_terminals
import threads

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

VERSION = "1.3.7"
if os.environ.get("_SUBWAY_SANDBOX_INITIALIZED") == "1":
    os.environ.pop("SUBWAY_HUD_NO_OPEN", None)
HOME = os.path.expanduser("~")
CLAUDE_DIR = os.environ.get("SUBWAY_CLAUDE_DIR", os.path.join(HOME, ".claude"))
CLAUDE_JSON = os.environ.get("SUBWAY_CLAUDE_JSON", os.path.join(HOME, ".claude.json"))
AGY_DIR = os.environ.get("SUBWAY_AGY_DIR", os.path.join(HOME, ".gemini", "antigravity"))
OPENCODE_DIR = os.environ.get("SUBWAY_OPENCODE_DIR", os.path.join(HOME, ".local", "share", "opencode"))
OPENCODE_AGENTS_DIR = os.environ.get("SUBWAY_OPENCODE_AGENTS_DIR",
                                     os.path.join(HOME, ".config", "opencode", "agents"))
COPILOT_DIR = os.environ.get("SUBWAY_COPILOT_DIR", os.path.join(HOME, ".copilot"))
def _discover_pi_dir() -> str:
    """The agent dir the running pi actually uses.

    pi's docs name ~/.pi/agent, but that is only the OSS default: DeepAI Code runs pi
    with PI_CODING_AGENT_DIR pointing at ~/.deepai-code/userdata/<app>/pi/<app>. Code
    that assumed the default silently read and wrote nothing there (settings, sessions
    and extensions were all affected). Resolved by env override, then the relocated
    userdata dir, then the OSS default.
    """
    for var in ("SUBWAY_PI_DIR", "PI_CODING_AGENT_DIR"):
        override = os.environ.get(var, "").strip()
        if override:
            return os.path.expanduser(override)
    try:
        candidates = sorted(glob.glob(os.path.join(HOME, ".deepai-code", "userdata", "*", "pi", "*")))
    except Exception:
        candidates = []
    for cand in candidates:
        if os.path.isfile(os.path.join(cand, "settings.json")) or os.path.isfile(os.path.join(cand, "models.json")):
            return cand
    return os.path.join(HOME, ".pi", "agent")


PI_DIR = _discover_pi_dir()
SUBWAY_DIR = subway_home.subway_dir()
LOCKS_FILE = os.path.join(SUBWAY_DIR, "locks.json")
WORKERS_DIR = os.path.join(SUBWAY_DIR, "workers")
TRACKS_DIR = os.path.join(SUBWAY_DIR, "tracks")
QUOTAS_FILE = os.path.join(SUBWAY_DIR, "quotas.json")
HUD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hud")
HUD_FILE = os.path.join(HUD_DIR, "index.html")
HUD_HOST = os.environ.get("SUBWAY_HOST") or "127.0.0.1"
HUD_PORT = int(os.environ.get("SUBWAY_HUD_PORT", "7890"))

DEFAULT_MAX_CHARS = 30000
MAX_THOUGHT_CHARS = 1200
_WORKER_READING_RULE = "Never read a file over 400 lines whole; grep for what you need, then read line ranges."
_EDIT_WORKER_RULE = "Make the edits early, before two thirds of your cap. Edit workers that finish with no file changes are marked failed."
_ORCHESTRATOR_RULES = (
    "Subway orchestrator rules (apply to this lead task):\n"
    "- Your FIRST tool calls MUST be spin_worker calls to spawn sub-workers; you are an orchestrator and MUST NOT attempt to do the whole task or write final target files directly.\n"
    "- Delegate, don't do it all yourself: split analysis and investigation into parallel wave-1 sub-workers via spin_worker (passing capabilities=['files:read', 'terminal:safe', 'mcp'] or ['files:read', 'files:write', 'terminal:safe'] as needed).\n"
    "- Multi-wave execution: wait for wave-1 reports with await_worker (or one watch_workers / collect_reports(wait=True) call), then spin wave-2 builder and reviewer sub-workers with exact file/line briefs from wave-1 findings. Never poll worker_status/hud_status/read_track/collect_reports in a loop.\n"
    "- Use diverse models: route sub-workers across local (e.g. gemma4-32k:latest / gemma4-24k:latest), antigravity (gemini-3.8-flash-low/medium), and copilot_inline; never use pools below shed_at_pct (20%).\n"
    "- If a sub-worker or test fails, immediately spin a fixer sub-worker automatically without waiting for user input."
)
_WORKER_STANDING_RULES = """SUBWAY STANDING RULES
Never read a file over 400 lines whole; grep for what you need, then read line ranges. For a Python symbol, read_symbol(file, symbol) first; claim_symbol(file, symbol) before editing it, and only fall back to claim_file when the change truly spans a whole file. Change only what the task asks.
Handle applicable edge cases: missing/corrupt inputs; claude, antigravity, opencode, copilot, local, custom; ask, auto_safe, auto_approve; isolated and in-place workers; silent providers.
On retry, reroute, or copy, preserve max_tokens, max_seconds, budgets, privacy, permissions, and launcher. Use the worker's own identity for its records.
For concurrency, use existing atomic-write helpers and state locks. Never log or print secrets.
Guard macOS, Linux, and Windows paths with existence checks. Keep HUD fields correct when empty, loading, or stale, including phone width.
Tests: use a temporary SUBWAY_DIR and patch/restore every touched *_DIR and home path; never touch real home state, network, or agent CLIs.
Clear module caches and restore env/patches. Do not sleep for timing; use threading.Event or timed joins. Test real functions, not reimplemented logic.
Run the full suite twice; tests must pass in any order."""


from store import (ttl_cached, log, clip, one_line, fmt_ts, sort_key_ts)


from chat_readers import _opencode_timestamp


# --------------------------------------------------------------------------
# File extraction & touch tracking
# --------------------------------------------------------------------------

from heuristics import FILE_PATH_RE  # noqa: E402


from chat_readers import extract_files_from_text


# --------------------------------------------------------------------------
# Stations & Checkpoints (~/.subway/)
# --------------------------------------------------------------------------

from store import (_ensure_subway_dir, _state_lock, _write_json_atomic, _load_json, _save_json)


from chat_readers import (slugify_topic, load_aliases)


from heuristics import ALIAS_RE, _STUB_MARKER, _STUB_SYNC_INTERVAL  # noqa: E402
# Stub-sync mutable state; rebound as server._last_stub_sync by chat_readers.aliases (keep here).
_last_stub_sync = 0.0


from store import _write_if_changed


from chat_readers import (_sync_opencode_agent_stubs, save_alias, _checkpoint_key, get_checkpoints, record_checkpoint, to_exchanges, render_exchange)


# Comprehensive decision & intent heuristics
from heuristics import DECISION_PATTERNS, DECISION_RE  # noqa: E402


from chat_readers import (render_decisions, render_chat)


# --------------------------------------------------------------------------
# Provider 1: Claude Code
# --------------------------------------------------------------------------

_claude_meta_cache: Dict[str, Tuple[Tuple[float, int], Dict[str, Any]]] = {}


from chat_readers import (claude_open_sessions, _read_tail, _read_head, _claude_is_human, claude_parse_lines, claude_files, claude_meta, _claude_read_full, claude_read, SegmentInfo, opencode_read_segments, agy_read_segments, claude_read_segments, chat_segments_info, chat_turns, _agy_db, _agy_transcript_path, _workspace_name, _workspace_paths, agy_list, _strip_user_request, agy_read, _opencode_db, opencode_list, opencode_read, _copilot_session_store, _copilot_worker_index, _copilot_open_sessions, _copilot_current_session, _copilot_int, _copilot_usage_checkpoint, _copilot_session_usage, _copilot_cli_meta, _vscode_user_dirs, _vscode_workspace_project, _vscode_chat_files, _vscode_patch_path, _vscode_set_path, _vscode_chat_state, _vscode_response_text, _vscode_request_model, _vscode_chat_data, _vscode_transcript_path, _timestamp_seconds, _vscode_transcript_latest_timestamp, _vscode_chat_meta, _copilot_agent_host_ids, copilot_list, pi_files, pi_meta, pi_turns, pi_list)


from heuristics import _SECRET_PATTERN  # noqa: E402

from chat_readers import (_redact_secrets, _last_tool_line, copilot_read_segments, pi_read_segments, cursor_list, cursor_read, cursor_read_segments, devin_list, devin_read, devin_read_segments)


from usage import _copilot_tokens_for_session


# --------------------------------------------------------------------------
# Multi-Window Registry & Resolution — extracted
# --------------------------------------------------------------------------

from chat_readers import (  # noqa: E402
    _detect_self_uncached, _detected_chat, _id_matches, _newest,
    _project_names, _self_id, _short_id, all_chats, detect_self, resolve,
)
from registry import (  # noqa: E402
    _SELF_TTL, _chat_is_in_worker_worktree, _is_authorized_worker,
    _orig_self_id, _worker_chat_session_ids,
)

# Live self cache; rebound by chat_readers.dispatch via server._self_cache (keep here).
_self_cache = (0.0, None)

# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

from chat_readers import _get_last_entry_preview


from cli_tools import t_list_chats


from chat_readers import _file_may_contain


from agent_chat import t_agent_chat
from cli_tools import (t_search_chat, t_tag_chat, t_read_chat, t_send_note, t_check_notes, t_whoami)


# --------------------------------------------------------------------------
# Dynamic Binary Discovery (extracted)
# --------------------------------------------------------------------------
from binary_discovery import (
    find_agy_binary,
    find_claude_binary,
    find_copilot_binary,
    find_cursor_binary,
    find_devin_binary,
    find_opencode_binary,
    find_pi_binary,
)

# ---- Feature B: Live Workspace Guard & Shared Tracks — extracted to guard.py (3/7) ----

from guard import (  # noqa: E402
    _ALERTS_TRACK, DESTRUCTIVE_RE, _broadcast_track_internal, _canon_path,
    _clean_locks, _clean_track, _file_sha, _git_head, _track_file,
    _writes_subway_state, hook_guard, load_locks, read_track_entries,
    save_locks, t_broadcast_track, t_claim_file, t_join_track, t_list_locks,
    t_read_track, t_release_file, t_signoff_file,
)
from cli_tools.symbols import t_claim_symbol, t_read_symbol, t_read_outline
from thread_state import t_set_principal
from composer_context import t_context_slice, t_project_files, t_thread_export
from worker_supervision.code_items import _extract_code_item, outline

# --------------------------------------------------------------------------
# Automated Worker Spawner & Delegation
# --------------------------------------------------------------------------

WORKER_ID_RE = re.compile(r"^worker_[0-9a-f]{6}$")
VALID_CAPABILITIES = {"files:read", "files:write", "terminal:safe", "terminal:full", "browser", "mcp", "subagents"}
VALID_EFFORTS = ("low", "medium", "high", "max")
ACTIVE_WORKER_STATES = ("pending_approval", "launching", "running", "stopping")
DEFAULT_WORKER_TIMEOUT = 3600


from workers import (_archive_dir, _worker_file, _exit_file, _log_file, _load_worker, _save_worker, _strftime, _pid_alive, _log_tail, _job_type, _is_read_only_prompt, _worker_expected_to_edit, _git_change_fingerprint, _git_status_entries, _git_dirty_paths)


_HEADLESS_DENIAL_RE = re.compile(r'required the "([\w:.\-]+)" permission that headless mode cannot prompt for')


from workers import (_headless_permission_denial, _settle_worker_result, _determine_outcome, _record_read_guard_hit, _record_worker_stat, record_worker_outcome, load_routing_weights)


from local_models import _load_model_stats


from workers import (_matches_job_type, _update_worker_status, _all_workers, wait_for_worker)
from workers import _record_activity_spun, _record_activity_finished, get_worker_activity


from caffeinate import (sync_caffeinate, _stop_caffeinate)


from threadlog import _WORD_CACHE, _SCALE_RE  # noqa: E402

from workers import _has_any  # noqa: E402
from worker_supervision import classify_mcp_tool  # noqa: E402  (Roadmap 4a: publish/delete always asks)


from workers import (_scale_of, estimate_complexity)


# Section 4/7: threadlog (worker spawner helpers + thread recording state) — extracted/verified.
from threadlog import (  # noqa: E402
    BULK_WORDS, DEFAULT_MODELS, PRIVATE_WORDS, ROLE_SKILLS, TASK_SKILLS, TIER_STRENGTH,
)


from local_models import load_custom_providers
from model_benchmarks import benchmark_strengths, refresh_benchmarks


_discovery_lock = threading.Lock()
_discovery_refreshing: Set[str] = set()
_discovery_threads: Dict[str, threading.Thread] = {}


from local_models import (_refresh_discovery_background, _model_metadata, _antigravity_models, _opencode_models, _local_models, _claude_models, _cursor_models, _devin_models, _pi_models, pi_agent_dir, pi_env)


COPILOT_CLI_MODELS_FILE = "copilot_cli_models.json"
COPILOT_CLI_MODEL_EXPIRY = 7 * 86400.0  # 7 days


from local_models import (_load_copilot_cli_models, _save_copilot_cli_models, record_copilot_cli_refused, record_copilot_cli_accepted, is_copilot_cli_refused, _copilot_models, _catalog_stamp, _discovered_model_ids, _validate_model_for_provider)


_LOCAL_OPENCODE_PROVIDERS = {"ollama", "lmstudio", "llama.cpp", "local"}


from usage import (_opencode_free_exhausted_models, _is_opencode_model_exhausted)


from local_models import (model_catalog, _model_catalog, set_model_rankings, DynamicModelCatalog, load_discovered_models, save_discovered_models, get_edge_endpoints, _discover_edge_models)


from workers import (_skill_for, _pool_left)


from usage import _parse_reset_seconds


from local_models import (_mark_model_bad, _mark_model_stall_cooldown, _clear_model_stall_cooldown, _match_bad_entry, _model_is_bad, _resolve_agy_model)


from workers import (route_task, t_routing_weights)


from cli_tools import t_estimate_complexity


from workers import (_safe_prompt, _worker_command)


PERMISSION_MODES = {
    "ask": "Approve everything",
    "auto_safe": "Auto-approve safe commands",
    "auto_approve": "Auto-approve everything",
}
_MODE_ALIASES = {"standard": "ask", "dangerously_skip": "auto_approve"}


from permissions import get_permission_mode


from usage import (chat_tokens_used, _provider_5h_tokens, get_compaction)


COMPACT_AT_DEFAULT = 300000  # Every turn re-reads the whole context, so a big context costs on every turn.


from usage import (compact_at_tokens, _claude_settings_file, _sync_claude_autocompact, set_compaction, chat_context_usage, compaction_threshold_pct, compaction_threshold, is_compaction_quiet, compact_focus_line, compact_opencode_workers, compaction_notice, request_chat_compaction, pending_compaction_requests, clear_compaction_requests, _chat_budget_cfg, chat_budget_for, chat_budget_rows, set_budget, set_chat_budget, budget_state)

from usage_glue import _provider_5h_cache

from usage import (_cached_provider_5h_tokens, _newest_claude_transcript_tokens, budget_rows, budget_ping_notice, _provider_budget_reset, enforce_budgets)

from permissions import (set_permission_mode, get_terminal_mode, set_terminal_mode, get_protected_commands, get_protected_exceptions, thread_permission_rows)


AGY_SAFE_COMMANDS = ["ls", "cat", "head", "tail", "wc", "grep", "rg", "find", "mkdir", "git status", "git diff", "git log",
                     "git grep", "git show", "git blame", "git ls-files",
                     "pytest", "npm test", "python3 -m pytest", "python3 -m unittest", "python3 --version",
                     "/usr/bin/python3 -m unittest", "/usr/bin/python3 --version"]
# Copilot matches shell rules on the command name only (plus git/gh subcommands), so "python3 -m unittest" can't
# be scoped; these are allowed only together with --sandbox.
COPILOT_SANDBOXED_COMMANDS = ["python3", "/usr/bin/python3"]
PERMISSION_WAIT = 1800


from permissions import approval_code


from store import _file_sha256



_TRIGGER_STEP_FIELDS = {"role", "prompt", "provider", "model", "when", "include", "other_provider", "max_tokens", "verdict", "then", "capabilities", "effort"}
_TRIGGER_WHEN = ("completed", "failed", "always", "approved", "rejected")
_TRIGGER_EFFORTS = ("auto", "low", "medium", "high", "max")
_TRIGGER_INCLUDE = ("patch", "report", "both", "none")
_REVIEW_PROMPT = ("Read-only review. Do not edit any file and use no tools. Reply with the first line APPROVE or REJECT: <reason>, "
                  "then at most 4 lines. Approve only if the patch does the TASK below, nothing else, and would not break "
                  "existing behaviour. Anything the TASK asks for is not scope creep.")

_TASK_RULES_OPENERS = ("Work only in this repo", "Private repo copy")


from worker_supervision import _reviewer_task_text


_REVIEW_DIFF_LIMIT = 60000


from worker_supervision import (_capture_in_place_review_diff, _parse_trigger_steps, _review_trigger_step, _resolve_review_step)


_PERMISSION_RANK = {"ask": 0, "auto_safe": 1, "auto_approve": 2}


from workers import (_lineage_parent, _inherit_from_parent)


from threadlog import _THREAD_PARENT, WORKER_RAM  # noqa: E402 — thread recording (section 4/7)

from workers import (_thread_parent, _thread_record, _thread_finished, _thread_root, _thread_root_for_caller, _settings_snapshot, t_spin_worker)  # noqa: E402
from concurrency import (run_concurrent, t_run_concurrent)


from workers import (capacity, _capacity_block, _git, _worktree_base, _prepare_worktree, _fallback_copilot_acp)


OPENCODE_WATCHDOG_TIMEOUT = 120


from workers import (_collect_worktree, _repo_has_uncommitted_changes, _supervisor_log_file, _verify_result, verify_worker, start_approved_workers, _launch_worker)


from permissions import (_perm_dir, _perm_file, _pending_permissions)


from hud_server import _hud_port


from store import _escape_xml


from hud_server.lifecycle import _notification_log_lock


from hud_server import (_log_notification_failure, _run_notification_command)


from store import _find_executable


from hud_server import _send_desktop_notification


from workers import notify_if_still_waiting


from hud_server import notify_desktop_event


from permissions import (_command_writes, _large_file_read_denial, _packet_reread_denial, _read_target, _readonly_terminal_denial, _terminal_command, request_permission)


def await_permission(rid: str, wait: int = PERMISSION_WAIT, stop: Optional[Callable[[], bool]] = None) -> str:
    """Block until the user answers. No answer means no: a worker never proceeds by default."""
    deadline = time.time() + wait
    while time.time() < deadline:
        if stop and stop():
            _answer_permission(rid, "deny", "worker stopped")
            return "deny"
        with contextlib.suppress(OSError, ValueError):
            with open(_perm_file(rid), "r", encoding="utf-8") as f:
                rec = json.load(f)
            if rec.get("status") != "pending":
                return str(rec.get("decision") or "deny")
        time.sleep(1.0)
    _answer_permission(rid, "deny", "timeout")
    return "deny"


from permissions import _answer_permission


from cli_tools import t_answer_permission


from permissions import (permission_text, wait_for_activity)


from cli_tools import (t_permission_requests, t_watch_workers, _terminals_dir, terminal_sharing_enabled, set_terminal_sharing, _tidy_terminals, t_list_terminals, t_read_terminal, t_search_terminal)


_last_capture_thread: Optional[threading.Thread] = None


from cli_tools import (_capture_hook_output, t_run_terminal)


from worker_supervision import (stream_activity, _agy_rules, _claude_resume, _agy_resume, _check_lead_tool_gate)


from usage import (_add_usage, _copilot_fresh_input, _normalise_copilot_worker_record, _check_cap, _resume_reuses_cache, _quota_error_event_text)


from worker_supervision import (_pump_plain, _pump_pi, _pump_claude, _pump_agy, _process_copilot_event)


from chat_readers import parse_copilot_jsonl


from worker_supervision import (_pump_copilot, _run_triggers, t_on_worker_done)

from iteration import t_spin_iteration, t_stop_iteration


DEFAULT_STALL_TIMEOUT = 120.0

from usage_glue import QUOTA_ERROR_PATTERNS


from usage import check_quota_error


from worker_supervision import (get_stall_timeout, _worker_stall_timeout, _process_cpu_time, _file_activity_signature, _copilot_has_open_tool_execution, _worker_folder_activity, _opencode_worker_session_activity, _worker_activity_signals, _collect_worker_activity_signals)


_SUBWORKER_ACTIVE_STATES = ("pending", "pending_approval", "launching", "running", "stopping")


from workers import _active_subworkers


from worker_supervision import (_update_worker_activity, _worker_is_stalled, _poll_opencode_worker_retry, _reroute_cost_rank)


from cli_tools import _ping_launcher


from worker_supervision import (_handle_worker_exhaustion_or_stall, supervise_worker)
from worker_supervision import (_originating_chat_for_worker, _wake_worker_milestone)


from workers import (t_spin_batch, _chunks)


from local_models import run_batch


from chat_readers import _opencode_api


from usage import _opencode_session_tokens


from workers.acp_pump import _opencode_progress_guard, _pump_opencode  # noqa: F401


def run_permit_server(worker_id: str) -> int:
    """Thin wrapper: the implementation lives in permissions.py."""
    from permissions import run_permit_server as _impl
    return _impl(worker_id)


from workers import (t_approve_worker, t_stop_worker, t_reject_worker, _track_only_workers, _worker_detail, worker_listing, archive_history, t_worker_archive, t_worker_unarchive, t_archive_settings, t_wake_settings)
from workers.spawn import t_prune_branches


from cli_tools import (t_worker_status, t_collect_reports, t_instruct_worker, t_await_worker)


BREADTH_WORDS = ["compare", "across", "survey", "every", "all", "each", "audit", "landscape", "versus", "vs"]
DEPTH_WORDS = ["research", "investigate", "deep dive", "analyze", "analyse", "benchmark", "review"]


from workers import estimate_workers


from cli_tools import (t_spin_swarm, t_escalate_worker)


# --------------------------------------------------------------------------
# Feature B.2: Real Usage Sources & Budget Guard
#
# Every number here comes from the tool that owns it. When a tool doesn't report
# a value it stays None and the HUD says so: an estimate is never shown as a limit.
#
#   Antigravity  local language server, RetrieveUserQuotaSummary (per-pool limits)
#   Claude Code  `claude -p /usage`, which keeps its own login and costs no tokens,
#                falling back to the cache Claude Code writes to ~/.claude.json
#   OpenCode     its SQLite database (real tokens, per message). It reports no limits.
# --------------------------------------------------------------------------

from usage_glue import (
    LIVE_SECONDS, APPLICATIONS_DIRS, FIVE_H, SEVEN_D,
    _usage, _usage_lock, _viewer_seen, _RESET_RE,
    _ledger_files, _ledger_cache, _usage_tails,
    GB, _ollama_caps, _ollama_details, _BPW,
    SOURCE_DEADLINE, _SOURCES, _SHARED_SOURCES,
    _source_pool, _source_in_flight, _source_in_flight_lock,
    _other_sessions_cache, _other_sessions_refreshing, _other_sessions_refresh_lock,
    _KIMI_TOKEN_DIR, _last_low_limit_check, _limit_checked,
)

from usage import _parse_ts


# ---- Antigravity ----------------------------------------------------------

from chat_readers import (_agy_hubs, _agy_rpc)


from usage import _fetch_agy_limits


# ---- Claude Code ----------------------------------------------------------

from usage import (_parse_reset_text, _parse_usage_text, _fetch_claude_live, _read_claude_json, _fetch_claude_cached)


# ---- Real token counts ----------------------------------------------------

from usage import _window_totals


from usage import _scan_ledger


from usage import (_parse_claude_usage, _fetch_claude_tokens, _parse_agy_usage, _fetch_agy_tokens, _fetch_opencode_usage, _pi_usage, pi_theos, token_history)
# ---- Local LLMs -----------------------------------------------------------

from local_models import (_ollama_base, _ollama, memory_snapshot, local_profile)


from local_models import (_estimate_local_model_bytes, _fetch_local, local_room, local_fit, choose_local_model, _unload_local, _wait_for_memory, local_capacity)


# ---- Source registry ------------------------------------------------------

from usage import (_run_source_task, _fallback_source_record, _refresh_source, _source, _sampler_loop)


# ---- Detection ------------------------------------------------------------

from hud_server import (_config_has_subway, _subway_sessions, _app_installed)


from store import _home


from hud_server import (detect_clients, _window, _state_of)


from hud_server import (_other_sessions, _other_sessions_for_hud, _source_record_age, _other_sessions_uncached, _claude_view, _agy_view)


from usage import _copilot_cli_usage


from chat_readers import _copilot_raw

from hud_server import _copilot_view

from usage import (_kimi_token, _KimiUsageError, _kimi_raw)

from hud_server import (_kimi_view, _opencode_view, _local_view, _pi_view, discover_connected_providers)


from usage import (load_quotas, _select_pool)


from usage import (low_limit_notice, _fmt_reset, check_budget_preflight)


from cli_tools import (t_quota_status, t_set_quota)


# --------------------------------------------------------------------------
# Feature C: Live Station Board HUD (Dashboard & HTTP Server)
# --------------------------------------------------------------------------

from local_models import get_models_track


from usage import get_exhausted_models


from chat_readers import get_chat_context


from hud_server import _compile_hud_data


from hud_server.lifecycle import _hud_cache_lock, _hud_cache


from hud_server import (mark_hud_cache_stale, _bg_refresh_hud, get_hud_data, t_hud_status)


_POLL_GUARD_CACHE: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
_POLL_GUARD_LOCK = threading.Lock()
POLL_GUARD_TOOLS = frozenset({"worker_status", "hud_status", "read_track", "collect_reports"})


def poll_guard_check(tool_name: str, target: str, output: str, sig_text: Optional[str] = None) -> str:
    """Repeat status/report polls are cheap: an unchanged result from the same caller for the
    same target collapses to one line, and after poll_guard_limit identical calls in a row
    refuses, pointing the caller at await_worker. A real change always returns the full output.
    Pass sig_text to compare on content that excludes purely cosmetic fields (e.g. a live clock)."""
    caller_id = _self_id() if callable(_self_id) else "unknown"
    key = (caller_id, tool_name, target or "")
    sig = hashlib.sha256((sig_text if sig_text is not None else (output or "")).encode("utf-8", "replace")).hexdigest()
    cfg = _load_json("config.json") or {}
    limit = int(cfg.get("poll_guard_limit", 3) or 0)
    with _POLL_GUARD_LOCK:
        entry = _POLL_GUARD_CACHE.get(key)
        if entry is None or entry.get("sig") != sig:
            _POLL_GUARD_CACHE[key] = {"sig": sig, "repeats": 0, "ts_iso": fmt_ts(time.time())}
            return output
        entry["repeats"] = int(entry.get("repeats") or 0) + 1
        if limit and entry["repeats"] >= limit:
            return (f"[poll guard] {tool_name}({target or ''}) unchanged for {entry['repeats']} calls in a row. "
                     "Stop polling: wait with await_worker instead.")
        return f"no change since {entry.get('ts_iso', 'unknown')}."


from hud_server.lifecycle import _hud_token_cache


from hud_server import (hud_token, _hud_url, _hud_probe, _autostart_hud, _editors, _host_editor, _cli_path, _has_extension)


from hud_server.lifecycle import EDITOR_HINT


from hud_server import (_find_editor_cli, _open_hud, t_start_hud, t_restart_hud, t_run_server)


from hud_server.lifecycle import MAX_BODY_BYTES


from hud_server import SubwayHUDHandler




from hud_server import SubwayHUDServer


from hud_server.lifecycle import _hud_server_instance, _sampler_running, _hud_opened_on_use, _hud_open_lock, _hud_open_thread


from hud_server import start_hud_server

from live_reload import (check_and_reload, _server as _lr_server, _last_reload_error)
from copilot_inline import t_copilot_inline


# --- first-run onboarding: t_subway(list) returns use-when hints (minimal, owned edit) ---
_ONBOARDING_USE_WHEN = {
    "read_chat": "Catch up on another agent; use mode=\"decisions\" first, since=\"last\" for deltas.",
    "search_chat": "Find a chat/topic without UUIDs; use quote= to jump to an exact point.",
    "list_chats": "Discover active lines/stations across agents and recent sessions.",
    "tag_chat": "Pin a chat to an @alias so others can address it by name.",
    "claim_file": "Lease a file BEFORE editing to avoid multi-agent collisions.",
    "claim_symbol": "Lease one function so others can edit the rest of the file.",
    "set_principal": "Hand a thread's principal baton to another chat, with its leases and notes.",
    "read_symbol": "Read one function or class (and optionally its callers) instead of a whole file.",
    "read_outline": "List a file's classes and functions with line ranges, not the bodies.",
    "project_files": "Find files in the project by name for the composer's @ mentions.",
    "context_slice": "Build a token-capped context slice of files and threads for a prompt.",
    "thread_export": "Export a whole thread (ledger, workers, patches) as Markdown or JSON.",
    "send_note": "Leave a dispatch/mailbox note for another agent window.",
    "check_notes": "Read and clear dispatches left for this agent window.",
    "spin_worker": "Delegate an isolated task to a worker; keep scope small and spec exact.",
    "escalate_worker": "Escalate when a worker struggles, stalls, or needs a stronger model/route.",
    "instruct_worker": "Steer a running worker mid-run with new guidance or constraints.",
    "on_worker_done": "Register follow-up actions; chain dependent work with then=.",
    "quota_status": "Check spend/quota before big runs to avoid surprise exhaustion.",
    "estimate_complexity": "Get routing/effort advice before choosing model or delegation shape.",
    "broadcast_track": "Share a decision on a track with files/decisions for the fleet.",
    "collect_reports": "Gather completed worker reports; wait=True to block for results.",
    "prune_branches": "List or delete finished worker branches and worktrees (dry run first).",
}


def t_subway(tool: str, args: Optional[Dict[str, Any]] = None) -> str:
    """Stable dispatcher to call any Subway tool by name."""
    _s = _lr_server()
    if tool == "list":
        tools = []
        for name, (_fn, spec) in _s.TOOLS.items():
            item = {"name": name, "inputSchema": spec.get("inputSchema", {})}
            if name in _ONBOARDING_USE_WHEN:
                item["use_when"] = _ONBOARDING_USE_WHEN[name]
            tools.append(item)
        return json.dumps(tools, indent=2)
    entry = _s.TOOLS.get(tool)
    if entry is None:
        msg = f"Error: unknown tool {tool}"
        if _last_reload_error:
            msg += f"\nLast reload error: {_last_reload_error}"
        return msg
    fn = entry[0]
    fn_name = getattr(fn, "__name__", None)
    fn_module = getattr(fn, "__module__", None)
    if fn_name and fn_module and fn_module in sys.modules:
        try:
            fresh = getattr(sys.modules[fn_module], fn_name, None)
            if fresh is not None and fresh is not fn:
                fn = fresh
        except Exception:
            pass
    return fn(**(args or {}))


READ_SOURCE = {"type": "string", "enum": ["all", "antigravity", "claude", "opencode", "copilot"], "default": "all"}

def t_wake_queue(action: str = "list", run_id: str = "", args: str = "", note: str = "", limit: int = 5) -> str:
    """Queue a worker run for when this Mac wakes up, or drain what is waiting.

    action=list | add | drain | remove. `args` is a JSON object with the same fields spin_worker takes.
    """
    import json as _json

    import wake_queue as _wq

    act = (action or "list").strip().lower()
    if act == "add":
        try:
            parsed = _json.loads(args or "{}")
        except ValueError as exc:
            return f"Error: args must be JSON ({exc})."
        if not isinstance(parsed, dict) or not parsed.get("role") or not parsed.get("prompt"):
            return "Error: args needs at least `role` and `prompt`."
        run = _wq.enqueue(SUBWAY_DIR, parsed, note=note)
        return f"[QUEUED] {run['id']} will run when this Mac wakes ({parsed.get('role')})."
    if act == "drain":
        drained = _wq.drain(SUBWAY_DIR, lambda **kw: t_spin_worker(**kw), limit=max(1, int(limit)))
        if not drained:
            return "Nothing is waiting to run."
        return "\n".join(f"- {r['id']}: {r['status']} — {r.get('result', '')[:160]}" for r in drained)
    if act == "remove":
        return "[REMOVED]" if _wq.remove(SUBWAY_DIR, run_id) else f"Error: no queued run `{run_id}`."
    waiting = _wq.listing(SUBWAY_DIR, include_done=True)
    if not waiting:
        return "The wake queue is empty."
    lines = []
    for r in waiting[-20:]:
        lines.append(f"- {r['id']} [{r['status']}] {r['args'].get('role', '?')}: {str(r['args'].get('prompt', ''))[:70]}")
    return "\n".join(lines)


def t_wake_status() -> str:
    """Read the Mac's sleep settings so a phone knows whether a closed lid can still work."""
    import wake as _wake

    return _wake.human_status(_wake.status())


TOOLS = {
    "subway": (t_subway, {
        "description": "Stable dispatcher to call any Subway tool (including newly reloaded tools) by name. tool='list' returns names, schemas, and use-when hints.",
        "inputSchema": {"type": "object", "required": ["tool"], "properties": {
            "tool": {"type": "string", "description": "Tool name, or 'list' to see current tools."},
            "args": {"type": "object", "description": "Arguments object for the tool."}
        }}
    }),
    "list_chats": (t_list_chats, {
        "description": "List all active lines and stations (chats) across Antigravity, Claude Code, OpenCode, and GitHub Copilot. "
                       "Highlights open tabs and sticky @aliases.",
        "inputSchema": {"type": "object", "properties": {
            "source": READ_SOURCE,
            "project": {"type": "string", "description": "Filter by project directory, e.g. 'my-project'."},
            "query": {"type": "string", "description": "Filter by text substring in title or prompts."},
            "limit": {"type": "integer", "default": 15},
            "include_subagents": {"type": "boolean", "default": False},
        }},
    }),
    "search_chat": (t_search_chat, {
        "description": "Find which agent window contains an exact phrase, code snippet, or instruction. Searches whole "
                       "transcripts, newest chats first. Returns the station ID and stop index to board from.",
        "inputSchema": {"type": "object", "required": ["quote"], "properties": {
            "quote": {"type": "string", "description": "The phrase, quote, or code snippet you remember."},
            "query": {"type": "string", "description": "Alias for quote."},
            "source": READ_SOURCE,
            "project": {"type": "string"},
            "segment": {"type": "string", "description": "'all' (default), 'latest', or a segment number, split at each compaction."},
            "limit": {"type": "integer", "default": 5},
            "include_subagents": {"type": "boolean", "default": True, "description": "Search across spun worker subagents as well."},
        }},
    }),
    "tag_chat": (t_tag_chat, {
        "description": "Assign a sticky station alias to a chat (e.g. tag_chat(alias='claude-main')). "
                       "If chat_id is omitted, it automatically tags the current active session. "
                       "You can then use '@alias' directly without remembering IDs.",
        "inputSchema": {"type": "object", "required": ["alias"], "properties": {
            "alias": {"type": "string", "description": "Memorable nickname, e.g. 'claude-main' or 'terminal-coder'."},
            "chat_id": {"type": "string", "description": "Optional Chat ID or prefix. Omit to tag current chat."},
        }},
    }),
    "read_chat": (t_read_chat, {
        "description": "Board and read a chat context from another agent, including GitHub Copilot CLI and VS Code chats. "
                       "chat_id can be UUID, prefix, open tab name, or @alias. "
                       "Modes: 'recent' (last N stops), 'decisions' (comprehensive rulings/files only), 'outline', 'full'. "
                       "since='last' returns what you haven't seen, and continues where a truncated read stopped. "
                       "anchor_text='phrase' boards at an exact quote.",
        "inputSchema": {"type": "object", "required": ["chat_id"], "properties": {
            "segment": {"type": "string", "description": "'all' (default), 'latest', or a segment number, split at each compaction."},
            "chat_id": {"type": "string", "description": "Chat ID, prefix, open tab name, or @alias."},
            "mode": {"type": "string", "enum": ["recent", "outline", "full", "decisions"], "default": "recent"},
            "turns": {"type": "integer", "default": 6, "description": "Number of exchanges for recent/outline."},
            "start": {"type": "integer", "description": "First exchange index to board from."},
            "since": {"type": "string", "description": "Delta since previous transit: 'last' or 'bridge -N'."},
            "anchor_text": {"type": "string", "description": "Board from the exchange containing this exact quote."},
            "max_chars": {"type": "integer", "default": DEFAULT_MAX_CHARS},
            "include_tools": {"type": "boolean", "default": False},
            "include_thoughts": {"type": "boolean", "default": True},
            "source": READ_SOURCE,
            "project": {"type": "string"},
        }},
    }),
    "send_note": (t_send_note, {
        "description": "Send a transit dispatch to another agent window (seen via check_notes). "
                       "Needs an exact alias, ID, or tab name: it won't guess a recipient from a title.",
        "inputSchema": {"type": "object", "required": ["to", "text"], "properties": {
            "to": {"type": "string", "description": "Recipient chat ID, prefix, tab name, or @alias."},
            "text": {"type": "string"},
            "from_chat": {"type": "string"},
            "share_chat": {"type": "string"},
        }},
    }),
    "check_notes": (t_check_notes, {
        "description": "Check and clear dispatches left for this agent window.",
        "inputSchema": {"type": "object", "properties": {
            "my_chat_id": {"type": "string"},
            "keep": {"type": "boolean", "default": False},
        }},
    }),
    "agent_chat": (t_agent_chat, {
        "description": "Threaded agent-to-agent dialogue (consensus, steering, planning). "
                       "Post/list/close conversations; @-address stations; consensus proposals "
                       "collect approve/reject replies and write verdicts to a shared track. "
                       "Messages to running workers deliver via instruct_worker(now=True).",
        "inputSchema": {"type": "object", "properties": {
            "chat_id": {"type": "string", "description": "Conversation key, e.g. 'consensus-routing-fix'."},
            "to": {"description": "Recipient station alias/id(s): string or comma-separated list."},
            "text": {"type": "string", "description": "Message text (required to post)."},
            "reply_to": {"type": "string", "description": "Message id being replied to."},
            "join": {"type": "boolean", "default": True},
            "list": {"type": "boolean", "default": False, "description": "List open conversations."},
            "read": {"type": "boolean", "default": False, "description": "Read thread messages (or omit text)."},
            "close": {"type": "boolean", "default": False, "description": "Close/archive chat_id."},
            "consensus": {"type": "boolean", "default": False, "description": "Mark as a consensus proposal."},
            "track": {"type": "string", "default": "main", "description": "Track for consensus verdicts."},
            "limit": {"type": "integer", "default": 50, "description": "Max messages when reading."},
            "from_station": {"type": "string", "description": "Override sender (normally auto-detected)."},
        }},
    }),
    "worker_archive": (t_worker_archive, {
        "description": "Move a single finished worker's record, exit code and log into the archive. "
                       "Refuses if the worker is active. Use for HUD undo/archive of one worker; "
                       "use archive_history to archive all finished workers at once.",
        "inputSchema": {"type": "object", "required": ["worker_id"], "properties": {
            "worker_id": {"type": "string", "description": "Worker id, e.g. worker_ab12cd."},
        }},
    }),
    "worker_unarchive": (t_worker_unarchive, {
        "description": "Restore an archived worker's record, exit code and log back into the active "
                       "workers dir so it is queryable again. Use when you need to inspect or resume "
                       "a past worker's detail after archive_history moved it aside.",
        "inputSchema": {"type": "object", "required": ["worker_id"], "properties": {
            "worker_id": {"type": "string", "description": "Worker id, e.g. worker_ab12cd."},
        }},
    }),
    "archive_settings": (t_archive_settings, {
        "description": "Read or set how long archived worker records are kept, or run cleanup now. "
                       "Compress and delete are each one of off, 3, 7, 30, 60, 90 days. Active workers are never touched.",
        "inputSchema": {"type": "object", "properties": {
            "compress_days": {"type": "string", "description": "Compress archived workers older than this: off, 3, 7, 30, 60 or 90."},
            "delete_days": {"type": "string", "description": "Delete archived workers older than this: off, 3, 7, 30, 60 or 90."},
            "branch_days": {"type": "string", "description": "Delete finished worker branches older than this: off, 3, 7, 30, 60 or 90."},
            "action": {"type": "string", "description": "'maintain' runs compression and deletion now."},
        }},
    }),
    "wake_status": (t_wake_status, {
        "description": "Report whether this Mac can be woken for network access (pmset womp/powernap), with the exact commands to enable it.",
        "inputSchema": {"type": "object", "properties": {}},
    }),
    "wake_queue": (t_wake_queue, {
        "description": "Queue a worker run to execute when this Mac wakes (action=add, args=JSON), list what waits, drain it, or remove one.",
        "inputSchema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "add", "drain", "remove"]},
            "run_id": {"type": "string"},
            "args": {"type": "string", "description": "JSON object: role, prompt, provider, model, max_tokens, ..."},
            "note": {"type": "string"},
            "limit": {"type": "integer"},
        }},
    }),
    "wake_settings": (t_wake_settings, {
        "description": "Read or set wake-up event policies (all, outcomes, failures, off).",
        "inputSchema": {"type": "object", "required": [], "properties": {
            "chat_id": {"type": "string", "description": "Optional chat ID to configure per-chat wake policy."},
            "policy": {"type": "string", "description": "Wake policy: all, outcomes, failures, or off (empty to read)."},
        }},
    }),
    "prune_branches": (t_prune_branches, {
        "description": "List or delete finished worker branches and worktrees (dry run first).",
        "inputSchema": {"type": "object", "properties": {
            "days": {"type": "integer", "description": "Delete branches older than this many days (default 14)."},
            "dry_run": {"type": "boolean", "default": True, "description": "When true, report what would be deleted without deleting."},
            "project_dir": {"type": "string", "description": "Optional repository path (defaults to current project)."},
        }},
    }),
    "set_principal": (t_set_principal, {
        "description": "Hand a thread's principal baton to another chat, with its leases and notes.",
        "inputSchema": {"type": "object", "required": ["thread_id", "principal_id"], "properties": {
            "thread_id": {"type": "string", "description": "Thread whose principal is being transferred."},
            "principal_id": {"type": "string", "description": "New principal chat ID (empty string clears)."},
            "note": {"type": "string", "description": "Optional reason recorded in the handoff history."},
            "auto_handoff": {"type": "boolean", "description": "Whether auto-handoff stays enabled on the thread."},
        }},
    }),
    "whoami": (t_whoami, {
        "description": "Report active station ID and line for this server.",
        "inputSchema": {"type": "object", "properties": {}},
    }),
    "claim_file": (t_claim_file, {
        "description": "Acquire a temporary workspace lease on a file to prevent collision and regressions across agents. "
                       "Prefer absolute paths, or pass project_dir with a relative path.",
        "inputSchema": {"type": "object", "required": ["filepath"], "properties": {
            "filepath": {"type": "string", "description": "Target file path to claim."},
            "holder": {"type": "string", "description": "Station ID or agent alias claiming the file (default: self)."},
            "duration_seconds": {"type": "integer", "default": 300, "description": "Lease lifetime in seconds (10-3600)."},
            "purpose": {"type": "string", "description": "Short explanation of the planned changes."},
            "baseline_commit": {"type": "string", "description": "Git commit hash to anchor against for Regression Shield."},
            "role": {"type": "string", "enum": ["implementer", "reviewer", "refactorer", "coordinator"], "default": "implementer"},
            "project_dir": {"type": "string", "description": "Base folder for a relative filepath."},
        }},
    }),
    "claim_symbol": (t_claim_symbol, {
        "description": "Acquire a temporary workspace lease on a specific function or class to allow concurrent editing of other parts of the file.",
        "inputSchema": {"type": "object", "required": ["filepath", "symbol"], "properties": {
            "filepath": {"type": "string", "description": "Target file path containing the symbol."},
            "symbol": {"type": "string", "description": "Function or class name to lease."},
            "holder": {"type": "string", "description": "Station ID or agent alias claiming the symbol (default: self)."},
            "duration_seconds": {"type": "integer", "default": 300, "description": "Lease lifetime in seconds (10-3600)."},
            "purpose": {"type": "string", "description": "Short explanation of the planned changes."},
            "project_dir": {"type": "string", "description": "Base folder for a relative filepath."},
            "baseline_commit": {"type": "string", "description": "Git commit hash to anchor against for Regression Shield."},
            "role": {"type": "string", "enum": ["implementer", "reviewer", "refactorer", "coordinator"], "default": "implementer"},
        }},
    }),
    "read_symbol": (t_read_symbol, {
        "description": "Read one function or class (and optionally its callers) instead of a whole file.",
        "inputSchema": {"type": "object", "required": ["file", "symbol"], "properties": {
            "file": {"type": "string", "description": "Target file containing the symbol."},
            "symbol": {"type": "string", "description": "Function or class name to read."},
            "include_callers": {"type": "boolean", "default": False, "description": "Whether to list call sites of the symbol."},
            "project_dir": {"type": "string", "description": "Base folder for relative paths."},
        }},
    }),
    "read_outline": (t_read_outline, {
        "description": "List a file's classes and functions with line ranges, not the bodies.",
        "inputSchema": {"type": "object", "required": ["file"], "properties": {
            "file": {"type": "string", "description": "Target file to outline."},
            "project_dir": {"type": "string", "description": "Base folder for relative paths."},
        }},
    }),
    "project_files": (t_project_files, {
        "description": "Find files in the project by name for the composer's @ mentions.",
        "inputSchema": {"type": "object", "properties": {
            "q": {"type": "string", "default": "", "description": "Fuzzy search query for file paths."},
            "project_dir": {"type": "string", "default": "", "description": "Base repository folder."},
            "limit": {"type": "integer", "default": 50, "description": "Maximum number of files to return."},
        }},
    }),
    "context_slice": (t_context_slice, {
        "description": "Build a token-capped context slice of files and threads for a prompt.",
        "inputSchema": {"type": "object", "properties": {
            "spec": {"type": "string", "default": "", "description": "File or symbol specification (e.g. path#item or #path:start-end)."},
            "path": {"type": "string", "default": "", "description": "Target file path."},
            "start_line": {"type": "integer", "default": 0, "description": "Starting line number (1-based)."},
            "end_line": {"type": "integer", "default": 0, "description": "Ending line number (1-based, inclusive)."},
            "project_dir": {"type": "string", "default": "", "description": "Base folder for relative paths."},
            "item": {"type": "string", "default": "", "description": "Symbol or item name alias for spec."},
        }},
    }),
    "thread_export": (t_thread_export, {
        "description": "Export a whole thread (ledger, workers, patches) as Markdown or JSON.",
        "inputSchema": {"type": "object", "required": ["thread"], "properties": {
            "thread": {"type": "string", "description": "Thread ID to export."},
            "fmt": {"type": "string", "default": "md", "description": "Export format: 'md', 'json', or 'patch'."},
        }},
    }),
    "signoff_file": (t_signoff_file, {
        "description": "Review and sign off on a completed file modification. The agent that changed a file can't approve it.",
        "inputSchema": {"type": "object", "required": ["filepath"], "properties": {
            "filepath": {"type": "string", "description": "Target file path to sign off."},
            "verdict": {"type": "string", "enum": ["approved", "needs_work"], "default": "approved"},
            "notes": {"type": "string", "description": "Evaluation rationale, test verification notes, or rework requirements."},
            "reviewer": {"type": "string", "description": "Station ID or alias of reviewer."},
            "project_dir": {"type": "string", "description": "Base folder for a relative filepath."},
        }},
    }),
    "release_file": (t_release_file, {
        "description": "Release your workspace file lease so other agents can edit it.",
        "inputSchema": {"type": "object", "required": ["filepath"], "properties": {
            "filepath": {"type": "string", "description": "File path to release."},
            "holder": {"type": "string", "description": "Station ID or agent alias releasing the file."},
            "project_dir": {"type": "string", "description": "Base folder for a relative filepath."},
            "force": {"type": "boolean", "default": False, "description": "Release another agent's lease. Only when your user asks."},
            "symbol": {"type": "string", "description": "Specific symbol lease to release instead of the whole file."},
        }},
    }),
    "list_locks": (t_list_locks, {
        "description": "List all active workspace file leases and sign-offs across all agents.",
        "inputSchema": {"type": "object", "properties": {}},
    }),
    "list_terminals": (t_list_terminals, {
        "description": "List terminals your user has opted in to sharing: name, folder, last command, failures.",
        "inputSchema": {"type": "object", "properties": {}},
    }),
    "read_terminal": (t_read_terminal, {
        "description": "Read a terminal's command history like a chat. since='last' returns only what you haven't seen.",
        "inputSchema": {"type": "object", "required": ["terminal_id"], "properties": {
            "terminal_id": {"type": "string"},
            "since": {"type": "string", "default": "last", "description": "'last' or a unix timestamp."},
            "mode": {"type": "string", "enum": ["commands", "errors", "full"], "default": "commands",
                     "description": "'errors' shows only non-zero exits, with output."},
        }},
    }),
    "search_terminal": (t_search_terminal, {
        "description": "Find a command or output substring across shared terminals.",
        "inputSchema": {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"},
            "terminal": {"type": "string", "default": "", "description": "Limit to one terminal id."},
        }},
    }),
    "run_terminal": (t_run_terminal, {
        "description": "Run a command in an editor terminal your user opted in to running commands. Refuses "
                       "terminals without that opt-in, without shell integration, or closed. Standalone terminals "
                       "(Terminal, iTerm2, etc.) aren't supported here.",
        "inputSchema": {"type": "object", "required": ["terminal_id", "command"], "properties": {
            "terminal_id": {"type": "string"},
            "command": {"type": "string"},
            "timeout_seconds": {"type": "integer", "default": 120},
        }},
    }),
    "spin_worker": (t_spin_worker, {
        "description": "Queue an autonomous worker (Claude Code, OpenCode, or Antigravity) for your user's approval. "
                       "Before calling, ask your user whether they want a budget for it. Nothing runs until they approve. "
                       "Costs and limits are checked against the agent that will actually run.",
        "inputSchema": {"type": "object", "required": ["role", "prompt"], "properties": {
            "role": {"type": "string", "description": "Brief 2-5 word job title for the worker (e.g. 'Unit Tester')."},
            "prompt": {"type": "string", "description": "Clear actionable instruction for the subagent."},
            "code": {"type": "array", "items": {"type": "string"}, "description": "Deterministic code packet items to include with prompt."},
            "attachments": {"type": "array", "items": {"type": "string"}, "description": "Absolute paths returned by /api/composer/upload."},
            "provider": {"type": "string", "default": "auto",
                         "description": "'auto' picks the agent from what the task needs. 'local' uses Ollama through OpenCode."},
            "model": {"type": "string", "description": "Claude: 'opus', 'sonnet', 'haiku'. Antigravity: e.g. 'gemini-4-argon-high', 'gemini-3.8-flash-medium', 'claude-sonnet-5-5-medium'. OpenCode: e.g. 'gemini-2.5-pro', 'claude-3-5-sonnet'. Copilot: 'auto', or model id. Local: e.g. 'llama3:8b'. Unknown ids are refused with the list of available models."},
            "private": {"type": "boolean", "default": False, "description": "The task must stay on this machine."},
            "swarm_id": {"type": "string", "description": "Set by spin_swarm. Leave empty."},
            "isolate": {"type": "boolean", "default": False, "description": "Work in a private git worktree, and return the change as a patch. Use it whenever workers share a repo."},
            "max_tokens": {"type": "integer", "description": "Stop the worker after this many tokens. Antigravity, OpenCode and DeepAI (pi) workers."},
            "max_seconds": {"type": "integer", "description": "Stop the worker after this many seconds. For providers that report no tokens, this is the cap."},
            "route_reason": {"type": "string", "description": "Why this worker got this provider, shown in the HUD."},
            "priority": {"type": "string", "enum": ["urgent", "normal", "low"], "default": "normal", "description": "Worker priority: urgent, normal, or low."},
            "track": {"type": "string", "default": "main", "description": "Shared context track name."},
            "project_dir": {"type": "string", "description": "Working directory (default: current workspace)."},
            "timeout_seconds": {"type": "integer", "default": DEFAULT_WORKER_TIMEOUT, "description": "Stop the worker after this long. 0 means no limit."},
            "effort": {"type": "string", "enum": ["auto", "low", "medium", "high", "max"], "default": "auto"},
            "mode": {"type": "string", "enum": ["standard", "god"], "default": "standard", "description": "'god' turns permission prompts off. Only with your user's explicit request."},
            "permission_mode": {"type": "string", "enum": ["ask", "auto_safe", "auto_approve"],
                                "description": "Omit to use this chat's effective mode: its own per-chat override if your user set one, else their universal default. A per-chat override always wins over the universal default. 'ask' sends every request to this chat and the HUD. 'auto_safe' auto-approves a read-only list. 'auto_approve' never asks: use it only if your user said so."},
            "capabilities": {"type": "array", "items": {"type": "string", "enum": sorted(VALID_CAPABILITIES)}, "description": "Default: files:read, files:write, terminal:safe."},
            "budget_session_pct": {"type": "number", "description": "Most of the 5-hour limit this worker may use, in points. Checked before it starts, and it's stopped when reached."},
            "budget_weekly_pct": {"type": "number", "description": "Most of the weekly limit this worker may use, in points. Checked before it starts, and it's stopped when reached."},
            "max_budget_usd": {"type": "number", "description": "Hard USD spend cap. Enforced by Claude and Copilot workers."},
            "thread": {"type": "string", "description": "Continue an existing chat of that agent, by @alias or ID, instead of starting fresh."},
            "then": {"type": "string", "description": "Optional JSON list of follow-up steps. Each step: role, prompt, provider, model, when, include, other_provider, max_tokens, verdict, capabilities, effort, then. The next worker starts when this one finishes."},
            "review": {"type": "string", "description": "Preset follow-up: '' (none), 'auto', or 'provider/model'. Appends a read-only Reviewer step that replies APPROVE or REJECT."},
            "wait": {"type": "boolean", "default": False, "description": "Block until the worker finishes and return its own final report. This is the native subagent shape: one call, one result. Leave false to spawn and move on."},
            "wait_timeout_seconds": {"type": "integer", "default": 0, "description": "How long to wait when wait=true. 0 uses the worker timeout."},
        }},
    }),
    "spin_batch": (t_spin_batch, {
        "description": "Queue a big job for a local model: files are split into chunks that fit in memory and processed one at a time, "
                       "saved as they finish so it can resume. Use it for repetitive work over many documents. Needs your user's approval.",
        "inputSchema": {"type": "object", "required": ["role", "prompt", "input_glob", "output_file"], "properties": {
            "role": {"type": "string", "description": "Short label for the job."},
            "prompt": {"type": "string", "description": "What to do with each chunk."},
            "input_glob": {"type": "string", "description": "Files to process, relative to project_dir, e.g. 'docs/**/*.txt'."},
            "output_file": {"type": "string", "description": "JSONL file for results. Re-running with the same file resumes."},
            "model": {"type": "string", "description": "Ollama model name. Omit to use the largest general model that fits."},
            "project_dir": {"type": "string"},
            "track": {"type": "string", "default": "main"},
            "timeout_seconds": {"type": "integer", "default": 0, "description": "0 means no limit."},
            "merge_prompt": {"type": "string", "default": "", "description": "Prompt to summarize or merge chunk results into one document once finished."},
        }},
    }),
    "spin_swarm": (t_spin_swarm, {
        "description": "Queue a group of workers for one task. In god mode, call it without subtasks to learn how many the task can use, "
                       "then call again with subtasks that don't overlap. Your user never has to pick a number.",
        "inputSchema": {"type": "object", "required": ["role", "prompt"], "properties": {
            "role": {"type": "string"}, "prompt": {"type": "string", "description": "The overall goal."},
            "provider": {"type": "string", "default": "auto"}, "track": {"type": "string", "default": "main"},
            "project_dir": {"type": "string"}, "mode": {"type": "string", "enum": ["standard", "god"], "default": "standard"},
            "subtasks": {"type": "array", "items": {"type": "string"}, "description": "One non-overlapping part per worker."},
            "max_tokens_each": {"type": "integer", "default": 0}, "permission_mode": {"type": "string", "enum": ["ask", "auto_safe", "auto_approve"]},
            "priority": {"type": "string", "enum": ["urgent", "normal", "low"], "default": "normal", "description": "Worker priority: urgent, normal, or low."},
        }},
    }),
    "run_concurrent": (t_run_concurrent, {
        "description": "Run multiple independent sub-tasks concurrently using isolated workers. Refuses dependent tasks (depends_on, after_worker, wait_for) or tasks with overlapping target files.",
        "inputSchema": {"type": "object", "required": ["tasks"], "properties": {
            "tasks": {
                "type": "array",
                "items": {"type": "object"},
                "description": "List of task dicts (each with prompt, role, files, provider, model, max_tokens, etc.)."
            },
            "track": {"type": "string", "default": "main", "description": "Shared context track name."},
        }},
    }),
    "stop_worker": (t_stop_worker, {
        "description": "Stop a running worker's process.",
        "inputSchema": {"type": "object", "required": ["worker_id"], "properties": {
            "worker_id": {"type": "string", "description": "Worker ID to stop."},
            "reason": {"type": "string", "description": "Why it's being stopped."},
        }},
    }),
    "instruct_worker": (t_instruct_worker, {
        "description": "Recall a running worker or stack instructions onto it.",
        "inputSchema": {"type": "object", "required": ["worker_id", "text"], "properties": {
            "worker_id": {"type": "string", "description": "Worker ID to instruct"},
            "text": {"type": "string", "description": "Instruction text"},
            "now": {"type": "boolean", "default": False, "description": "True to recall immediately, False to stack onto worker"},
            "attachments": {"type": "array", "items": {"type": "string"}, "description": "Absolute paths returned by /api/composer/upload."},
        }}
    }),
    "spin_iteration": (t_spin_iteration, {
        "description": "Give the same task to 2-4 participants (capable agents). Each works in its own isolated "
                       "worktree, then every participant reviews, critiques and scores the others plus itself. "
                       "They revise and keep improving round over round until someone clears the goal threshold, "
                       "a round limit, a stall, or a budget stops it. No auto-merge: all branches are kept and a "
                       "final report ranks participants. The first round's work needs your approval; later rounds "
                       "launch on their own.",
        "inputSchema": {"type": "object", "required": ["task", "participants"], "properties": {
            "task": {"type": "string", "description": "The task every participant attempts."},
            "participants": {"type": "array", "minItems": 2, "maxItems": 4,
                              "items": {"type": "object", "required": ["provider"], "properties": {
                                  "provider": {"type": "string"},
                                  "model": {"type": "string"},
                                  "effort": {"type": "string", "enum": ["auto", "low", "medium", "high", "max"]},
                                  "role": {"type": "string"},
                              }},
                              "description": "2-4 participants, each {provider, model?, effort?, role?}."},
            "threshold": {"type": "number", "default": 0.85, "description": "Self-score and peer-mean score both need to clear this (0-1, or 1-10 read as out of 10) to converge."},
            "max_rounds": {"type": "integer", "default": 4, "description": "Maximum rounds to run; 0 means no limit."},
            "stall_rounds": {"type": "integer", "default": 2, "description": "Rounds in a row with no gain in the best peer score before stopping; 0 never stops on a stall."},
            "round_seconds": {"type": "integer", "default": 600, "description": "Seconds budget per round (split across phases); 0 means no cap."},
            "round_tokens": {"type": "integer", "default": 350000, "description": "Token budget per round (split across phases); 0 means no cap."},
            "overseer": {"type": ["object", "string"], "description": "Optional overseer spun once on finish: {provider, model?, effort?} or JSON string."},
            "overseer_provider": {"type": "string", "description": "Flat form of overseer.provider, used when overseer is not given."},
            "overseer_model": {"type": "string", "description": "Flat form of overseer.model."},
            "overseer_effort": {"type": "string", "enum": ["auto", "low", "medium", "high", "max"], "description": "Flat form of overseer.effort."},
            "rubric": {"type": "string", "description": "Freeform scoring rubric shown to every participant."},
            "track": {"type": "string", "default": "main"},
            "budget_per_phase": {"type": "number", "default": 0, "description": "USD cap per participant per phase. 0 means unlimited."},
            "project_dir": {"type": "string", "description": "Working directory (default: current workspace)."},
        }},
    }),
    "stop_iteration": (t_stop_iteration, {
        "description": "Stop an ongoing iteration, halting its pending workers, updating status to stopped, and recording a final report without spinning an overseer.",
        "inputSchema": {"type": "object", "required": ["iteration_id"], "properties": {
            "iteration_id": {"type": "string", "description": "Iteration ID to stop."},
        }},
    }),
    "on_worker_done": (t_on_worker_done, {
        "description": "Attach follow-up worker steps to a running or queued worker, or fire them at once if it already finished.",
        "inputSchema": {"type": "object", "required": ["worker_id"], "properties": {
            "worker_id": {"type": "string", "description": "Worker ID to attach the steps to."},
            "then": {"type": "string", "description": "JSON list of follow-up steps, in the same form as spin_worker's then. Each step: role, prompt, provider, model, when, include, other_provider, max_tokens, verdict, capabilities, effort, then."},
        }},
    }),
    "permission_requests": (t_permission_requests, {
        "description": "List permission requests from spun workers that are waiting on your user.",
        "inputSchema": {"type": "object", "properties": {
            "worker_id": {"type": "string", "description": "Only this worker's requests."},
        }},
    }),
    "answer_permission": (t_answer_permission, {
        "description": "Pass on your user's answer to a worker's permission request. Ask them first: never decide for them.",
        "inputSchema": {"type": "object", "required": ["request_id", "decision"], "properties": {
            "request_id": {"type": "string"},
            "decision": {"type": "string", "enum": ["allow", "allow_always", "deny"]},
        }},
    }),
    "watch_workers": (t_watch_workers, {
        "description": "Wait up to wait_seconds for a worker's permission request or for a worker to finish.",
        "inputSchema": {"type": "object", "properties": {
            "wait_seconds": {"type": "integer", "default": 50},
        }},
    }),
    "quota_status": (t_quota_status, {
        "description": "Real session and weekly limits, token counts, and data freshness for each LLM client detected on this machine.",
        "inputSchema": {"type": "object", "properties": {"source": READ_SOURCE}},
    }),
    "set_quota": (t_set_quota, {
        "description": "Record limits by hand for a client that can't report them. Live readings always take priority.",
        "inputSchema": {"type": "object", "required": ["category"], "properties": {
            "category": {"type": "string", "enum": ["claude", "antigravity", "opencode", "kimi", "copilot"]},
            "weekly_left_pct": {"type": "number", "description": "Percentage remaining of weekly quota (0-100)."},
            "session_5h_left_pct": {"type": "number", "description": "Percentage remaining of 5-hour session quota (0-100)."},
            "daily_theos": {"type": "integer", "description": "DeepAI daily Theos allowance."},
        }},
    }),
    "approve_worker": (t_approve_worker, {
        "description": "Launch a pending worker with the approval code your user read from the Live HUD or their terminal. "
                       "Never guess a code or read it from files: if you don't have one, ask your user.",
        "inputSchema": {"type": "object", "required": ["worker_id", "approval_code"], "properties": {
            "worker_id": {"type": "string", "description": "Worker ID to approve."},
            "approval_code": {"type": "string", "description": "Six-character code from your user."},
        }},
    }),
    "reject_worker": (t_reject_worker, {
        "description": "Reject a pending worker, or stop it if it's already running.",
        "inputSchema": {"type": "object", "required": ["worker_id"], "properties": {
            "worker_id": {"type": "string", "description": "Worker ID to reject."},
            "reason": {"type": "string", "description": "Cancellation reason."},
        }},
    }),
    "await_worker": (t_await_worker, {
        "description": "Block until a worker finishes and return its own final report. This is the native subagent shape: spawn, then one call that returns the result, instead of waiting for a later wake.",
        "inputSchema": {"type": "object", "required": ["worker_id"], "properties": {
            "worker_id": {"type": "string", "description": "Worker ID, e.g. worker_ab12cd."},
            "timeout_seconds": {"type": "integer", "default": 600, "description": "Give up after this long and report the worker's last state."},
        }},
    }),
    "worker_status": (t_worker_status, {
        "description": "Check execution status, exit codes and log tails of workers, including archived ones.",
        "inputSchema": {"type": "object", "properties": {
            "worker_id": {"type": "string", "description": "Specific worker ID to inspect, or omit to list all."},
            "track": {"type": "string", "description": "Filter by shared context track name."},
            "all": {"type": "boolean", "default": False, "description": "Include all workers including archived."},
        }},
    }),
    "collect_reports": (t_collect_reports, {
        "description": "Collect completion reports and log tails from workers on a track.",
        "inputSchema": {"type": "object", "properties": {
            "track": {"type": "string", "default": "main", "description": "Track name or 'all'."},
            "wait": {"type": "boolean", "default": False, "description": "Wait up to 15s for running workers to finish."},
        }},
    }),
    "estimate_complexity": (t_estimate_complexity, {
        "description": "Recommend effort, tier, and provider for a task. Advisory only: it never changes permissions.",
        "inputSchema": {"type": "object", "required": ["prompt"], "properties": {
            "prompt": {"type": "string", "description": "Task description to evaluate."},
            "role": {"type": "string", "description": "Optional worker role."},
        }},
    }),
    "escalate_worker": (t_escalate_worker, {
        "description": "Stop a struggling worker and queue a stronger successor with its context, for your user's approval.",
        "inputSchema": {"type": "object", "required": ["worker_id"], "properties": {
            "worker_id": {"type": "string", "description": "Target worker ID to escalate."},
            "new_effort": {"type": "string", "enum": ["medium", "high", "max"], "default": "max"},
            "new_mode": {"type": "string", "enum": ["standard", "god"], "default": "god"},
            "reason": {"type": "string", "description": "Why escalation was triggered."},
            "successor_provider": {"type": "string", "enum": ["auto", "claude", "opencode", "antigravity"], "default": "auto"},
        }},
    }),
    "join_track": (t_join_track, {
        "description": "Subscribe this station to a shared multi-agent decision track and receive current track state.",
        "inputSchema": {"type": "object", "properties": {
            "track": {"type": "string", "default": "main", "description": "Track name (e.g. 'main', 'pr')."},
            "station_id": {"type": "string", "description": "Station ID or alias (default: self)."},
        }},
    }),
    "broadcast_track": (t_broadcast_track, {
        "description": "Publish a decision, milestone, or file modification to a shared multi-agent track. "
                       "Only post what is true: other agents treat the ledger as fact, so tests belong on their own track.",
        "inputSchema": {"type": "object", "required": ["message"], "properties": {
            "message": {"type": "string", "description": "Summary of decision or progress update."},
            "track": {"type": "string", "default": "main"},
            "files": {"type": "array", "items": {"type": "string"}, "description": "List of files touched or claimed."},
            "decisions": {"type": "array", "items": {"type": "string"}, "description": "Architectural rulings or commitments."},
            "station_id": {"type": "string", "description": "Author station ID or alias."},
            "session_id": {"type": "string", "description": "Session to attribute the entry to."},
        }},
    }),
    "read_track": (t_read_track, {
        "description": "Read latest synchronized decisions and status dispatches from a shared context track.",
        "inputSchema": {"type": "object", "properties": {
            "track": {"type": "string", "default": "main"},
            "limit": {"type": "integer", "default": 15},
            "since_timestamp": {"type": "number", "default": 0.0},
        }},
    }),
    "hud_status": (t_hud_status, {
        "description": "Snapshot departure-board view of active lines, file leases, worker swarms, and shared tracks.",
        "inputSchema": {"type": "object", "properties": {
            "project": {"type": "string", "description": "Optional project filter."},
        }},
    }),
    "start_hud": (t_start_hud, {
        "description": "Start the Live HUD dashboard. Ask your user first: it isn't started automatically.",
        "inputSchema": {"type": "object", "properties": {
            "port": {"type": "integer", "description": "Port to use (default 7890)."},
        }},
    }),
    "restart_hud": (t_restart_hud, {
        "description": "Stop any stale HUD/server process on the port, reload live modules, and start the latest Subway HUD server cleanly.",
        "inputSchema": {"type": "object", "properties": {
            "port": {"type": "integer", "description": "Port to use (default 7890)."},
        }},
    }),
    "run_server": (t_run_server, {
        "description": "Run or restart the latest Subway HUD server cleanly, reloading live modules.",
        "inputSchema": {"type": "object", "properties": {
            "port": {"type": "integer", "description": "Port to use (default 7890)."},
        }},
    }),
    "copilot_inline": (t_copilot_inline, {
        "description": "Request an inline code completion fill from Copilot (or fall back to worker's model). Only implementer workers may call this for small single-file fills.",
        "inputSchema": {"type": "object", "required": ["worker_id", "prompt"], "properties": {
            "worker_id": {"type": "string", "description": "Worker ID of the calling implementer."},
            "prompt": {"type": "string", "description": "Prompt or instruction describing the fill."},
            "prefix": {"type": "string", "description": "Code preceding the cursor/fill point."},
            "suffix": {"type": "string", "description": "Code following the cursor/fill point."},
            "filepath": {"type": "string", "description": "Target single file path."}
        }}
    }),
    "routing_weights": (t_routing_weights, {
        "description": "Inspect learned adaptive routing weights, run counts, and explanations.",
        "inputSchema": {"type": "object", "properties": {
            "role": {"type": "string", "description": "Filter by target role or role category.", "default": ""},
            "complexity": {"type": "string", "description": "Filter by task complexity tier (e.g. low, medium, high, frontier).", "default": ""},
        }}
    }),
}


_REPEAT_GUARD_LOCK = threading.Lock()
_REPEAT_GUARD: Dict[str, List[Tuple[float, str]]] = {}
# Tools that can block for minutes. The read loop dispatches these off-thread, so the
# elicitation reply a waiting worker needs still gets read (cli_tools.main).
SLOW_TOOLS = {"spin_worker", "collect_reports", "await_worker"}

_REPEAT_GUARD_TOOLS = {
    "hud_status", "worker_status", "list_chats", "list_terminals",
    "quota_status", "collect_reports", "read_chat", "search_chat",
}
_REPEAT_GUARD_WINDOW = 120.0
_REPEAT_GUARD_LIMIT = 3
_REPEAT_GUARD_KEEP = 5


def _repeat_guard_hash(args: Dict[str, Any]) -> str:
    try:
        raw = json.dumps(args, sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        raw = repr(args)
    return hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()


def _repeat_guard_check(name: str, args: Dict[str, Any]) -> Optional[str]:
    """Tool-dispatch guard: refuse identical expensive repeat calls within the window."""
    now = time.time()
    ah = _repeat_guard_hash(args)
    with _REPEAT_GUARD_LOCK:
        hist = _REPEAT_GUARD.get(name) or []
        if not hist or hist[-1][1] != ah:
            hist = [(now, ah)]
        else:
            hist = [e for e in hist if now - e[0] <= _REPEAT_GUARD_WINDOW and e[1] == ah]
            hist.append((now, ah))
        hist = hist[-_REPEAT_GUARD_KEEP:]
        _REPEAT_GUARD[name] = hist
        n = len(hist)
        if n >= _REPEAT_GUARD_LIMIT:
            return (
                f"[REPEAT GUARD] You have called '{name}' {n} times in the last 2 minutes "
                "with identical arguments. The result will not change. If you are stuck in "
                "a call loop: route your intended action through a different tool "
                "(e.g. codemode), or use the cached output you already have. "
                "State what decision this call was supposed to inform."
            )
    return None


def _repeat_guard_wrap(name: str, fn: Callable[..., str]) -> Callable[..., str]:
    def wrapped(**args: Any) -> str:
        refusal = _repeat_guard_check(name, args)
        if refusal is not None:
            return refusal
        return fn(**args)

    wrapped.__name__ = getattr(fn, "__name__", name)
    wrapped.__doc__ = getattr(fn, "__doc__", None)
    setattr(wrapped, "__wrapped__", fn)  # preserve signature for schema/signature tests
    setattr(wrapped, "_repeat_guard_tool", name)
    return wrapped


for _rg_name in list(_REPEAT_GUARD_TOOLS):
    if _rg_name in TOOLS:
        _entry = TOOLS[_rg_name]
        TOOLS[_rg_name] = (_repeat_guard_wrap(_rg_name, _entry[0]), _entry[1])  # type: ignore[assignment]


# ---- MCP stdio JSON-RPC loop + __main__ (stays, 7/7) ----

_send_lock = threading.Lock()
_client: Dict[str, Any] = {}
_asked: Set[str] = set()
_closed: Set[str] = set()


from cli_tools import (send, _note_client, _elicitation_step, _elicitation_loop)


from permissions import _handle_elicitation_reply
    # Anything else stays pending. Some clients decline these prompts by themselves, with nobody having seen them.


from workers import (pending_worker_pings, _worker_ping_loop)


from hud_server import _maybe_open_hud_on_use


from cli_tools import handle as _base_handle
import cli_tools as _cli_tools


# --- first-run onboarding: MCP initialize carries a compact capability playbook ---
_SUBWAY_ONBOARDING_INSTRUCTIONS = (
    "Subway: multi-agent transit. Board other chats on demand only. "
    "Token rules: 1) prefer mode=\"decisions\" first; 2) use since=\"last\" for ongoing loops; 3) use quote=/anchor_text to target a topic. "
    "Coordination: claim_file BEFORE editing; escalate_worker when a worker struggles; on_worker_done + then= for dependent chains; "
    "instruct_worker to steer mid-run; review=\"auto\" for built-in review; quota_status before big spends; estimate_complexity for routing advice. "
    "Concurrency: use run_concurrent(tasks=[...]) when a request holds multiple independent sub-tasks or separate files to fan out parallel isolated workers; do not use it for dependent tasks. "
    "Call subway with tool='list' for the full catalog."
    " Principals: lead with the answer, at most 8 lines unless asked, no recaps; think as long as needed."
)


def handle(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if isinstance(req, dict) and req.get("method") == "tools/call":
        try:
            is_worker = bool(os.environ.get("SUBWAY_WORKER") == "1" or os.environ.get("SUBWAY_WORKER_ID"))
            _self = getattr(sys.modules.get(__name__), "_self_id", _self_id)
            station = _self() if callable(_self) else ""
            if station and ("worker_" in station or station.startswith("worker-")):
                is_worker = True
            if not is_worker:
                import governor
                params = req.get("params") or {}
                tool_name = params.get("name") or ""
                args = params.get("arguments") or {}
                provider = "mcp"
                if station:
                    for p in ("claude", "antigravity", "opencode", "pi"):
                        if station.startswith(p):
                            provider = p
                            break
                r = governor.check(provider, station, tool_name, args)
                if isinstance(r, dict) and not r.get("allow", True):
                    return {
                        "jsonrpc": "2.0",
                        "id": req.get("id"),
                        "result": {
                            "content": [{"type": "text", "text": r.get("reason", "Governor limit reached.")}],
                            "isError": False,
                        },
                    }
        except Exception:
            pass
    res = _base_handle(req)
    if isinstance(req, dict) and req.get("method") == "initialize" and isinstance(res, dict):
        result = res.get("result")
        if isinstance(result, dict):
            suffix = ""
            try:
                params = req.get("params") or {}
                cname = str((params.get("clientInfo") or {}).get("name") or "").lower()
                if "claude" in cname:
                    p = "claude"
                elif "antigravity" in cname or "gemini" in cname:
                    p = "antigravity"
                elif "opencode" in cname:
                    p = "opencode"
                elif "pi" in cname:
                    p = "pi"
                else:
                    p = "mcp"
                import reply_styles
                suffix = " " + reply_styles.text_for(p)
            except Exception:
                suffix = ""
            result["instructions"] = _SUBWAY_ONBOARDING_INSTRUCTIONS + suffix
    if isinstance(req, dict) and req.get("method") == "tools/call" and isinstance(res, dict):
        result = res.get("result")
        if isinstance(result, dict):
            notice = ""
            try:
                _self = getattr(sys.modules.get(__name__), "_self_id", _self_id)
                pending = _pending_permissions(station=_self())
                notice = permission_text(pending) if pending else ""
            except Exception:
                notice = ""
            if notice:
                content = result.get("content")
                if isinstance(content, list) and content:
                    first = content[0]
                    if isinstance(first, dict) and first.get("type") == "text":
                        text = first.get("text") or ""
                        first["text"] = notice + ("\n\n" if text else "") + text
    return res


_cli_tools.handle = handle


USAGE = """Subway v{version}

  subway                     Run as an MCP server over stdio
  subway hud [port] [--open] Start the Live HUD on {host}
  subway workers             List workers, with approval codes for pending ones
  subway approve <worker_id> Launch a pending worker
  subway reject <worker_id> [reason]
  subway stop <worker_id>    Stop a running worker
  subway permissions         List worker permission requests
  subway watch [--stream]    Wait for, or stream, permission requests and finished workers
  subway mode [ask|auto_safe|auto_approve]  Show or set how workers get permissions
  subway allow|always|deny <request_id>
  subway tail <worker_id>    Show a worker's status and log
  subway locks | track [name] | quota [source]
  subway pair [list]           Devices asking to connect; pair approve <id> | pair deny <id> | pair revoke <device_id>
  subway wake                  Whether this Mac can be woken, and how to enable it; wake queue to see what waits
  subway notify test           Send a test desktop notification
  subway install | status | uninstall
  subway <tool> '<json args>' Call any MCP tool directly"""


from permissions import run_hook_guard

from usage import hook_prompt

from cli_tools import (_track_for_session, hook_precompact, run_hook_precompact)


from usage import run_hook_prompt


from cli_tools import (run_cli, main)


if __name__ == "__main__":
    main()
