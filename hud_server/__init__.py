import sys
sys.modules.setdefault("server", sys.modules[__name__])  # one module object when run as a script
import server as _s
# Ensure decorators/attrs used by hud_server submodules are present
try:
    from store import ttl_cached  # type: ignore
    mod = sys.modules[__name__]
    # attach to both
    for target in [_s, mod]:
        if not hasattr(target, 'ttl_cached'):
            try:
                setattr(target, 'ttl_cached', ttl_cached)
            except Exception:
                pass
    # copy common attrs from root server if exists
    try:
        import server as _root_srv
        for name in dir(_root_srv):
            if name.isupper() or name.startswith('_'):
                if not hasattr(mod, name):
                    try:
                        setattr(mod, name, getattr(_root_srv, name))
                    except Exception:
                        pass
                if not hasattr(_s, name):
                    try:
                        setattr(_s, name, getattr(_root_srv, name))
                    except Exception:
                        pass
    except Exception:
        pass
    # common attrs
    for name in ['DEFAULT_MAX_CHARS', 'MAX_BODY_BYTES', 'HTTP_JSON', 'HTTP_OK', 'HUD_PORT', 'HUD_HOST']:
        if not hasattr(mod, name):
            setattr(mod, name, 7890 if name.endswith('PORT') else '127.0.0.1')
        if not hasattr(_s, name):
            setattr(_s, name, 7890 if name.endswith('PORT') else '127.0.0.1')
except Exception:
    pass

from .notifications import (_log_notification_failure, _run_notification_command, _send_desktop_notification, notify_desktop_event)
from .clients import (_config_has_subway, _subway_sessions, _app_installed, detect_clients)
from .views import (_window, _state_of, _other_sessions, _other_sessions_for_hud, _source_record_age, _other_sessions_uncached, _claude_view, _agy_view, _copilot_view, _kimi_view, _opencode_view, _local_view, _pi_view, discover_connected_providers)
from .data import (_compile_hud_data, mark_hud_cache_stale, _bg_refresh_hud, get_hud_data, t_hud_status)
from .lifecycle import (_hud_port, hud_token, _hud_url, _hud_probe, _autostart_hud, _editors, _host_editor, _cli_path, _has_extension, _find_editor_cli, _open_hud, t_start_hud, _stop_hud_on_port, t_restart_hud, start_hud_server, _maybe_open_hud_on_use, t_run_server, _notification_log_lock, _hud_cache_lock, _hud_cache, _hud_token_cache, EDITOR_HINT, MAX_BODY_BYTES, _hud_server_instance, _sampler_running, _hud_opened_on_use, _hud_open_lock, _hud_open_thread)
from .handlers import (SubwayHUDHandler, SubwayHUDServer)

__all__ = [
    '_log_notification_failure',
    '_run_notification_command',
    '_send_desktop_notification',
    'notify_desktop_event',
    '_config_has_subway',
    '_subway_sessions',
    '_app_installed',
    'detect_clients',
    '_window',
    '_state_of',
    '_other_sessions',
    '_other_sessions_for_hud',
    '_source_record_age',
    '_other_sessions_uncached',
    '_claude_view',
    '_agy_view',
    '_copilot_view',
    '_kimi_view',
    '_opencode_view',
    '_local_view',
    '_pi_view',
    'discover_connected_providers',
    '_compile_hud_data',
    'mark_hud_cache_stale',
    '_bg_refresh_hud',
    'get_hud_data',
    't_hud_status',
    '_hud_port',
    'hud_token',
    '_hud_url',
    '_hud_probe',
    '_autostart_hud',
    '_editors',
    '_host_editor',
    '_cli_path',
    '_has_extension',
    '_find_editor_cli',
    '_open_hud',
    't_start_hud',
    '_stop_hud_on_port',
    't_restart_hud',
    'start_hud_server',
    '_maybe_open_hud_on_use',
    't_run_server',
    '_notification_log_lock',
    '_hud_cache_lock',
    '_hud_cache',
    '_hud_token_cache',
    'EDITOR_HINT',
    'MAX_BODY_BYTES',
    '_hud_server_instance',
    '_sampler_running',
    '_hud_opened_on_use',
    '_hud_open_lock',
    '_hud_open_thread',
    'SubwayHUDHandler',
    'SubwayHUDServer',
]
