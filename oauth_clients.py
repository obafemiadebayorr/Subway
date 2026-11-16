"""Your own OAuth app, for servers that don't let apps register themselves (e.g. GitHub).
The client ID lives in Subway's config.json; the client secret only in the Keychain."""
import json
import os
import re

import keychain_native

_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def _config_path():
    import mcp_hub
    return os.path.join(mcp_hub._subway_dir(), "config.json")


def _read_cfg():
    try:
        with open(_config_path(), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_cfg(cfg):
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    os.replace(tmp, path)


def _account(server_key):
    if not isinstance(server_key, str) or not _KEY_RE.fullmatch(server_key) or server_key in (".", ".."):
        raise ValueError("invalid server name")
    return "client." + server_key


def get_client(server_key):
    _account(server_key)
    ent = (_read_cfg().get("hub_oauth_clients") or {}).get(server_key)
    if isinstance(ent, dict) and isinstance(ent.get("client_id"), str) and ent["client_id"]:
        return {"client_id": ent["client_id"], "has_secret": bool(ent.get("has_secret"))}
    return None


def client_secret(server_key):
    c = get_client(server_key)
    if not c or not c["has_secret"]:
        return None
    raw = keychain_native.get_secret(keychain_native.SUBWAY_MCP_SERVICE, _account(server_key))
    return raw.decode("utf-8") if raw else None


def set_client(server_key, client_id, secret=None):
    acct = _account(server_key)
    if not isinstance(client_id, str) or not client_id.strip() or len(client_id) > 200:
        raise ValueError("client ID is required")
    if secret:
        keychain_native.set_secret(keychain_native.SUBWAY_MCP_SERVICE, acct, secret.encode("utf-8"))
    else:
        try:
            keychain_native.delete_secret(keychain_native.SUBWAY_MCP_SERVICE, acct)
        except Exception:
            pass
    cfg = _read_cfg()
    clients = cfg.get("hub_oauth_clients") if isinstance(cfg.get("hub_oauth_clients"), dict) else {}
    clients[server_key] = {"client_id": client_id.strip(), "has_secret": bool(secret)}
    cfg["hub_oauth_clients"] = clients
    _write_cfg(cfg)


def remove_client(server_key):
    acct = _account(server_key)
    try:
        keychain_native.delete_secret(keychain_native.SUBWAY_MCP_SERVICE, acct)
    except Exception:
        pass
    cfg = _read_cfg()
    clients = cfg.get("hub_oauth_clients") if isinstance(cfg.get("hub_oauth_clients"), dict) else {}
    existed = clients.pop(server_key, None) is not None
    cfg["hub_oauth_clients"] = clients
    _write_cfg(cfg)
    return existed
