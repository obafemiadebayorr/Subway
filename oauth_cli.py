"""subway mcp connect|disconnect|status (roadmap 50c-8). Prints the public view only, never a token."""
import time
import webbrowser


def _find_server(key, project=""):
    """Return (key, url, error). Only HTTP servers can sign in."""
    import mcp_hub
    try:
        specs = mcp_hub.discover_servers(project) or {}
    except Exception as e:
        return key, None, f"Error: could not list MCP servers: {type(e).__name__}"
    spec = specs.get(key)
    if spec is None:
        for k, s in specs.items():
            if isinstance(s, dict) and s.get("name") == key:
                key, spec = k, s
                break
    if not spec:
        return key, None, f"Error: no shared MCP server named `{key}`. Run `subway mcp list`."
    url = str(spec.get("url") or "")
    if not url.startswith(("https://", "http://")):
        return key, None, f"Error: `{key}` is not an HTTP server; only HTTP servers can sign in."
    return key, url, ""


def status_line(key):
    import oauth_tokens
    try:
        rec = oauth_tokens.load(key)
    except ValueError:
        return f"{key}: invalid server name"
    except Exception as e:
        return f"{key}: could not read sign-in ({type(e).__name__})"
    if not rec:
        return f"{key}: not signed in to Subway (a borrowed token is used, if any)"
    v = oauth_tokens.public_view(rec)
    parts = [f"{key}: {v.get('status') or 'connected'} (Subway token)"]
    if v.get("scopes"):
        sc = v["scopes"]
        parts.append("scopes " + (" ".join(sc) if isinstance(sc, list) else str(sc)))
    exp = v.get("expires_at")
    if isinstance(exp, (int, float)):
        mins = int((exp - time.time()) // 60)
        parts.append(f"access token expires in {mins} min" if mins >= 0 else "access token expired (refreshes on next use)")
    return ", ".join(parts)


def connect(key, project="", scopes=None, timeout=180.0, open_browser=True, out=print):
    key, url, err = _find_server(key, project)
    if err:
        out(err)
        return 2
    import oauth_connect
    try:
        sess = oauth_connect.start_connect(key, url, scopes=scopes or None, timeout=timeout)
    except Exception as e:
        out(f"Error: could not start sign-in for {key}: {type(e).__name__}: {str(e)[:200]}")
        return 1
    out(f"Sign in to {key} in your browser. If it does not open, visit:\n  {sess.authorize_url}")
    if getattr(sess, "user_code", None):
        out(f"Enter this code there: {sess.user_code}")
    if open_browser:
        try:
            webbrowser.open(sess.authorize_url)
        except Exception:
            pass
    try:
        sess.wait()
    except KeyboardInterrupt:
        sess.cancel()
        out("Cancelled.")
        return 130
    except Exception as e:
        out(f"Error: sign-in failed: {type(e).__name__}: {str(e)[:200]}")
        return 1
    out(status_line(key))
    return 0


def disconnect(key, out=print):
    import oauth_revoke
    res = oauth_revoke.disconnect(key)
    if not res.get("was_connected"):
        out(f"{key}: was not signed in. Revoked {res.get('grants_revoked', 0)} worker pass(es).")
        return 0
    where = "revoked at the server" if res.get("revoked_at_server") else "removed locally (the server did not confirm revocation)"
    out(f"{key}: signed out, token {where}. Revoked {res.get('grants_revoked', 0)} worker pass(es).")
    return 0


def status(key="", project="", out=print):
    if key:
        out(status_line(key))
        return 0
    import mcp_hub
    try:
        specs = mcp_hub.discover_servers(project) or {}
    except Exception as e:
        out(f"Error: could not list MCP servers: {type(e).__name__}")
        return 1
    keys = [k for k, s in specs.items() if isinstance(s, dict) and str(s.get("url") or "").startswith(("https://", "http://"))]
    if not keys:
        out("(no HTTP MCP servers found)")
        return 0
    for k in sorted(keys):
        out(status_line(k))
    return 0


def client(key, client_id="", remove=False, out=print, ask_secret=None):
    """Set your own OAuth app for a server that doesn't let apps register themselves. The secret is asked for, never taken from argv."""
    import oauth_clients
    if not key:
        out(USAGE)
        return 2
    try:
        if remove:
            gone = oauth_clients.remove_client(key)
            out(f"{key}: removed your OAuth app." if gone else f"{key}: had no OAuth app set.")
            return 0
        if not client_id:
            c = oauth_clients.get_client(key)
            if not c:
                out(f"{key}: no OAuth app set. Subway registers itself where the server allows it.")
            else:
                out(f"{key}: your OAuth app {c['client_id']}" + (", secret in the Keychain" if c["has_secret"] else ", no secret"))
            return 0
        if ask_secret is None:
            import getpass
            ask_secret = lambda: getpass.getpass("Client secret (leave empty if the app has none): ")
        secret = ask_secret() or None
        oauth_clients.set_client(key, client_id, secret)
    except ValueError as e:
        out(f"Error: {e}")
        return 2
    except Exception as e:
        out(f"Error: could not save the OAuth app ({type(e).__name__})")
        return 1
    out(f"{key}: OAuth app saved" + (", secret stored in the Keychain." if secret else ".")
        + f" Its callback URL must be http://127.0.0.1/callback. Now run: subway mcp connect {key}")
    return 0


USAGE = ("Usage: subway mcp connect <server> [--scope S ...] [--no-browser] [--project DIR]\n"
         "       subway mcp disconnect <server>\n"
         "       subway mcp status [server] [--project DIR]\n"
         "       subway mcp client <server> [--client-id ID | --remove]   (your own OAuth app, e.g. for GitHub)\n"
         "       subway mcp github-app [--no-browser]                    (create your own GitHub App in one click)")


def github_app(open_browser=True, out=print):
    """Create a user-owned GitHub App with GitHub's manifest flow."""
    import github_app_manifest
    res = github_app_manifest.create(open_browser=open_browser, out=out) or {}
    return 0 if res.get("ok") else 1


def run(rest, out=print):
    """rest = argv after `mcp`, e.g. ["connect", "notion", "--scope", "read"]."""
    verb = rest[0] if rest else ""
    args, scopes, project, open_browser, client_id, remove = [], [], "", True, "", False
    i = 1
    while i < len(rest):
        a = rest[i]
        if a == "--scope" and i + 1 < len(rest):
            scopes.append(rest[i + 1]); i += 2; continue
        if a == "--project" and i + 1 < len(rest):
            project = rest[i + 1]; i += 2; continue
        if a == "--no-browser":
            open_browser = False; i += 1; continue
        if a == "--client-id" and i + 1 < len(rest):
            client_id = rest[i + 1]; i += 2; continue
        if a == "--remove":
            remove = True; i += 1; continue
        args.append(a); i += 1
    key = args[0] if args else ""
    if verb == "client":
        return client(key, client_id, remove, out=out)
    if verb == "status":
        return status(key, project, out=out)
    if verb == "github-app":
        return github_app(open_browser=open_browser, out=out)
    if verb in ("connect", "disconnect") and not key:
        out(USAGE)
        return 2
    if verb == "connect":
        return connect(key, project, scopes, open_browser=open_browser, out=out)
    if verb == "disconnect":
        return disconnect(key, out=out)
    out(USAGE)
    return 2
