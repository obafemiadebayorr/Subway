"""Find MCP server configs on this machine without a fixed list of apps.
A bounded scan finds any app that keeps an MCP config file; users can add more in config.json."""
import json
import os
import time

JSON_NAMES = ("mcp.json", "mcp_config.json", "mcp-config.json", "mcp_settings.json", ".mcp.json")
TOML_NAMES = ("config.toml",)
KEYS = ("mcpServers", "servers", "mcp_servers")
SKIP_DIRS = {"node_modules", ".git", ".cache", "Caches", "cache", ".npm", ".Trash", "logs", "sessions",
             "worktrees", "venv", ".venv", "site-packages", "extensions", "plugins", "Cache", "GPUCache",
             "IndexedDB", "Local Storage", "Service Worker", "CachedData", "workspaceStorage", "History"}
# Handled by mcp_hub.discover_servers with their own formats.
SKIP_ROOTS = {".claude", ".gemini", ".subway", ".subway-context", "Claude", "opencode"}
GENERIC = {"config", "agent", "user", "settings", "mcp", "globalstorage", "default", "data", "application support"}
MAX_DEPTH = 3
MAX_DIRS = 4000
MAX_BYTES = 1024 * 1024
_cache = {}


def _owner_for(path, root):
    """Name the app after the nearest folder that is not a generic one: ~/.pi/agent/mcp.json -> pi."""
    d = os.path.dirname(path)
    while d and os.path.abspath(d) != os.path.abspath(root):
        name = os.path.basename(d).lstrip(".")
        if name and name.lower() not in GENERIC:
            return name.lower().replace(" ", "-")
        d = os.path.dirname(d)
    return "other"


def _read(path):
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        if path.endswith(".toml"):
            import tomllib
            with open(path, "rb") as fh:
                return tomllib.load(fh)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _servers_in(data):
    if not isinstance(data, dict):
        return None
    for k in KEYS:
        v = data.get(k)
        if isinstance(v, dict) and v and all(isinstance(x, dict) for x in v.values()):
            return v
    return None


def _walk(root, depth, budget, out, skip):
    if depth > MAX_DEPTH or budget[0] <= 0:
        return
    budget[0] -= 1
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    for e in entries:
        try:
            if e.is_symlink():
                continue
            if e.is_file() and (e.name in JSON_NAMES or e.name in TOML_NAMES):
                p = os.path.realpath(e.path)
                if p not in skip:
                    out.append(e.path)
            elif e.is_dir() and e.name not in SKIP_DIRS:
                _walk(e.path, depth + 1, budget, out, skip)
        except OSError:
            continue


def _roots(home):
    roots = []
    try:
        for e in os.scandir(home):
            if e.is_dir() and not e.is_symlink() and e.name.startswith(".") and e.name.lstrip(".") and e.name not in SKIP_ROOTS \
                    and e.name not in SKIP_DIRS and e.name != ".config":
                roots.append((e.path, home))
    except OSError:
        pass
    for parent in (os.path.join(home, ".config"), os.path.join(home, "Library", "Application Support")):
        try:
            for e in os.scandir(parent):
                if e.is_dir() and not e.is_symlink() and e.name not in SKIP_ROOTS and e.name not in SKIP_DIRS:
                    roots.append((e.path, parent))
        except OSError:
            pass
    return roots


def _user_sources(home):
    """config.json 'hub_mcp_sources': [{"path": "~/x/mcp.json", "owner": "x", "key": "mcpServers"}]."""
    out = []
    try:
        with open(os.path.join(home, ".subway", "config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh) or {}
    except Exception:
        return out, True
    for s in cfg.get("hub_mcp_sources") or []:
        if isinstance(s, dict) and isinstance(s.get("path"), str):
            out.append(s)
    return out, cfg.get("hub_scan") is not False


def find(home, project_dir="", skip=(), ttl=60.0):
    """Yield (owner, servers_dict, source) for every MCP config found. Cached for ttl seconds."""
    skip = {os.path.realpath(p) for p in skip}
    key = (home, project_dir, tuple(sorted(skip)))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return list(hit[1])
    results = []
    user, scan = _user_sources(home)
    for s in user:
        p = os.path.expanduser(s["path"]).replace("~", home, 1) if s["path"].startswith("~") else s["path"]
        data = _read(p)
        servers = data.get(s["key"]) if isinstance(data, dict) and s.get("key") else _servers_in(data)
        if isinstance(servers, dict):
            results.append((str(s.get("owner") or _owner_for(p, home)), servers, "user:" + p))
    if scan:
        files = []
        budget = [MAX_DIRS]
        for root, base in _roots(home):
            _walk(root, 1, budget, files, skip)
        for p in sorted(files):
            servers = _servers_in(_read(p))
            if servers:
                base = home
                for parent in (os.path.join(home, ".config"), os.path.join(home, "Library", "Application Support")):
                    if p.startswith(parent + os.sep):
                        base = parent
                results.append((_owner_for(p, base), servers, "scan:" + p))
    if project_dir and os.path.isdir(project_dir):
        for sub in (".cursor", ".vscode", ".windsurf", ".devin", ".codex"):
            for name in JSON_NAMES + TOML_NAMES:
                p = os.path.join(project_dir, sub, name)
                if os.path.isfile(p) and os.path.realpath(p) not in skip:
                    servers = _servers_in(_read(p))
                    if servers:
                        results.append((sub.lstrip("."), servers, "project:" + p))
    _cache[key] = (time.time(), list(results))
    return results
