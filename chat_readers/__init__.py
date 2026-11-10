"""Chat readers for Claude, Antigravity, OpenCode and Copilot, plus shared listing, search and decisions.

Moved from server.py (Subway 1.3.5). Rule: server-level names are read late through `_s.NAME` so tests that patch `server.X` still apply. Import this module through server, not directly."""

from __future__ import annotations

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
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple, Union

import terminals
import native_terminals
import threads

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

import server as _s

from .common import (
    _opencode_timestamp,
    extract_files_from_text,
    slugify_topic,
    _read_tail,
    _read_head,
    SegmentInfo,
    chat_segments_info,
    chat_turns,
)

from .render import (
    to_exchanges,
    render_exchange,
    render_decisions,
    render_chat,
)

from .aliases import (
    load_aliases,
    _sync_opencode_agent_stubs,
    save_alias,
    _checkpoint_key,
    get_checkpoints,
    record_checkpoint,
)

from .claude import (
    claude_open_sessions,
    _claude_is_human,
    claude_parse_lines,
    claude_files,
    claude_meta,
    _claude_read_full,
    claude_read,
    claude_read_segments,
)

from .antigravity import (
    agy_read_segments,
    _agy_db,
    _agy_transcript_path,
    _workspace_name,
    _workspace_paths,
    agy_list,
    _strip_user_request,
    agy_read,
    _agy_hubs,
    _agy_rpc,
)

from .opencode import (
    opencode_read_segments,
    _opencode_db,
    opencode_list,
    opencode_read,
    _opencode_api,
)

from .copilot import (
    _copilot_session_store,
    _copilot_worker_index,
    _copilot_open_sessions,
    _copilot_current_session,
    _copilot_int,
    _copilot_usage_checkpoint,
    _copilot_session_usage,
    _copilot_cli_meta,
    _copilot_agent_host_ids,
    copilot_list,
    _redact_secrets,
    _last_tool_line,
    copilot_read_segments,
    parse_copilot_jsonl,
    _copilot_raw,
)

from .vscode import (
    _vscode_user_dirs,
    _vscode_workspace_project,
    _vscode_chat_files,
    _vscode_patch_path,
    _vscode_set_path,
    _vscode_chat_state,
    _vscode_response_text,
    _vscode_request_model,
    _vscode_chat_data,
    _vscode_transcript_path,
    _timestamp_seconds,
    _vscode_transcript_latest_timestamp,
    _vscode_chat_meta,
)

from .pi import (
    _pi_sessions_root,
    pi_files,
    _pi_entries,
    _pi_content_text,
    _pi_turns_from_entries,
    pi_turns,
    pi_read,
    pi_read_segments,
    pi_meta,
    pi_list,
)

from .cursor import (
    _cursor_dir,
    _cursor_db,
    _cursor_vscdb,
    cursor_list,
    cursor_read,
    cursor_read_segments,
)

from .devin import (
    _devin_dir,
    devin_list,
    devin_read,
    devin_read_segments,
)

from .dispatch import (
    all_chats,
    _project_names,
    _newest,
    resolve,
    _detect_self_uncached,
    detect_self,
    _self_id,
    _short_id,
    _detected_chat,
    _id_matches,
    _get_last_entry_preview,
    _file_may_contain,
    get_chat_context,
)

__all__ = [
    '_opencode_timestamp',
    'extract_files_from_text',
    'slugify_topic',
    '_read_tail',
    '_read_head',
    'SegmentInfo',
    'chat_segments_info',
    'chat_turns',
    'to_exchanges',
    'render_exchange',
    'render_decisions',
    'render_chat',
    'load_aliases',
    '_sync_opencode_agent_stubs',
    'save_alias',
    '_checkpoint_key',
    'get_checkpoints',
    'record_checkpoint',
    'claude_open_sessions',
    '_claude_is_human',
    'claude_parse_lines',
    'claude_files',
    'claude_meta',
    '_claude_read_full',
    'claude_read',
    'claude_read_segments',
    'agy_read_segments',
    '_agy_db',
    '_agy_transcript_path',
    '_workspace_name',
    '_workspace_paths',
    'agy_list',
    '_strip_user_request',
    'agy_read',
    '_agy_hubs',
    '_agy_rpc',
    'opencode_read_segments',
    '_opencode_db',
    'opencode_list',
    'opencode_read',
    '_opencode_api',
    '_copilot_session_store',
    '_copilot_worker_index',
    '_copilot_open_sessions',
    '_copilot_current_session',
    '_copilot_int',
    '_copilot_usage_checkpoint',
    '_copilot_session_usage',
    '_copilot_cli_meta',
    '_copilot_agent_host_ids',
    'copilot_list',
    '_redact_secrets',
    '_last_tool_line',
    'copilot_read_segments',
    'parse_copilot_jsonl',
    '_copilot_raw',
    '_vscode_user_dirs',
    '_vscode_workspace_project',
    '_vscode_chat_files',
    '_vscode_patch_path',
    '_vscode_set_path',
    '_vscode_chat_state',
    '_vscode_response_text',
    '_vscode_request_model',
    '_vscode_chat_data',
    '_vscode_transcript_path',
    '_timestamp_seconds',
    '_vscode_transcript_latest_timestamp',
    '_vscode_chat_meta',
    '_pi_sessions_root',
    'pi_files',
    '_pi_entries',
    '_pi_content_text',
    '_pi_turns_from_entries',
    'pi_turns',
    'pi_read',
    'pi_read_segments',
    'pi_meta',
    'pi_list',
    '_cursor_dir',
    '_cursor_db',
    '_cursor_vscdb',
    'cursor_list',
    'cursor_read',
    'cursor_read_segments',
    '_devin_dir',
    'devin_list',
    'devin_read',
    'devin_read_segments',
    'all_chats',
    '_project_names',
    '_newest',
    'resolve',
    '_detect_self_uncached',
    'detect_self',
    '_self_id',
    '_short_id',
    '_detected_chat',
    '_id_matches',
    '_get_last_entry_preview',
    '_file_may_contain',
    'get_chat_context',
]
