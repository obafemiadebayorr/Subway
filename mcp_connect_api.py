"""HUD sign-in for shared MCP servers (roadmap 50c-9). Returns public views only, never a token."""
import json
import subprocess
import sys
import threading
import time
import urllib.parse

_sessions = {}  # key -> {"session", "state": waiting|done|error, "error", "started"}
_lock = threading.Lock()
_borrowed_cache = {"at": 0.0, "urls": set()}
_github_app = {"pending": False, "error": "", "slug": ""}  # user-owned GitHub App flow


def _borrowed_urls():
    """Server URLs with a live token in Claude Code's own store. One Keychain read, cached 30s."""
    now = time.time()
    if now - _borrowed_cache["at"] < 30:
        return _borrowed_cache["urls"]
    urls = set()
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                                 capture_output=True, text=True, timeout=10)
            if out.returncode == 0:
                for v in (json.loads(out.stdout).get("mcpOAuth") or {}).values():
                    if not isinstance(v, dict) or not v.get("accessToken"):
                        continue
                    exp = v.get("expiresAt")
                    if isinstance(exp, (int, float)) and exp and exp / 1000 < now:
                        continue
                    urls.add(str(v.get("serverUrl") or "").rstrip("/"))
        except Exception:
            pass
    _borrowed_cache.update(at=now, urls=urls)
    return urls


def _http_servers(project=""):
    import mcp_hub
    specs = mcp_hub.discover_servers(project) or {}
    return {k: s for k, s in specs.items()
            if isinstance(s, dict) and str(s.get("url") or "").startswith(("https://", "http://"))}


def _cancel(entry):
    if entry:
        try:
            entry["session"].cancel()
        except Exception:
            pass


def _row(key, spec, borrowed):
    import oauth_tokens
    url = str(spec.get("url") or "")
    row = {"key": key, "name": spec.get("name") or key,
           "host": urllib.parse.urlparse(url).hostname or "", "source": "none"}
    try:
        import oauth_clients
        row["own_client"] = bool(oauth_clients.get_client(key))
    except Exception:
        row["own_client"] = False
    if key == "github":
        with _lock:
            ga = dict(_github_app)
        row["github_app_pending"] = bool(ga["pending"])
        row["github_app_error"] = ga["error"]
        row["github_app_slug"] = ga["slug"]
    try:
        rec = oauth_tokens.load(key)
    except Exception as e:
        rec = None
        row["error"] = type(e).__name__
    if rec and rec.get("status") != "disconnected":
        v = oauth_tokens.public_view(rec)
        row.update(source="subway", status=v.get("status") or "connected",
                   scopes=v.get("scopes"), expires_at=v.get("expires_at"))
    else:
        if rec:
            row["status"] = "disconnected"
        if url.rstrip("/") in borrowed:
            row["source"] = "borrowed"
    with _lock:
        s = _sessions.get(key)
        if s:
            if s["state"] != "done":
                row["pending"] = s["state"]
                if s.get("user_code") and s["state"] == "waiting":
                    row["user_code"] = s["user_code"]
                if s.get("error"):
                    row["pending_error"] = s["error"]
            if s["state"] != "waiting":
                _sessions.pop(key, None)
    return row


def handle_get(project=""):
    try:
        servers = _http_servers(project)
    except Exception as e:
        return 500, {"error": f"could not list MCP servers: {type(e).__name__}"}
    borrowed = _borrowed_urls()
    return 200, {"servers": [_row(k, servers[k], borrowed) for k in sorted(servers)]}


def _watch(entry):
    try:
        entry["session"].wait()
        entry["state"] = "done"
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        entry["state"] = "error"


def _run_github_app():
    try:
        import github_app_manifest
        res = github_app_manifest.create(open_browser=True, out=lambda *_a, **_k: None) or {}
    except Exception as e:
        res = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    with _lock:
        if res.get("ok"):
            _github_app.update(pending=False, error="", slug=str(res.get("slug") or ""))
        else:
            _github_app.update(pending=False,
                               error=str(res.get("error") or "GitHub App creation failed")[:300])


def handle_post(payload):
    payload = payload or {}
    action = str(payload.get("action") or "")
    key = str(payload.get("server") or payload.get("key") or "")
    project = str(payload.get("project") or "")
    if action not in ("connect", "disconnect", "cancel", "github_app"):
        return 400, {"error": "action must be connect, disconnect, cancel or github_app"}
    if not key:
        return 400, {"error": "server is required"}
    if action == "github_app":
        if key != "github":
            return 400, {"error": "github_app is only for the github server"}
        with _lock:
            if _github_app["pending"]:
                return 200, {"pending": True}
            _github_app.update(pending=True, error="")
        threading.Thread(target=_run_github_app, daemon=True).start()
        return 200, {"pending": True}
    if action in ("cancel", "disconnect"):
        with _lock:
            entry = _sessions.pop(key, None)
        _cancel(entry)
        if action == "cancel":
            return 200, {"server": key, "cancelled": bool(entry)}
        import oauth_revoke
        res = oauth_revoke.disconnect(key) or {}
        _borrowed_cache["at"] = 0.0
        return 200, {k: res.get(k) for k in ("server", "revoked_at_server", "grants_revoked", "was_connected")}
    try:
        servers = _http_servers(project)
    except Exception as e:
        return 500, {"error": f"could not list MCP servers: {type(e).__name__}"}
    spec = servers.get(key)
    if not spec:
        return 404, {"error": f"no HTTP MCP server named {key}"}
    with _lock:
        old = _sessions.pop(key, None)
    _cancel(old)
    import oauth_connect
    try:
        sess = oauth_connect.start_connect(key, spec["url"], timeout=300.0)
    except Exception as e:
        msg = str(e)
        return 502, {"error": msg[:200] if "register themselves" in msg else f"Sign-in could not start: {msg[:160]}"}
    try:
        import webbrowser
        webbrowser.open(sess.authorize_url)
    except Exception:
        pass
    entry = {"session": sess, "state": "waiting", "error": "", "started": time.time(), "user_code": getattr(sess, "user_code", None)}
    with _lock:
        _sessions[key] = entry
    threading.Thread(target=_watch, args=(entry,), daemon=True).start()
    return 200, {"server": key, "authorize_url": sess.authorize_url, "state": "waiting", "user_code": getattr(sess, "user_code", None)}
