"""One schema of every Subway setting: key, type, default, scope (global | thread) and group.

Data only. It imports nothing from Subway. `managed` means hud_server/settings_api.py validates and
writes the key; unmanaged keys are listed so the schema is complete, but still have their own handler
(side effects, nested config, thread state) in hud_server/handlers.py.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

GROUPS = ("workers", "tokens", "git", "storage", "notifications", "startup", "chats", "hud", "mail", "delegation", "sandbox")
TYPES = ("bool", "int", "enum", "str", "multi")
SCOPES = ("global", "thread")


def _e(key: str, type: str, default: Any, group: str, label: str, scope: str = "global", managed: bool = False,
       **extra: Any) -> Dict[str, Any]:
    spec: Dict[str, Any] = {"key": key, "type": type, "default": default, "scope": scope, "group": group,
                            "label": label, "managed": managed}
    spec.update(extra)
    return spec


SCHEMA: List[Dict[str, Any]] = [
    # workers
    _e("god_mode", "bool", False, "workers", "God mode", managed=True),
    _e("launch_approval_local", "enum", "ask", "workers", "Approval before launching workers on this station",
       managed=True, choices=["ask", "auto", "never"], help="ask: every worker waits for your approval. auto: launches at once when the worker is isolated, has token, time and tool-call caps, and isn't a top-tier model (Opus/Fable); otherwise it asks. never: every worker launches at once. God mode always asks."),
    _e("launch_approval_remote", "enum", "link", "workers", "Approval before launching workers from linked stations",
       managed=True, choices=["link", "ask"], help="link: the link's spawn grant decides. ask: every worker from a linked station waits for your approval here."),
    _e("worker_budget_mode", "enum", "auto", "workers", "Token cap per worker", managed=True,
       choices=["none", "auto", "range"],
       help="none: workers run with no token cap, even if the principal sets one. auto: the principal picks a cap per task (default). range: every worker's token cap is clamped between the two numbers below; a worker spun with no cap gets the upper one."),
    _e("worker_budget_min", "int", 200000, "workers", "Token cap: at least (range)", managed=True, min=10000, max=10000000),
    _e("worker_budget_max", "int", 1000000, "workers", "Token cap: at most (range)", managed=True, min=10000, max=10000000),
    _e("require_approval", "bool", False, "workers", "Require device approval codes", managed=True,
       help="When on, a linked station must exchange an approval code before it may use any non-pairing route."),
    _e("link_default_preset", "enum", "personal", "workers", "Permissions for newly linked stations",
       managed=True, choices=["personal", "collaborator", "observer"], help="The grant preset a station gets when it first links. Change each one later on its People card."),
    _e("link_default_allowance", "int", None, "workers", "Daily token allowance for newly linked stations", nullable=True, min=0, max=100000000),
    _e("worker_limits", "bool", True, "workers", "Worker limits", managed=True),
    _e("max_workers", "int", None, "workers", "Max workers", nullable=True, min=1, max=128),
    _e("no_edit_tool_calls", "int", 12, "workers", "Hard stop after calls without an edit (0 = off)", managed=True, min=0, max=50),
    _e("doctor_mode", "enum", "auto", "workers", "Failure doctor", managed=True, choices=["off", "on", "auto"]),
    _e("doctor_budget_mode", "enum", "dynamic", "workers", "Doctor budget", managed=True, choices=["fixed", "dynamic"]),
    _e("doctor_budget_tokens", "int", 150000, "workers", "Doctor max tokens (fixed budget)", managed=True, min=20000, max=2000000),
    _e("doctor_model", "str", "", "workers", "Doctor model (empty = pick a cheap one)", managed=True),
    _e("private_routing", "enum", "auto", "workers", "Privacy routing", choices=["local_only", "auto", "off"]),
    _e("code_packets_mode", "enum", "auto", "workers", "Code packets", managed=True, choices=["off", "warn", "require", "auto"]),
    _e("units_budget", "enum", "pool", "workers", "Unit call budget (experimental)", managed=True, choices=["pool", "per_unit"]),
    _e("packet_range_max_lines", "int", 120, "workers", "Range packets: widen to the whole function up to (lines) (experimental)", managed=True, min=20, max=2000),
    _e("skeleton_min_lines", "int", 400, "workers", "Skeleton packets from (lines) (experimental)", managed=True, min=50, max=5000),
    _e("peer_claim_misses", "int", 6, "workers", "Release a linked station's claims after missed checks (10s each)", managed=True, min=2, max=60),
    _e("whole_file_max_lines", "int", 400, "workers", "Whole-file packets up to (lines) (experimental)", managed=True, min=50, max=5000),
    _e("cap_cache_weight_pct", "int", 25, "workers", "Cache-read tokens counted toward the cap (percent) (experimental)", managed=True, min=0, max=100),
    _e("local_memory_guard", "bool", True, "workers", "Local memory guard"),
    _e("local_concurrency_limits", "bool", True, "workers", "Local concurrency limits"),
    _e("max_local_workers", "int", None, "workers", "Max local workers", nullable=True, min=1, max=32),
    _e("hub_concurrency_limits", "bool", True, "workers", "Hub concurrency limits"),
    _e("hub_max_local", "int", None, "workers", "Hub max local", nullable=True, min=1, max=32),
    _e("hub_max_remote", "int", None, "workers", "Hub max remote", nullable=True, min=1, max=64),
    _e("acp_pump_enabled", "bool", True, "workers", "Persistent ACP pump", managed=True),
    _e("acp_pump_idle_seconds", "int", 300, "workers", "ACP pump idle timeout (seconds)", managed=True, min=30, max=3600),
    # tokens
    _e("nudge_builder_calls", "int", 5, "tokens", "Builder nudge", managed=True, min=2, max=50),
    _e("nudge_other_calls", "int", 7, "tokens", "Other workers nudge", managed=True, min=2, max=50,
       aliases=["nudge_default_calls"]),
    _e("projector_enabled", "bool", True, "tokens", "Test output projector", managed=True),
    _e("projector_max_lines", "int", 40, "tokens", "Projector failure lines", managed=True, min=5, max=400),
    _e("table_rows", "int", 20, "tokens", "Rows per table", managed=True, min=5, max=200),
    _e("thoughts_mode", "enum", "include", "tokens", "Thoughts in chat reads", managed=True, choices=["include", "exclude"]),
    _e("chat_budget", "int", 0, "tokens", "Chat token budget", scope="thread", min=0, max=100000000),
    _e("compaction", "enum", "suggest", "tokens", "Compaction", scope="thread", choices=["suggest", "auto", "never"]),
    _e("compact_at_tokens", "int", 300000, "tokens", "Compact at", scope="thread", min=1000, max=10000000),
    _e("read_budget_warn_pct", "int", 80, "tokens", "Read budget soft warning (%)", managed=True, min=50, max=100),
    # git
    _e("git_branch_mode", "bool", False, "git", "Branch track mode", managed=True),
    _e("chat_branch_auto", "bool", False, "git", "Require a chat branch when a second chat edits the same tree", managed=True),
    _e("land_mode", "enum", "merge", "git", "Land mode", choices=["merge", "preserve"]),
    _e("lanes_max", "int", 0, "git", "Lane limit (0 = recommended)", min=0, max=64),
    _e("lanes_unlocked", "bool", False, "git", "Allow lanes past 2× recommended"),
    _e("lanes_default", "str", "", "git", "Default lane"),
    _e("lanes_forward_port", "bool", True, "git", "Forward-port ship fixes to dev", managed=True),
    _e("ship_auto_advance", "bool", False, "git", "Auto-advance ship when green", managed=True,
       help="Advance the next ship step automatically when a step passes. Release and Mirror always need a click."),
    # storage
    _e("worktree_cleanup", "enum", "off", "storage", "Worktree cleanup", managed=True, choices=["off", "on"]),
    _e("worker_log_days", "int", 14, "storage", "Compress worker logs after days (0 = never)", managed=True, min=0, max=365),
    # notifications / startup
    _e("caffeinate", "enum", "off", "notifications", "Keep awake", choices=["off", "workers", "always"]),
    _e("hud_autostart", "bool", True, "startup", "Start the HUD automatically", managed=True),
    _e("hud_open_on_use", "bool", True, "startup", "Open the HUD on first use", managed=True),
    _e("hud_open_in", "enum", "auto", "startup", "Open the HUD in",
       choices=["auto", "browser", "app", "editor", "antigravity", "vscode", "cursor", "windsurf"]),
    _e("hud_wallpaper", "str", "default", "startup", "Wallpaper", managed=True),
    _e("mcp_relay", "bool", False, "startup", "Chats connect through a ~15 MB relay to the HUD instead of loading all of Subway each", managed=True),
    _e("deepai_tunnel", "enum", "off", "startup", "DeepAI tunnel (carrier-restricted networks)", managed=True,
       choices=["off", "on"],
       help="on: pin api.deepai.org to a local IPv4 forwarder for networks where direct TLS to it fails (carrier-dependent IPv6/Happy Eyeballs). Pure TCP pipe to the same AWS servers, zero TLS termination — billing and requests are byte-identical to direct; only reachability changes. Needs one sudo command to arm (port 443 + /etc/hosts), shown when you turn it on."),
    _e("transplant_turns", "int", 12, "startup", "Exchanges carried when a conversation moves provider", managed=True, min=5, max=20),
    _e("transplant_full", "bool", False, "startup", "Carry the full conversation when it moves provider", managed=True),
    # chats (thread scope)
    _e("permission_mode", "str", "", "chats", "Worker permission mode", scope="thread"),
    _e("terminal_mode", "str", "", "chats", "Terminal command mode", scope="thread"),
    _e("upload_max_mb", "int", 10, "chats", "Largest file you can attach (MB)", min=1, max=100),
    _e("apps_headless_mode", "enum", "connected_only", "chats", "Headless app actions", managed=True,
       choices=["off", "connected_only", "on"],
       help="off: block app actions in headless turns. connected_only: allow Subway app_action MCP tool for connected apps. on: allow for any discovered app."),
    _e("headless_deepai", "bool", False, "chats", "DeepAI models in HUD-started chats", managed=True,
       help="off (default): chats the HUD starts itself cannot use DeepAI-app-managed models — they fail with the reason (the bare pi CLI has no deepai provider). on: HUD-started pi chats may use DeepAI models through the app's own local proxy, billed exactly like in-app chats; fails loudly when the app (and its proxy) is closed. Billing semantics are the app's; this toggle only allows using the same path."),
    _e("mail_slow_check_seconds", "int", 30, "chats", "Re-check for file-change and chat-idle triggers while one is waiting (seconds)", min=5, max=600),
    # delegation (multi = a non-empty list drawn from choices)
    _e("delegation.shapes", "multi", ["tree", "mesh", "pipeline", "adaptive"], "delegation", "Allowed delegation shapes",
       managed=True, choices=["tree", "mesh", "pipeline", "adaptive"]),
    _e("iteration.threshold_pct", "int", 85, "delegation", "Goal threshold (percent)", managed=True, min=1, max=100),
    _e("iteration.max_rounds", "int", 4, "delegation", "Max rounds (0 = no limit)", managed=True, min=0, max=20),
    _e("iteration.stall_rounds", "int", 2, "delegation", "Stop after rounds without gain (0 = never)", managed=True, min=0, max=10),
    _e("iteration.round_seconds", "int", 600, "delegation", "Seconds per round (0 = no cap)", managed=True, min=0, max=7200),
    _e("iteration.round_tokens", "int", 350000, "delegation", "Tokens per round (0 = no cap)", managed=True, min=0, max=5000000),
    # mail
    _e("mail.mode", "enum", "fast", "mail", "Delivery mode for direct notes", managed=True, choices=["normal", "fast"]),
    _e("mail.wake_offline", "enum", "ask", "mail", "Offline behaviour", managed=True, choices=["off", "ask", "on"]),
    _e("mail.offline_budget_tokens", "int", 20000, "mail", "Hard cap for a headless resume (tokens)", managed=True, min=1000, max=2000000),
    _e("mail.offline_budget_seconds", "int", 120, "mail", "Wall-clock cap for a headless resume (seconds)", managed=True, min=5, max=3600),
    _e("mail.broadcast_wait_seconds", "int", 20, "mail", "Default collection window for ask_room (seconds)", managed=True, min=1, max=600),
    _e("mail.broadcast_max_targets", "int", 12, "mail", "Broadcast fan-out cap", managed=True, min=1, max=100),
    _e("mail.max_hops", "int", 3, "mail", "Reply loop cap", managed=True, min=1, max=20),
    _e("mail.rate_limit_per_hour", "int", 20, "mail", "Per-sender rate limit (messages per hour)", managed=True, min=1, max=10000),
    _e("mail.mute", "bool", False, "mail", "Mute this thread's mailbox (queue only)", scope="thread", managed=False),
    _e("mail.muted_senders", "str", "", "mail", "Comma-separated station ids that are never woken", managed=True),
    # sandbox
    _e("sandbox.exec_default", "enum", "os", "sandbox", "Default execution sandbox", managed=True, choices=["off", "worktree", "os", "vm"]),
    _e("sandbox.net_default", "enum", "off", "sandbox", "Default sandbox network policy", managed=True, choices=["off", "allowlist", "open"]),
    _e("sandbox.allow_domains", "str", "pypi.org,registry.npmjs.org,api.github.com", "sandbox", "Comma-separated domains allowed in allowlist mode", managed=True),
    _e("sandbox.unsandboxed_fallback", "bool", False, "sandbox", "Allow permission-prompted fallback outside sandbox", managed=True),
    _e("sandbox.state_mode", "enum", "live", "sandbox", "Thread state mode (live or dry_run playground)", scope="thread", choices=["live", "dry_run"]),
]

_BY_KEY: Dict[str, Dict[str, Any]] = {s["key"]: s for s in SCHEMA}
_BY_NAME: Dict[str, Dict[str, Any]] = dict(_BY_KEY)
for _spec in SCHEMA:
    for _alias in _spec.get("aliases", []):
        _BY_NAME[_alias] = _spec


def get(key: str) -> Optional[Dict[str, Any]]:
    """The spec for a canonical key, else None."""
    return _BY_KEY.get(str(key))


def resolve(name: str) -> Optional[Dict[str, Any]]:
    """The spec for a canonical key or one of its legacy aliases, else None."""
    return _BY_NAME.get(str(name))


def keys(scope: str = "", group: str = "") -> List[str]:
    return [s["key"] for s in SCHEMA if (not scope or s["scope"] == scope) and (not group or s["group"] == group)]
