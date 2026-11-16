"""OAuth connect flow for Subway MCP sign-in (step 50c-4)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import http.server
import json
import os
import secrets
import socket
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import oauth_discovery


class OAuthConnectError(Exception):
    """Base error for OAuth connect failures."""


class OAuthConnectTimeout(OAuthConnectError):
    """Raised when the connect flow times out."""


class OAuthStateMismatch(OAuthConnectError):
    """Raised when the callback state does not match."""


def _state_dir() -> str:
    return os.path.expanduser("~/.subway")


def _save_token(server_key: str, record: dict) -> None:
    import oauth_tokens

    oauth_tokens.save(server_key, record)


def _clients_path() -> str:
    return os.path.join(_state_dir(), "mcp_clients.json")


def _load_clients() -> dict:
    path = _clients_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _atomic_write(path: str, data: str, mode: int = 0o600) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _save_clients(clients: dict) -> None:
    _atomic_write(_clients_path(), json.dumps(clients, separators=(",", ":")), 0o600)


def _b64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    if len(verifier) < 43:
        verifier = (verifier + secrets.token_urlsafe(64))[:43]
    if len(verifier) > 128:
        verifier = verifier[:128]
    challenge = _b64url_no_pad(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _is_loopback_host(host: str) -> bool:
    h = (host or "").lower()
    return h in ("localhost", "127.0.0.1", "::1") or h.startswith("127.")


def _validate_token_endpoint(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname or ""
    if scheme == "https":
        return
    if scheme == "http" and _is_loopback_host(host):
        return
    raise OAuthConnectError("token_endpoint must be https unless loopback")


def _read_body_limited(resp, limit: int = 1024 * 1024) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = resp.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise OAuthConnectError("token response too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _post_json(url: str, payload: dict, timeout: float = 10.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _read_body_limited(resp)
    except Exception as e:
        raise OAuthConnectError(f"client registration failed: {e}") from e
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception as e:
        raise OAuthConnectError("client registration returned invalid JSON") from e
    if not isinstance(obj, dict):
        raise OAuthConnectError("client registration returned invalid response")
    return obj


def _post_form_no_redirect(url: str, payload: dict, timeout: float = 10.0) -> dict:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
            raise OAuthConnectError("token endpoint redirect not allowed")

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = _read_body_limited(resp)
    except OAuthConnectError:
        raise
    except urllib.error.HTTPError as e:
        try:
            body = _read_body_limited(e)
            obj = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            obj = {}
        err = obj.get("error") if isinstance(obj, dict) else None
        raise OAuthConnectError(f"token exchange failed: {err or e.code}") from e
    except Exception as e:
        raise OAuthConnectError(f"token exchange failed: {e}") from e
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception as e:
        raise OAuthConnectError("token endpoint returned invalid JSON") from e
    if not isinstance(obj, dict):
        raise OAuthConnectError("token endpoint returned invalid response")
    return obj


_STATIC_OK = (
    "<!doctype html><html><head><meta charset=\"utf-8\"><title>Subway</title></head>"
    "<body>Subway is connected. You can close this tab.</body></html>"
)
_STATIC_ERR = (
    "<!doctype html><html><head><meta charset=\"utf-8\"><title>Subway</title></head>"
    "<body>Sign-in failed. Return to Subway.</body></html>"
)


def _own_secret(server_key: str) -> str:
    import oauth_clients
    secret = oauth_clients.client_secret(server_key)
    if not secret:
        raise OAuthConnectError("your OAuth app's client secret is missing from the Keychain")
    return secret


@dataclass
class _FlowState:
    server_key: str
    server_url: str
    token_endpoint: str
    client_id: str
    redirect_uri: str
    verifier: str
    state: str
    scopes: list[str]
    resource: str
    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    result: Optional[dict] = None
    error: Optional[BaseException] = None
    handled: bool = False
    httpd: Any = None
    client_auth: str = ""


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server_version = "SubwayOAuth/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return

    def _send_page(self, ok: bool) -> None:
        body = (_STATIC_OK if ok else _STATIC_ERR).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _send_404(self) -> None:
        body = b"Not Found"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self) -> None:  # noqa: N802
        flow: _FlowState = self.server.flow  # type: ignore[attr-defined]
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self._send_404()
            return

        with flow.lock:
            if flow.handled:
                self._send_page(False)
                flow.done.set()
                threading.Thread(target=self.server.server_close, daemon=True).start()
                return
            flow.handled = True

        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        try:
            if "error" in params:
                raise OAuthConnectError("authorization failed")
            state_vals = params.get("state", [])
            code_vals = params.get("code", [])
            state = state_vals[0] if state_vals else ""
            code = code_vals[0] if code_vals else ""
            if not state or not code or not hmac.compare_digest(state, flow.state):
                raise OAuthStateMismatch("state mismatch")
            _validate_token_endpoint(flow.token_endpoint)
            token = _post_form_no_redirect(
                flow.token_endpoint,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": flow.redirect_uri,
                    "client_id": flow.client_id,
                    "code_verifier": flow.verifier,
                    **({"client_secret": _own_secret(flow.server_key)} if flow.client_auth == "secret" else {}),
                    "resource": flow.resource,
                },
                timeout=10.0,
            )
            if "error" in token:
                raise OAuthConnectError("token exchange failed")
            access_token = token.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise OAuthConnectError("missing access_token")
            refresh_token = token.get("refresh_token")
            if refresh_token is not None and not isinstance(refresh_token, str):
                refresh_token = None
            expires_in = token.get("expires_in", 3600)
            try:
                expires_in_val = int(expires_in)
            except Exception:
                expires_in_val = 3600
            if expires_in_val <= 0:
                expires_in_val = 3600
            expires_at = int(time.time()) + expires_in_val
            scopes_val = token.get("scope")
            if isinstance(scopes_val, str) and scopes_val.strip():
                scopes = scopes_val.split()
            else:
                scopes = list(flow.scopes)
            token_ep = token.get("token_endpoint") if isinstance(token.get("token_endpoint"), str) else flow.token_endpoint
            client_id = token.get("client_id") if isinstance(token.get("client_id"), str) else flow.client_id
            record = {
                "server": flow.server_key,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
                "scopes": scopes,
                "token_endpoint": token_ep,
                "client_id": client_id,
                "resource": flow.resource,
                "status": "connected",
                "client_auth": flow.client_auth,
            }
            _save_token(flow.server_key, record)
            public = {
                "server": flow.server_key,
                "status": "connected",
                "scopes": scopes,
                "expires_at": expires_at,
                "resource": flow.resource,
            }
            with flow.lock:
                flow.result = public
        except BaseException as e:  # noqa: BLE001
            with flow.lock:
                flow.error = e
            self._send_page(False)
        else:
            self._send_page(True)
        finally:
            flow.done.set()
            threading.Thread(target=self.server.server_close, daemon=True).start()


class ConnectSession:
    def __init__(self, flow: _FlowState, thread: threading.Thread):
        self._flow = flow
        self._thread = thread
        self.authorize_url = flow.state and ""  # will be set properly
        self.scopes = list(flow.scopes)
        self.redirect_uri = flow.redirect_uri

    def wait(self, timeout: Optional[float] = None) -> dict:
        if timeout is None:
            remaining = self._flow.deadline - time.time()
        else:
            remaining = min(timeout, self._flow.deadline - time.time())
        if remaining <= 0:
            self.cancel()
            raise OAuthConnectTimeout("connect timed out")
        finished = self._flow.done.wait(remaining)
        if not finished:
            self.cancel()
            raise OAuthConnectTimeout("connect timed out")
        err = self._flow.error
        if err is not None:
            if isinstance(err, OAuthConnectError):
                raise err
            raise OAuthConnectError(str(err)) from err
        if self._flow.result is None:
            raise OAuthConnectError("connect failed")
        return dict(self._flow.result)

    def cancel(self) -> None:
        self._flow.done.set()
        try:
            if self._flow.httpd is not None:
                self._flow.httpd.server_close()
        except Exception:
            pass


def _register_client(registration_endpoint: str, redirect_uri: str) -> str:
    payload = {
        "client_name": "Subway",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    obj = _post_json(registration_endpoint, payload, timeout=10.0)
    if "client_secret" in obj and obj.get("client_secret"):
        raise OAuthConnectError(
            "server issued a client secret; confidential clients are not supported yet"
        )
    client_id = obj.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise OAuthConnectError("client registration missing client_id")
    return client_id


def _bind_listener(port: int) -> http.server.HTTPServer:
    httpd = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    # ensure quick shutdown
    httpd.timeout = 0.5
    return httpd


def start_connect(
    server_key: str,
    server_url: str,
    *,
    scopes: Optional[list[str]] = None,
    timeout: float = 120.0,
) -> ConnectSession:
    pr = oauth_discovery.discover_protected_resource(server_url)
    auth_servers = pr.get("authorization_servers") or []
    if not auth_servers:
        raise OAuthConnectError("no authorization_servers discovered")
    issuer = auth_servers[0]
    as_meta = oauth_discovery.discover_authorization_server(issuer)
    registration_endpoint = as_meta.get("registration_endpoint")
    authorization_endpoint = as_meta["authorization_endpoint"]
    token_endpoint = as_meta["token_endpoint"]

    resource = pr.get("resource", server_url)
    pr_scopes = pr.get("scopes_supported") or []
    if scopes is not None:
        use_scopes = list(scopes)
    elif isinstance(pr_scopes, list) and pr_scopes:
        use_scopes = list(pr_scopes)
    else:
        use_scopes = []

    import oauth_clients
    own = oauth_clients.get_client(server_key)
    clients = {} if own else _load_clients()
    issuer_cache = clients.get(issuer) if isinstance(clients.get(issuer), dict) else None
    cached_client_id = issuer_cache.get("client_id") if issuer_cache else None
    cached_redirect = issuer_cache.get("redirect_uri") if issuer_cache else None
    cached_port = None
    if isinstance(cached_redirect, str):
        try:
            pu = urllib.parse.urlparse(cached_redirect)
            if pu.hostname in ("127.0.0.1", "localhost") and pu.port:
                cached_port = int(pu.port)
        except Exception:
            cached_port = None

    httpd = None
    client_id = None
    redirect_uri = None
    if isinstance(cached_client_id, str) and cached_client_id and cached_port:
        try:
            httpd = _bind_listener(cached_port)
            client_id = cached_client_id
            redirect_uri = cached_redirect
        except OSError:
            httpd = None

    if httpd is None:
        # need bind (and possibly register)
        try:
            httpd = _bind_listener(0)
        except OSError as e:
            raise OAuthConnectError(f"failed to bind loopback listener: {e}") from e
        port = httpd.server_address[1]
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        if own:
            client_id = own["client_id"]
        elif isinstance(cached_client_id, str) and cached_client_id and issuer_cache and issuer_cache.get("redirect_uri") == redirect_uri:
            client_id = cached_client_id
        else:
            if not registration_endpoint:
                try:
                    httpd.server_close()
                except Exception:
                    pass
                import oauth_device
                dev = oauth_device.builtin_client(issuer, as_meta)
                if dev:
                    return oauth_device.start_device(server_key, as_meta, dev, scopes=use_scopes,
                                                     resource=resource, timeout=timeout)
                raise OAuthConnectError(
                    f"{server_key} does not let apps register themselves. Add your own OAuth app: subway mcp client {server_key} --client-id ID"
                )
            client_id = _register_client(registration_endpoint, redirect_uri)
            clients[issuer] = {"client_id": client_id, "redirect_uri": redirect_uri}
            _save_clients(clients)

    verifier, challenge = _make_pkce()
    state = secrets.token_urlsafe(32)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": resource,
    }
    if use_scopes:
        params["scope"] = " ".join(use_scopes)
    authorize_url = authorization_endpoint + ("&" if "?" in authorization_endpoint else "?") + urllib.parse.urlencode(params)

    deadline = time.time() + float(timeout)
    flow = _FlowState(
        server_key=server_key,
        server_url=server_url,
        token_endpoint=token_endpoint,
        client_id=client_id,
        redirect_uri=redirect_uri,
        verifier=verifier,
        state=state,
        scopes=list(use_scopes),
        resource=resource,
        deadline=deadline,
    )
    httpd.flow = flow  # type: ignore[attr-defined]
    flow.httpd = httpd
    flow.client_auth = "secret" if own and own.get("has_secret") else ""

    def _serve() -> None:
        try:
            while not flow.done.is_set() and time.time() < flow.deadline:
                httpd.handle_request()
            if not flow.done.is_set():
                with flow.lock:
                    if flow.error is None and not flow.handled:
                        flow.error = OAuthConnectTimeout("connect timed out")
                flow.done.set()
        except Exception as e:
            with flow.lock:
                if flow.error is None:
                    flow.error = e
            flow.done.set()
        finally:
            try:
                httpd.server_close()
            except Exception:
                pass

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()

    session = ConnectSession(flow, thread)
    session.authorize_url = authorize_url
    return session
