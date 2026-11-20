"""Shared MCP hub: discover and host stdio MCP servers for Subway agents/workers.

Step 1 of 4 (roadmap item 50). Standard library only.
"""
import atexit
import json
import os
import re
import subprocess
import sys
import threading
import time

try:
    import subway_home
except Exception:  # pragma: no cover - import error surfaced at call time
    subway_home = None


class McpError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.message = message
        self.code = code


def _subway_dir():
    if subway_home is None:
        raise RuntimeError("subway_home unavailable")
    return subway_home.subway_dir()


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _norm_server(name, raw, owner, source, plugin_root=None):
    if not isinstance(raw, dict):
        return None
    t = raw.get("type")
    command = raw.get("command")
    args = raw.get("args") or []
    env = raw.get("env") or raw.get("environment") or {}
    url = raw.get("url") or raw.get("serverUrl") or raw.get("httpUrl")
    headers = raw.get("headers") or {}

    transport = None
    # type http/sse/remote or a 'url' -> http
    if t in ("http", "sse", "remote") or url:
        if not url:
            return None
        transport = "http"
        # opencode remote may have headers; command/args irrelevant
        command = None
        args = []
        env = {}
    elif command or t == "stdio" or t == "local" or t is None:
        # 'command' (or type stdio/local) -> stdio
        if isinstance(command, list):
            # opencode local: command:[prog,*args]
            if not command:
                return None
            args = list(command[1:]) + list(args)
            command = command[0]
        elif command is None:
            # no usable command
            return None
        transport = "stdio"
        if not isinstance(args, list):
            args = [args]
        if not isinstance(env, dict):
            env = {}
        url = None
        headers = {}
    else:
        return None

    if plugin_root:
        if isinstance(command, str):
            command = command.replace("${CLAUDE_PLUGIN_ROOT}", plugin_root)
        if isinstance(args, list):
            args = [a.replace("${CLAUDE_PLUGIN_ROOT}", plugin_root) if isinstance(a, str) else a for a in args]

    if not isinstance(headers, dict):
        headers = {}
    if not isinstance(env, dict):
        env = {}

    return {
        "name": name,
        "owner": owner,
        "transport": transport,
        "command": command,
        "args": list(args) if isinstance(args, list) else [],
        "env": dict(env),
        "url": url,
        "headers": dict(headers),
        "supported": transport == "stdio" or bool(url),
    }


def _fingerprint(spec):
    if spec["transport"] == "stdio":
        return ("stdio", spec.get("command"), tuple(spec.get("args") or []))
    return ("http", spec.get("url"))


def discover_servers(project_dir=""):
    """Discover MCP server specs from known agent configs."""
    HOME = os.path.expanduser("~")
    # exclusions
    excluded = {"subway", "subway-context", "subway-wake-probe"}
    try:
        cfg = _read_json(os.path.join(_subway_dir(), "config.json")) or {}
        for n in (cfg.get("hub_exclude") or []):
            if isinstance(n, str):
                excluded.add(n)
    except Exception:
        pass

    # Collect entries per owner in source order so later overrides earlier same-name within owner.
    per_owner = {}  # owner -> dict name -> spec
    owner_order = []

    def ensure_owner(owner):
        if owner not in per_owner:
            per_owner[owner] = {}
            owner_order.append(owner)
        return per_owner[owner]

    def add(owner, name, spec):
        if not name or not isinstance(name, str):
            return
        if spec is None:
            return
        ensure_owner(owner)[name] = spec

    def add_mcpservers_dict(owner, d, source=""):
        if not isinstance(d, dict):
            return
        for name, raw in d.items():
            spec = _norm_server(name, raw, owner, source)
            add(owner, name, spec)

    # claude: ~/.claude.json 'mcpServers'
    claude_json = _read_json(os.path.join(HOME, ".claude.json")) or {}
    add_mcpservers_dict("claude", claude_json.get("mcpServers"), "claude.json")
    # ~/.claude.json['projects'][project_dir]['mcpServers']
    if project_dir:
        try:
            proj = ((claude_json.get("projects") or {}).get(project_dir) or {})
            add_mcpservers_dict("claude", proj.get("mcpServers"), "claude.json.project")
        except Exception:
            pass
        # <project_dir>/.mcp.json 'mcpServers'
        add_mcpservers_dict("claude", (_read_json(os.path.join(project_dir, ".mcp.json")) or {}).get("mcpServers"), "project.mcp.json")
    # plugins
    try:
        inst = _read_json(os.path.join(HOME, ".claude", "plugins", "installed_plugins.json")) or {}
        plugins = inst.get("plugins") or {}
        if isinstance(plugins, dict):
            for _pname, entries in plugins.items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    install_path = entry.get("installPath")
                    if not install_path or not isinstance(install_path, str):
                        continue
                    mcp = _read_json(os.path.join(install_path, ".mcp.json"))
                    if not isinstance(mcp, dict):
                        continue
                    servers = mcp.get("mcpServers") if "mcpServers" in mcp else mcp
                    if not isinstance(servers, dict):
                        continue
                    for name, raw in servers.items():
                        spec = _norm_server(name, raw, "claude", "plugin", plugin_root=install_path)
                        add("claude", name, spec)
    except Exception:
        pass

    # Synced Claude plugins: ~/.claude/plugins/synced/<sync-id>/<plugin>/.mcp.json
    try:
        import glob as _glob
        for _mcp_path in sorted(_glob.glob(os.path.join(HOME, ".claude", "plugins", "synced", "*", "*", ".mcp.json"))):
            _root = os.path.dirname(_mcp_path)
            _doc = _read_json(_mcp_path)
            if not isinstance(_doc, dict):
                continue
            _servers = _doc.get("mcpServers") if "mcpServers" in _doc else _doc
            if not isinstance(_servers, dict):
                continue
            for _name, _raw in _servers.items():
                add("claude", _name, _norm_server(_name, _raw, "claude", "plugin", plugin_root=_root))
    except Exception:
        pass

    # claude-desktop
    add_mcpservers_dict(
        "claude-desktop",
        (_read_json(os.path.join(HOME, "Library", "Application Support", "Claude", "claude_desktop_config.json")) or {}).get("mcpServers"),
        "claude-desktop",
    )
    # antigravity (current config path first, then the older one)
    for _agy in (os.path.join(HOME, ".gemini", "config", "mcp_config.json"),
                 os.path.join(HOME, ".gemini", "antigravity", "mcp_config.json")):
        add_mcpservers_dict("antigravity", (_read_json(_agy) or {}).get("mcpServers"), "antigravity")
    # gemini
    add_mcpservers_dict(
        "gemini",
        (_read_json(os.path.join(HOME, ".gemini", "settings.json")) or {}).get("mcpServers"),
        "gemini",
    )
    # opencode: ~/.config/opencode/opencode.json 'mcp'
    try:
        oc = _read_json(os.path.join(HOME, ".config", "opencode", "opencode.json")) or {}
        mcp = oc.get("mcp") or {}
        servers = mcp
        if isinstance(mcp, dict) and "servers" in mcp and isinstance(mcp.get("servers"), dict):
            servers = mcp.get("servers")
        if isinstance(servers, dict):
            for name, raw in servers.items():
                if isinstance(raw, dict) and raw.get("enabled") is False:
                    continue
                spec = _norm_server(name, raw, "opencode", "opencode")
                add("opencode", name, spec)
    except Exception:
        pass
    # Every other app's MCP config: a bounded scan plus user-listed sources (mcp_sources.py). No fixed app list.
    try:
        import mcp_sources
        _known = (
            os.path.join(HOME, ".claude.json"),
            os.path.join(HOME, "Library", "Application Support", "Claude", "claude_desktop_config.json"),
            os.path.join(HOME, ".gemini", "config", "mcp_config.json"),
            os.path.join(HOME, ".gemini", "antigravity", "mcp_config.json"),
            os.path.join(HOME, ".gemini", "settings.json"),
            os.path.join(HOME, ".config", "opencode", "opencode.json"),
        )
        for _owner, _servers, _src in mcp_sources.find(HOME, project_dir, skip=_known):
            add_mcpservers_dict(_owner, _servers, _src)
    except Exception:
        pass
    # subway: subway_dir()/config.json 'hub_mcp_servers' (mcpServers shape)
    try:
        scfg = _read_json(os.path.join(_subway_dir(), "config.json")) or {}
        add_mcpservers_dict("subway", scfg.get("hub_mcp_servers"), "subway")
    except Exception:
        pass

    # subway built-in: desktop tools for workers granted the "desktop" capability (macOS only)
    if sys.platform == "darwin":
        _dspec = _norm_server("desktop", {"command": sys.executable,
                                          "args": [os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop_mcp.py")]},
                              "subway", "builtin")
        add("subway", "desktop", _dspec)

    # Flatten and dedupe by fingerprint (preserve first-seen order)
    ordered_specs = []  # (fp, spec_template)
    fp_index = {}
    # To have stable, deterministic order, iterate owners in discovery order then names sorted? 
    # but within owner later overrides earlier same-name entry - keep final per owner.
    for owner in owner_order:
        # iterate in insertion-ish order of names? dict preserves insertion in py3.7+
        for name, spec in per_owner[owner].items():
            if name in excluded:
                continue
            fp = _fingerprint(spec)
            if fp in fp_index:
                idx = fp_index[fp]
                existing = ordered_specs[idx][1]
                if owner not in existing["_owners"]:
                    existing["_owners"].append(owner)
                # env merged (first wins on conflict)
                merged_env = dict(spec.get("env") or {})
                merged_env.update(existing.get("env") or {})
                existing["env"] = merged_env
            else:
                fp_index[fp] = len(ordered_specs)
                tmpl = {
                    "name": name,
                    "owners": [owner],
                    "transport": spec["transport"],
                    "command": spec.get("command"),
                    "args": list(spec.get("args") or []),
                    "env": dict(spec.get("env") or {}),
                    "url": spec.get("url"),
                    "headers": dict(spec.get("headers") or {}),
                    "supported": spec.get("supported", False),
                    "_owners": [owner],
                    "_fp": fp,
                }
                ordered_specs.append((fp, tmpl))

    # assign keys
    result = {}
    name_to_key = {}
    for _fp, tmpl in ordered_specs:
        name = tmpl["name"]
        owners = list(tmpl["_owners"])
        # key = name; if a different server already holds that name, key = f'{owner}.{name}'
        if name in name_to_key:
            # different server (different fp) has same name
            key = f"{owners[0]}.{name}"
        else:
            key = name
        if key in result:
            # extremely rare collision, disambiguate
            key = f"{owners[0]}.{name}"
            i = 2
            base = key
            while key in result:
                key = f"{base}.{i}"
                i += 1
        name_to_key[name] = name_to_key.get(name, key)
        spec = {
            "key": key,
            "name": name,
            "owners": owners,
            "transport": tmpl["transport"],
            "command": tmpl.get("command"),
            "args": list(tmpl.get("args") or []),
            "env": dict(tmpl.get("env") or {}),
            "url": tmpl.get("url"),
            "headers": dict(tmpl.get("headers") or {}),
            "supported": bool(tmpl.get("supported")),
        }
        result[key] = spec
    return result


def list_servers(project_dir=""):
    specs = discover_servers(project_dir=project_dir)
    out = []
    for key in sorted(specs.keys()):
        s = dict(specs[key])
        if s.get("env"):
            s["env"] = {k: "***" for k in s["env"].keys()}
        else:
            s["env"] = {}
        if s.get("headers"):
            s["headers"] = {k: "***" for k in s["headers"].keys()}
        else:
            s["headers"] = {}
        out.append(s)
    return out


class StdioServer:
    def __init__(self, spec):
        self.spec = spec
        self.key = spec["key"]
        self.proc = None
        self._reader = None
        self._lock = threading.RLock()
        self._pending = {}  # id -> [event, slot]
        self._next_id = 0
        self._tools_cache = None
        self._started = False
        self.last_used = 0.0

    def _log_path(self):
        base = os.path.join(_subway_dir(), "mcp_hub")
        os.makedirs(base, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in ".-_@" else "_" for c in self.key)
        return os.path.join(base, safe + ".log")

    def _spawn(self):
        env = os.environ.copy()
        env.update(self.spec.get("env") or {})
        stderr = open(self._log_path(), "a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [self.spec["command"]] + list(self.spec.get("args") or []),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            env=env,
            text=True,
            bufsize=1,
        )
        self._stderr_handle = stderr
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            mid = msg.get("id")
            # responses have id and no method; requests/notifications from server ignored
            if "method" in msg:
                continue
            if mid is None:
                continue
            with self._lock:
                pending = self._pending.pop(mid, None)
            if pending:
                evt, slot = pending
                slot["msg"] = msg
                evt.set()
        # EOF: wake all pending
        with self._lock:
            items = list(self._pending.values())
            self._pending.clear()
        for evt, slot in items:
            slot["msg"] = {"_eof": True}
            evt.set()

    def start(self):
        with self._lock:
            if self._started and self.alive_locked():
                return
            self._spawn()
            self._started = True
            self._tools_cache = None
        self.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "subway-hub", "version": "1"},
        }, timeout=30)
        self._notify("notifications/initialized", {})

    def _notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        with self._lock:
            proc = self.proc
        if proc is None or proc.stdin is None:
            raise McpError("process not running")
        try:
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
        except Exception as e:
            raise McpError(f"write failed: {e}")

    def alive(self):
        with self._lock:
            return self.alive_locked()

    def alive_locked(self):
        return self.proc is not None and self.proc.poll() is None

    def request(self, method, params=None, timeout=30):
        with self._lock:
            self.last_used = time.time()
            if not self.alive_locked():
                raise McpError("process not running")
            self._next_id += 1
            rid = self._next_id
            evt = threading.Event()
            slot = {"msg": None}
            self._pending[rid] = [evt, slot]
            proc = self.proc
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
        except Exception:
            with self._lock:
                self._pending.pop(rid, None)
            raise McpError("process not running")
        if not evt.wait(timeout):
            with self._lock:
                self._pending.pop(rid, None)
            raise TimeoutError(f"timeout waiting for {method}")
        resp = slot.get("msg") or {}
        if resp.get("_eof"):
            raise McpError("process exited")
        if "error" in resp:
            err = resp["error"] or {}
            raise McpError(err.get("message", "MCP error"), code=err.get("code"))
        return resp.get("result")

    def tools(self):
        with self._lock:
            if self._tools_cache is not None:
                return list(self._tools_cache)
        all_tools = []
        cursor = None
        while True:
            params = {}
            if cursor is not None:
                params["cursor"] = cursor
            result = self.request("tools/list", params, timeout=30) or {}
            page = result.get("tools") or []
            all_tools.extend(page)
            cursor = result.get("nextCursor")
            if not cursor:
                break
        with self._lock:
            self._tools_cache = list(all_tools)
        return list(all_tools)

    def call(self, tool, arguments=None, timeout=120):
        return self.request("tools/call", {"name": tool, "arguments": arguments or {}}, timeout=timeout)

    def stop(self):
        with self._lock:
            proc = self.proc
            self.proc = None
            self._started = False
            self._tools_cache = None
            # wake pending
            items = list(self._pending.values())
            self._pending.clear()
        for evt, slot in items:
            slot["msg"] = {"_eof": True}
            evt.set()
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            h = getattr(self, "_stderr_handle", None)
            if h:
                h.close()
        except Exception:
            pass


# Module-level server registry
_servers = {}  # key -> StdioServer
_server_locks = {}  # key -> Lock
_registry_lock = threading.Lock()
_specs_cache = None
_specs_project = None


def _ensure_spec(key_or_name, project_dir=""):
    specs = discover_servers(project_dir=project_dir)
    if key_or_name in specs:
        return specs[key_or_name]
    # plain name or owner.name
    for spec in specs.values():
        if spec["name"] == key_or_name or spec["key"] == key_or_name:
            return spec
        # owner.name accepted
        if "." in key_or_name:
            owner, name = key_or_name.split(".", 1)
            if spec["name"] == name and owner in spec.get("owners", []):
                return spec
    raise KeyError(f"Unknown MCP server '{key_or_name}'. Known keys: {sorted(specs.keys())}")


def _get_lock(key):
    with _registry_lock:
        if key not in _server_locks:
            _server_locks[key] = threading.Lock()
        return _server_locks[key]


class HttpServer:
    """Streamable-HTTP MCP client for a remote server, authenticated with the owner's own token.
    Same surface as StdioServer: key, last_used, _tools_cache, alive(), start(), tools(), call(), stop()."""

    def __init__(self, spec):
        self.spec = spec
        self.key = spec["key"]
        self.url = spec.get("url")
        self.proc = None
        self.session_id = None
        self._next_id = 0
        self._tools_cache = None
        self._started = False
        self.last_used = 0.0

    def _headers(self):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        h.update(self.spec.get("headers") or {})
        if not any(k.lower() == "authorization" for k in h):
            h.update(_oauth_header(self.url, self.key))
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _post(self, payload, timeout=60):
        import urllib.request, urllib.error
        name = self.spec.get("name")
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode("utf-8"),
                                     headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                ctype = resp.headers.get("Content-Type", "") or ""
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            hint = " (the owner must sign in again with /mcp)" if e.code in (401, 403) else ""
            raise McpError(f"{name}: HTTP {e.code}{hint}")
        except Exception as e:
            raise McpError(f"{name}: {type(e).__name__}: {e}")
        if "id" not in payload:
            return None
        msgs = []
        if "text/event-stream" in ctype:
            for line in body.splitlines():
                if line.startswith("data:"):
                    try:
                        msgs.append(json.loads(line[5:].strip()))
                    except ValueError:
                        pass
        elif body.strip():
            d = json.loads(body)
            msgs = d if isinstance(d, list) else [d]
        for m in msgs:
            if isinstance(m, dict) and m.get("id") == payload["id"]:
                if m.get("error"):
                    err = m["error"]
                    raise McpError(f"{name}: {err.get('message') if isinstance(err, dict) else err}")
                return m.get("result")
        raise McpError(f"{name}: no reply to {payload.get('method')}")

    def request(self, method, params=None, timeout=60):
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.last_used = time.time()
        return self._post(msg, timeout)

    def start(self):
        if self._started:
            return
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "subway-hub", "version": "1"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._started = True

    def alive(self):
        return self._started

    def alive_locked(self):
        return self._started

    def tools(self):
        self.start()
        if self._tools_cache is None:
            out, cursor = [], None
            while True:
                res = self.request("tools/list", {"cursor": cursor} if cursor else {}) or {}
                out.extend(res.get("tools") or [])
                cursor = res.get("nextCursor")
                if not cursor:
                    break
            self._tools_cache = out
        return list(self._tools_cache)

    def call(self, tool, arguments=None, timeout=120):
        self.start()
        return self.request("tools/call", {"name": tool, "arguments": arguments or {}}, timeout)

    def stop(self):
        self._started = False
        self.session_id = None
        self._tools_cache = None


def _get_server(key_or_name, project_dir=""):
    spec = _ensure_spec(key_or_name, project_dir=project_dir)
    if not spec.get("supported"):
        raise McpError("this server has no command or url the hub can reach")
    key = spec["key"]
    with _registry_lock:
        srv = _servers.get(key)
        if srv is None:
            srv = StdioServer(spec) if spec.get("transport") == "stdio" else HttpServer(spec)
            _servers[key] = srv
        else:
            srv.spec = spec
    return srv


def get(key_or_name, project_dir=""):
    srv = _get_server(key_or_name, project_dir=project_dir)
    lock = _get_lock(srv.key)
    with lock:
        if not srv.alive():
            srv.start()
        srv.last_used = time.time()
    return srv


def _with_restart(srv, fn):
    lock = _get_lock(srv.key)
    for attempt in (1, 2):
        try:
            with lock:
                if not srv.alive():
                    srv.start()
                return fn()
        except (McpError, TimeoutError, BrokenPipeError, OSError):
            if attempt == 2:
                raise
            try:
                srv.stop()
            except Exception:
                pass
            # restart once on next iteration
    raise McpError("request failed after restart")


def tools(key_or_name, project_dir=""):
    srv = _get_server(key_or_name, project_dir=project_dir)
    return _with_restart(srv, lambda: srv.tools())


def call(key_or_name, tool, arguments=None, timeout=120, project_dir=""):
    srv = _get_server(key_or_name, project_dir=project_dir)
    return _with_restart(srv, lambda: srv.call(tool, arguments, timeout=timeout))


def stop_idle(max_idle_seconds=600):
    now = time.time()
    stopped = []
    with _registry_lock:
        items = list(_servers.items())
    for key, srv in items:
        lock = _get_lock(key)
        with lock:
            if srv.alive() and srv.last_used and (now - srv.last_used) >= max_idle_seconds:
                srv.stop()
                stopped.append(key)
    return stopped


def stop_all():
    with _registry_lock:
        items = list(_servers.values())
    for srv in items:
        lock = _get_lock(srv.key)
        with lock:
            try:
                srv.stop()
            except Exception:
                pass


def status():
    specs = discover_servers()
    with _registry_lock:
        items = dict(_servers)
    out = []
    for key, spec in sorted(specs.items()):
        srv = items.get(key)
        out.append({
            "key": key,
            "owners": list(spec.get("owners") or []),
            "alive": bool(srv.alive()) if srv else False,
            "last_used": srv.last_used if srv else 0,
            "tool_count": (len(srv._tools_cache) if srv and srv._tools_cache is not None else None),
        })
    return out


atexit.register(stop_all)


def need_kinds(needs, project_dir="", _found=None):
    """Concurrency pools a worker's lent servers draw from: 'local' (its own heavy copy) and/or 'remote'.
    Shared servers (one hub-held copy) draw from neither."""
    kinds = set()
    if not needs:
        return kinds
    try:
        found = _found if _found is not None else discover_servers(project_dir)
    except Exception:
        return kinds
    for n in needs:
        spec = found.get(n) or next((s for s in found.values() if s.get("name") == n), None)
        if not spec:
            continue
        mode = share_mode(spec)
        if mode == "per_worker":
            kinds.add("local")
        elif mode == "remote":
            kinds.add("remote")
    return kinds


def cli_list(project_dir=""):
    """Lines for `subway mcp list`: every lendable server, its owners, transport, share mode and current borrowers."""
    try:
        import server as _srv
        workers = [w for w in _srv._all_workers() if w.get("status") in ("running", "launching", "stopping")]
    except Exception:
        workers = []
    found = discover_servers(project_dir)
    rows = [("SERVER", "OWNERS", "TRANSPORT", "SHARE", "LENT TO")]
    for key, spec in sorted(found.items()):
        borrowers = [w.get("id", "?") for w in workers
                     if key in (w.get("needs") or []) or spec.get("name") in (w.get("needs") or [])]
        rows.append((key, ",".join(spec.get("owners") or []), spec.get("transport") or "?", share_mode(spec),
                     ", ".join(borrowers) or "-"))
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    return ["  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)).rstrip() for r in rows]


def cli_media(worker_id=""):
    """Lines for `subway mcp media [worker_id]`: images workers got back from lent servers (screenshots)."""
    root = os.path.join(_subway_dir(), "hub_media")
    dirs = [worker_id] if worker_id else (sorted(os.listdir(root)) if os.path.isdir(root) else [])
    out = []
    for w in dirs:
        d = os.path.join(root, w)
        if os.path.isdir(d):
            out.extend(os.path.join(d, fn) for fn in sorted(os.listdir(d)))
    return out or ["No saved images" + (f" for {worker_id}." if worker_id else ".")]


def cli_log(n=20):
    """Lines for `subway mcp log`: the last n lent calls (argument names only)."""
    import datetime as _dt
    path = os.path.join(_subway_dir(), "hub_calls.jsonl")
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()[-max(1, int(n)):]
    except OSError:
        return ["No lent MCP calls yet."]
    out = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        ts = _dt.datetime.fromtimestamp(r.get("ts", 0)).strftime("%m-%d %H:%M:%S")
        refused = r.get("decision") not in (None, "", "allow")
        status = "ok" if r.get("ok") else ("denied" if refused else "failed")
        out.append(f"{ts}  {r.get('worker_id') or 'owner':<16} {r.get('server')}.{r.get('tool')}  {status}"
                   + (f"  ({r.get('decision')})" if refused else ""))
    return out or ["No lent MCP calls yet."]


def copilot_mcp_config(specs):
    """Copilot CLI --additional-mcp-config document."""
    servers = {}
    for s in specs:
        if s.get("transport") == "stdio":
            servers[s["name"]] = {"type": "local", "command": s["command"], "args": list(s.get("args") or []),
                                  "env": dict(s.get("env") or {}), "tools": ["*"]}
        else:
            servers[s["name"]] = {"type": s.get("transport") or "http", "url": s["url"],
                                  "headers": dict(s.get("headers") or {}), "tools": ["*"]}
    return {"mcpServers": servers}


def worker_mcp_dir():
    return os.path.join(_subway_dir(), "hub_mcp")


def write_worker_mcp_file(worker_id, needs, project_dir="", fmt="claude"):
    """Write the worker's native MCP config to a private file; return its path, or None if nothing to lend."""
    if not worker_id or not needs:
        return None
    specs = native_specs(needs, project_dir, worker_id=worker_id)
    if not specs:
        return None
    doc = copilot_mcp_config(specs) if fmt == "copilot" else claude_mcp_config(specs)
    d = worker_mcp_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{worker_id}.{fmt}.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(doc, fh)
    return path


def hub_servers(project_dir=""):
    """List discovered MCP hub servers (safe for display)."""
    return list_servers(project_dir=project_dir)


def hub_tools(server, project_dir=""):
    """List tools for a hosted stdio MCP server."""
    return tools(server, project_dir=project_dir)


def hub_call(server, tool, arguments=None, timeout=120, project_dir=""):
    """Call a tool on a hosted stdio MCP server."""
    return call(server, tool, arguments=arguments, timeout=timeout, project_dir=project_dir)


def _claude_credential_blobs():
    """Claude Code's credential JSON from every store it uses: macOS Keychain, then the plaintext file Claude Code
    uses on Linux and Windows (<CLAUDE_CONFIG_DIR or ~/.claude>/.credentials.json). Yields parsed dicts; never logs."""
    import json, os, subprocess, sys
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                                 capture_output=True, text=True, timeout=10)
            if out.returncode == 0:
                yield json.loads(out.stdout)
        except Exception:
            pass
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    try:
        with open(os.path.join(base, ".credentials.json"), encoding="utf-8") as fh:
            yield json.load(fh)
    except Exception:
        pass

def _oauth_header(url, server_key=""):
    """Bearer header for a remote server. Subway's own sign-in (HUD MCP list, any OS, refreshed when due) wins;
    then Claude Code's OAuth store, freshest valid entry. Never logged."""
    import time
    if not url:
        return {}
    if server_key:
        try:
            import oauth_tokens
            rec = oauth_tokens.ensure_fresh(server_key)
            res = str(rec.get("resource") or "").rstrip("/")
            if rec.get("access_token") and rec.get("status") != "disconnected" and (not res or res == url.rstrip("/")):
                return {"Authorization": f"Bearer {rec['access_token']}"}
        except Exception:
            pass
    want = url.rstrip("/")
    best = None  # (expires_sort_key, token)
    for blob in _claude_credential_blobs():
        for v in ((blob or {}).get("mcpOAuth") or {}).values():
            if not isinstance(v, dict) or str(v.get("serverUrl") or "").rstrip("/") != want:
                continue
            tok, exp = v.get("accessToken"), v.get("expiresAt")
            if not tok or (isinstance(exp, (int, float)) and exp and exp / 1000 < time.time()):
                continue
            key = exp if isinstance(exp, (int, float)) and exp else float("inf")
            if best is None or key > best[0]:
                best = (key, tok)
    return {"Authorization": f"Bearer {best[1]}"} if best else {}


_PROXY_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def _proxy_enabled():
    try:
        with open(os.path.join(_subway_dir(), "config.json"), encoding="utf-8") as fh:
            return json.load(fh).get("hub_proxy", True) is not False
    except Exception:
        return True


def _proxy_spec(key, spec, worker_id, project_dir, ttl):
    """A remote spec pointed at Subway's local proxy with a per-worker capability token (50c-7).
    The worker never sees the real bearer; the proxy adds it and applies the publish/delete gate."""
    import hud_server.lifecycle as lifecycle
    import mcp_grants
    real_url = str(spec.get("url") or "")
    out = dict(spec)
    out["url"] = f"{lifecycle._hud_url(lifecycle._hud_port())}/api/mcp/proxy/{key}"
    out["headers"] = {"Authorization": "Bearer " + mcp_grants.issue_grant(worker_id, key, project_dir or "", ttl, url=real_url)}
    return out


def worker_servers(needs, project_dir="", worker_id="", ttl=3900):
    """Full specs for the servers a worker was granted. Remote ones go through Subway's proxy when the
    worker is known (no real token leaves Subway); otherwise the owner's auth is filled in as before."""
    found = discover_servers(project_dir)
    out = []
    for n in needs or []:
        spec = found.get(n) or next((s for s in found.values() if s.get("name") == n), None)
        if not spec:
            continue
        spec = dict(spec)
        if (spec.get("name") or n) == "desktop":
            env = dict(spec.get("env") or {})
            if worker_id:
                env["SUBWAY_WORKER_ID"] = worker_id
            env["SUBWAY_DIR"] = _subway_dir()
            spec["env"] = env
        own_auth = any(k.lower() == "authorization" for k in (spec.get("headers") or {}))
        key = n if _PROXY_KEY_RE.match(str(n)) else str(spec.get("name") or "")
        if (spec.get("transport") != "stdio" and worker_id and not own_auth
                and _PROXY_KEY_RE.match(key) and _proxy_enabled()):
            out.append(_proxy_spec(key, spec, worker_id, project_dir, ttl))
            continue
        if spec.get("transport") != "stdio":
            hdr = dict(spec.get("headers") or {})
            if not any(k.lower() == "authorization" for k in hdr):
                hdr.update(_oauth_header(spec.get("url"), key))
            spec["headers"] = hdr
        out.append(spec)
    return out


def acp_mcp_servers(specs):
    """OpenCode ACP session/new 'mcpServers' list."""
    res = []
    for s in specs:
        if s.get("transport") == "stdio":
            res.append({"name": s["name"], "command": s["command"], "args": list(s.get("args") or []),
                        "env": [{"name": k, "value": str(v)} for k, v in (s.get("env") or {}).items()]})
        else:
            res.append({"type": s.get("transport") or "http", "name": s["name"], "url": s["url"],
                        "headers": [{"name": k, "value": str(v)} for k, v in (s.get("headers") or {}).items()]})
    return res


def claude_mcp_config(specs):
    """Claude Code --mcp-config document."""
    servers = {}
    for s in specs:
        if s.get("transport") == "stdio":
            servers[s["name"]] = {"command": s["command"], "args": list(s.get("args") or []),
                                  "env": dict(s.get("env") or {})}
        else:
            servers[s["name"]] = {"type": s.get("transport") or "http", "url": s["url"],
                                  "headers": dict(s.get("headers") or {})}
    return {"mcpServers": servers}


# How a lent server is given to workers: "per_worker" = each worker runs its own copy (native injection),
# "shared" = one hub-held copy, calls queued (single-resource servers: one desktop app / canvas),
# "remote" = http/sse server, each worker connects itself with the owner's auth.
SHARE_DEFAULTS = {"paper": "shared"}
ISOLATE_ARGS = {"chrome-devtools-mcp": "--isolated"}


def share_mode(spec):
    """Share mode for one spec; subway config 'hub_share' {name: mode} overrides the defaults."""
    name = spec.get("name") or ""
    try:
        cfg = _read_json(os.path.join(_subway_dir(), "config.json")) or {}
        mode = (cfg.get("hub_share") or {}).get(name)
    except Exception:
        mode = None
    if mode in ("per_worker", "shared", "remote"):
        return mode
    if spec.get("transport") != "stdio":
        return "remote"
    return SHARE_DEFAULTS.get(name, "per_worker")


def native_specs(needs, project_dir="", worker_id="", ttl=3900):
    """Specs a worker should launch itself: every granted server except the shared ones.
    Per-worker browsers get an isolated profile so parallel workers never fight over one Chrome."""
    out = []
    for spec in worker_servers(needs, project_dir, worker_id=worker_id, ttl=ttl):
        if share_mode(spec) == "shared":
            continue
        spec = dict(spec)
        args = list(spec.get("args") or [])
        flag = ISOLATE_ARGS.get(spec.get("name") or "")
        if flag and spec.get("transport") == "stdio" and flag not in args:
            args.append(flag)
        spec["args"] = args
        out.append(spec)
    return out
