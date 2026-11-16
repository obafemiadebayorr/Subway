"""Create a user-owned GitHub App with GitHub's manifest flow.

The flow keeps every secret out of Subway's logs and return values:

1. A single-use loopback server serves a page containing a manifest form.
2. The form posts to GitHub, the user confirms, and GitHub redirects back
   with a one-time code.
3. The code is exchanged for the app's credentials. Only the OAuth client
   id + secret and non-secret metadata are persisted (the secret goes to the
   OS keychain through ``oauth_clients``). The private key and webhook
   secret are discarded immediately.

Standard library only. Never print, log or return secrets.
"""

from __future__ import annotations

import hmac
import html
import http.server
import json
import secrets
import threading
import urllib.parse
import urllib.request

_MANIFEST_ACTION = "https://github.com/settings/apps/new"
_CONVERSIONS = "https://api.github.com/app-manifests/{code}/conversions"
_LOOPBACK_CALLBACK = "http://127.0.0.1/callback"
_MAX_PATH = 8192
_MAX_BODY = 64 * 1024


def _manifest(port: int) -> dict:
    # GitHub App names are global, so a short random suffix avoids collisions.
    return {
        "name": "Subway " + secrets.token_hex(3),
        "url": "https://github.com",
        "redirect_url": f"http://127.0.0.1:{port}/done",
        "callback_urls": [_LOOPBACK_CALLBACK],
        "public": False,
        "request_oauth_on_install": False,
        "hook_attributes": {"url": "https://example.invalid", "active": False},
        "default_permissions": {
            "contents": "write",
            "issues": "write",
            "pull_requests": "write",
            "metadata": "read",
        },
    }


def _page(title: str, body: str) -> bytes:
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title></head><body>{body}</body></html>"
    ).encode("utf-8")


def _form_page(manifest_json: str, state: str) -> bytes:
    action = _MANIFEST_ACTION + "?" + urllib.parse.urlencode({"state": state})
    body = (
        f"<h1>{html.escape('Create your Subway GitHub App')}</h1>"
        '<p>Continue to GitHub to confirm the app. This page submits itself.</p>'
        f'<form id="manifest-form" method="post" action="{html.escape(action)}">'
        f'<input type="hidden" name="manifest" value="{html.escape(manifest_json, quote=True)}">'
        '<noscript><button type="submit">Continue to GitHub</button></noscript>'
        "</form><script>document.getElementById('manifest-form').submit();</script>"
    )
    return _page("Create your Subway GitHub App", body)


def _install_page(slug: str) -> bytes:
    url = _install_url(slug)
    body = (
        "<h1>GitHub App created.</h1>"
        "<p>Next: install it on your account. GitHub App user tokens only "
        "reach repositories where the app is installed.</p>"
        f'<p><a href="{html.escape(url)}">Install the app on GitHub</a></p>'
    )
    return _page("GitHub App created", body)


def _install_url(slug: str) -> str:
    return f"https://github.com/apps/{urllib.parse.quote(slug, safe='')}/installations/new"


def _save_metadata(info: dict) -> None:
    """Persist non-secret app metadata next to the hub OAuth client config."""
    import oauth_clients

    cfg = oauth_clients._read_cfg()
    cfg["hub_github_app"] = {
        "id": info.get("id"),
        "slug": info.get("slug"),
        "html_url": info.get("html_url"),
    }
    oauth_clients._write_cfg(cfg)


def _exchange(code: str) -> dict:
    url = _CONVERSIONS.format(code=urllib.parse.quote(code, safe=""))
    req = urllib.request.Request(
        url,
        data=b"",
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "subway",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    data = json.loads(raw.decode("utf-8") or "{}")
    if not isinstance(data, dict):
        raise ValueError("unexpected response from GitHub")
    return data


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "SubwayGitHubApp/1.0"

    def log_message(self, fmt, *args):  # keep loopback URLs out of stderr
        return

    def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _not_found(self) -> None:
        self._send(404, _page("Not found", "<h1>Not found</h1>"))

    def do_HEAD(self):
        # Never run the conversion (or any side effect) for a HEAD request.
        if urllib.parse.urlparse(self.path).path == "/":
            self._send(200, _form_page(self.server.subway_manifest, self.server.subway_state))
        else:
            self._not_found()

    def do_POST(self):
        length = self.headers.get("Content-Length") or "0"
        try:
            if int(length) > _MAX_BODY:
                self._send(413, _page("Too large", "<h1>Request too large</h1>"))
                return
        except ValueError:
            self._send(400, _page("Bad request", "<h1>Bad request</h1>"))
            return
        self._not_found()

    def do_GET(self):
        if len(self.path) > _MAX_PATH:
            self._send(414, _page("Too long", "<h1>URI too long</h1>"))
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send(200, _form_page(self.server.subway_manifest, self.server.subway_state))
            return
        if parsed.path == "/done":
            self._done(urllib.parse.parse_qs(parsed.query, keep_blank_values=True))
            return
        self._not_found()

    def _done(self, qs: dict) -> None:
        srv = self.server
        state = (qs.get("state") or [""])[0]
        code = (qs.get("code") or [""])[0]
        expected = srv.subway_state
        if not isinstance(state, str) or not hmac.compare_digest(state, expected):
            self._send(400, _page("Bad state", "<h1>State did not match.</h1>"))
            return
        if not code:
            self._send(400, _page("Missing code", "<h1>Missing authorization code.</h1>"))
            return
        if srv.subway_event.is_set():
            self._send(409, _page("Already used", "<h1>Already created.</h1>"))
            return

        result = {"ok": False, "error": "GitHub did not return app credentials"}
        try:
            data = _exchange(code)
            client_id = data.get("client_id")
            client_secret = data.get("client_secret")
            slug = data.get("slug")
            if not isinstance(client_id, str) or not client_id:
                raise ValueError("missing client_id")
            if not isinstance(client_secret, str) or not client_secret:
                raise ValueError("missing client_secret")
            if not isinstance(slug, str) or not slug:
                raise ValueError("missing slug")

            import oauth_clients
            oauth_clients.set_client("github", client_id, client_secret)
            _save_metadata({
                "id": data.get("id"),
                "slug": slug,
                "html_url": data.get("html_url"),
            })
            result = {"ok": True, "slug": slug, "install_url": _install_url(slug)}
        except Exception as e:  # no secret ever goes into the message
            result = {"ok": False, "error": f"GitHub App creation failed: {type(e).__name__}"}

        srv.subway_result = result
        if result.get("ok"):
            self._send(200, _install_page(str(result["slug"])))
        else:
            self._send(502, _page("Creation failed", "<h1>Could not create the GitHub App.</h1>"))
        srv.subway_event.set()


def create(open_browser: bool = True, timeout: float = 600, out=print) -> dict:
    """Run the manifest flow and return ``{ok, slug, install_url}`` or ``{ok: False, error}``."""
    emit = out if callable(out) else (lambda *_a, **_k: None)
    state = secrets.token_urlsafe(24)

    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    except OSError as e:
        return {"ok": False, "error": f"could not start loopback listener: {type(e).__name__}"}

    srv.daemon_threads = True
    port = int(srv.server_address[1])
    manifest = _manifest(port)
    srv.subway_state = state
    srv.subway_manifest = json.dumps(manifest, separators=(",", ":"))
    srv.subway_result = None
    srv.subway_event = threading.Event()

    page_url = f"http://127.0.0.1:{port}/"
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    thread.start()

    try:
        emit(f"Open this page to create your GitHub App:\n  {page_url}")
        if open_browser:
            try:
                import webbrowser
                webbrowser.open(page_url)
            except Exception:
                pass

        srv.subway_event.wait(max(0.1, float(timeout)))
        result = srv.subway_result or {"ok": False, "error": "GitHub App creation timed out"}
        if result.get("ok") and open_browser:
            try:
                import webbrowser
                webbrowser.open(result["install_url"])
            except Exception:
                pass
        if result.get("ok"):
            emit(f"Install the app on your account:\n  {result['install_url']}")
            emit("then run: subway mcp connect github")
        else:
            emit(str(result.get("error") or "GitHub App creation failed"))
        return result
    finally:
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass
        try:
            thread.join(2.0)
        except Exception:
            pass
