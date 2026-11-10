"""Token usage, quotas, budgets, compaction and usage-limit tools.

Moved from server.py (Subway 1.3.5). Rule: server-level names are read late through `_s.NAME` so tests that patch `server.X` still apply. Import this module through server, not directly."""

import sys
sys.modules.setdefault("server", sys.modules[__name__])  # one module object when run as a script
import server as _s

from .claude import (_newest_claude_transcript_tokens, _parse_usage_text, _fetch_claude_live, _read_claude_json, _fetch_claude_cached, _window_totals, _scan_ledger, _parse_claude_usage, _fetch_claude_tokens)
from .antigravity import (_parse_ts, _fetch_agy_limits, _parse_agy_usage, _fetch_agy_tokens)
from .opencode import (_opencode_free_exhausted_models, _is_opencode_model_exhausted, _opencode_session_tokens, _fetch_opencode_usage, get_exhausted_models)
from .copilot import (_copilot_tokens_for_session, _copilot_fresh_input, _normalise_copilot_worker_record, _copilot_cli_usage)
from .kimi import (_kimi_token, _KimiUsageError, _kimi_raw)
from .local import (chat_tokens_used, _provider_5h_tokens, _run_source_task, _fallback_source_record, _refresh_source, _source, _sampler_loop, load_quotas)
from .shared import (_parse_reset_seconds, _add_usage, _check_cap, _resume_reuses_cache, _quota_error_event_text, check_quota_error, _parse_reset_text, _select_pool, low_limit_notice, _fmt_reset)
from .compaction import (get_compaction, compact_at_tokens, _claude_settings_file, _sync_claude_autocompact, _pi_settings_file, _sync_pi_autocompact, _pi_chat_context_usage, set_compaction, chat_context_usage, compaction_threshold_pct, compaction_threshold, is_compaction_quiet, compact_focus_line, compact_opencode_workers, compaction_notice, request_chat_compaction, pending_compaction_requests, clear_compaction_requests, _parse_ctx_size)
from .budget import (_chat_budget_cfg, chat_budget_for, chat_budget_rows, set_budget, set_chat_budget, budget_state, _cached_provider_5h_tokens, budget_rows, budget_ping_notice, _provider_budget_reset, enforce_budgets, check_budget_preflight)
from .tools import (hook_prompt, run_hook_prompt)

__all__ = ['_resume_reuses_cache', '_copilot_tokens_for_session', '_opencode_free_exhausted_models', '_is_opencode_model_exhausted', '_parse_reset_seconds', 'chat_tokens_used', '_provider_5h_tokens', 'get_compaction', 'compact_at_tokens', '_claude_settings_file', '_sync_claude_autocompact', '_pi_settings_file', '_sync_pi_autocompact', '_pi_chat_context_usage', 'set_compaction', 'chat_context_usage', 'compaction_threshold_pct', 'compaction_threshold', 'is_compaction_quiet', 'compact_focus_line', 'compact_opencode_workers', 'compaction_notice', '_chat_budget_cfg', 'chat_budget_for', 'chat_budget_rows', 'set_budget', 'set_chat_budget', 'budget_state', '_cached_provider_5h_tokens', '_newest_claude_transcript_tokens', 'budget_rows', 'budget_ping_notice', '_provider_budget_reset', 'enforce_budgets', '_add_usage', '_copilot_fresh_input', '_normalise_copilot_worker_record', '_check_cap', '_quota_error_event_text', 'check_quota_error', '_opencode_session_tokens', '_parse_ts', '_fetch_agy_limits', '_parse_reset_text', '_parse_usage_text', '_fetch_claude_live', '_read_claude_json', '_fetch_claude_cached', '_window_totals', '_scan_ledger', '_parse_claude_usage', '_fetch_claude_tokens', '_parse_agy_usage', '_fetch_agy_tokens', '_fetch_opencode_usage', '_run_source_task', '_fallback_source_record', '_refresh_source', '_source', '_sampler_loop', '_copilot_cli_usage', '_kimi_token', '_KimiUsageError', '_kimi_raw', 'load_quotas', '_select_pool', 'low_limit_notice', '_fmt_reset', 'check_budget_preflight', 'get_exhausted_models', 'hook_prompt', 'run_hook_prompt', '_pi_usage', 'pi_theos', 'token_history']
from .pi import _pi_usage, _theos as pi_theos
from .history import token_history
