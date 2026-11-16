"""OAuth token storage and refresh handling for MCP servers."""

import win_lock
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import keychain_native
import oauth_discovery


RETRY_DELAY = 2
_SERVER_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_locks_guard = threading.Lock()
_thread_locks = {}


class OAuthTokenError(Exception):
    """Base error for OAuth token operations."""


class OAuthReuseDetected(OAuthTokenError):
    """Raised when a refresh token has already been used or revoked."""


class OAuthRefreshFailed(OAuthTokenError):
    """Raised when a token refresh fails."""


class OAuthRefreshTimeout(OAuthTokenError):
    """Raised when a refresh lock cannot be acquired in time."""


class OAuthDisconnected(OAuthTokenError):
    """Raised when the server has no usable OAuth connection."""


def _validate_server_key(server_key):
    if (
        not isinstance(server_key, str)
        or not _SERVER_KEY_RE.fullmatch(server_key)
        or server_key in (".", "..")
    ):
        raise ValueError("Invalid OAuth server key")


def load(server_key):
    _validate_server_key(server_key)
    secret = keychain_native.get_secret(keychain_native.SUBWAY_MCP_SERVICE, server_key)
    if secret is None:
        return None
    try:
        record = json.loads(secret.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OAuthTokenError("Stored OAuth token record is invalid") from exc
    if not isinstance(record, dict):
        raise OAuthTokenError("Stored OAuth token record is invalid")
    return record


def save(server_key, record):
    _validate_server_key(server_key)
    if not isinstance(record, dict):
        raise ValueError("OAuth token record must be a dictionary")
    try:
        secret = json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise OAuthTokenError("OAuth token record cannot be stored") from exc
    keychain_native.set_secret(keychain_native.SUBWAY_MCP_SERVICE, server_key, secret)


def forget(server_key):
    _validate_server_key(server_key)
    keychain_native.delete_secret(keychain_native.SUBWAY_MCP_SERVICE, server_key)


def public_view(record):
    return {
        "server": record.get("server"),
        "status": record.get("status"),
        "scopes": record.get("scopes"),
        "expires_at": record.get("expires_at"),
        "resource": record.get("resource"),
    }


def _lock_dir():
    directory = os.path.join(os.path.expanduser("~/.subway"), "locks")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


def _check_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme.lower() == "http"
            and parsed.hostname in ("127.0.0.1", "::1", "localhost")
        ):
            parsed.port
            return parsed
        return oauth_discovery._validate_url_security(url, "token endpoint")
    except (oauth_discovery.OAuthDiscoveryError, TypeError, ValueError) as exc:
        raise OAuthRefreshFailed("Token endpoint URL is not allowed") from exc


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_json_response(response):
    body = response.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise OAuthRefreshFailed("Token endpoint response is too large")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OAuthRefreshFailed("Token endpoint returned an invalid response") from exc
    if not isinstance(parsed, dict):
        raise OAuthRefreshFailed("Token endpoint returned an invalid response")
    return parsed


def _request_refresh(record, now):
    endpoint = record.get("token_endpoint")
    _check_url(endpoint)
    fields = {
        "grant_type": "refresh_token",
        "refresh_token": record.get("refresh_token"),
        "client_id": record.get("client_id"),
    }
    if record.get("client_auth") == "secret":
        import oauth_clients
        secret = oauth_clients.client_secret(str(record.get("server") or ""))
        if not secret:
            raise OAuthRefreshFailed("client secret missing from the Keychain")
        fields["client_secret"] = secret
    if record.get("resource") is not None:
        fields["resource"] = record["resource"]
    request = urllib.request.Request(
        endpoint,
        data=urllib.parse.urlencode(fields).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=10) as response:
            status = response.getcode()
            payload = _read_json_response(response)
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            payload = _read_json_response(exc)
        except OAuthRefreshFailed:
            payload = {}
    if status == 400 and payload.get("error") == "invalid_grant":
        raise OAuthReuseDetected("OAuth refresh token was rejected")
    if status != 200:
        raise OAuthRefreshFailed("Token endpoint refresh failed")
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthRefreshFailed("Token endpoint response has no access token")
    expires_in = payload.get("expires_in") or 3600
    try:
        expires_in = int(expires_in)
    except (TypeError, ValueError, OverflowError) as exc:
        raise OAuthRefreshFailed("Token endpoint returned an invalid expiration") from exc
    new_record = dict(record)
    new_record["access_token"] = access_token
    refresh_token = payload.get("refresh_token")
    if isinstance(refresh_token, str) and refresh_token:
        new_record["refresh_token"] = refresh_token
    new_record["expires_at"] = now() + expires_in
    if "scope" in payload:
        scope = payload["scope"]
        if not isinstance(scope, str):
            raise OAuthRefreshFailed("Token endpoint returned invalid scopes")
        new_record["scopes"] = scope.split()
    new_record["status"] = "connected"
    return new_record


def _mark_disconnected(server_key, record):
    disconnected = dict(record)
    disconnected["status"] = "disconnected"
    disconnected["access_token"] = None
    disconnected["refresh_token"] = None
    save(server_key, disconnected)


def _get_thread_lock(server_key):
    with _locks_guard:
        return _thread_locks.setdefault(server_key, threading.Lock())


def ensure_fresh(server_key, *, skew=60, lock_timeout=30.0, now=time.time):
    _validate_server_key(server_key)
    record = load(server_key)
    if record is None or record.get("status") == "disconnected":
        raise OAuthDisconnected("OAuth connection is disconnected")
    exp = record.get("expires_at")
    if exp is None:
        if skew < 10**9:
            return record
    elif exp > now() + skew:
        return record

    deadline = time.monotonic() + lock_timeout
    thread_lock = _get_thread_lock(server_key)
    if not thread_lock.acquire(blocking=False):
        while time.monotonic() < deadline:
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            if thread_lock.acquire(blocking=False):
                break
        else:
            raise OAuthRefreshTimeout("Timed out waiting for OAuth refresh lock")
    lock_file = None
    try:
        lock_directory = _lock_dir()
        os.makedirs(lock_directory, mode=0o700, exist_ok=True)
        os.chmod(lock_directory, 0o700)
        lock_path = os.path.join(lock_directory, f"mcp_refresh_{server_key}.lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        lock_file = os.fdopen(fd, "a+b")
        os.chmod(lock_path, 0o600)
        while True:
            try:
                win_lock.lock(lock_file.fileno(), nonblock=True)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OAuthRefreshTimeout("Timed out waiting for OAuth refresh lock")
                time.sleep(min(0.05, remaining))

        record = load(server_key)
        if record is None or record.get("status") == "disconnected":
            raise OAuthDisconnected("OAuth connection is disconnected")
        if record.get("expires_at", 0) > now() + skew:
            return record
        if not record.get("refresh_token"):
            _mark_disconnected(server_key, record)
            if record.get("expires_at") is None:
                raise OAuthRefreshFailed("OAuth connection has no refresh token")
            raise OAuthDisconnected("OAuth connection has no refresh token")

        try:
            _check_url(record.get("token_endpoint"))
        except OAuthRefreshFailed:
            _mark_disconnected(server_key, record)
            raise

        for attempt in range(2):
            try:
                refreshed = _request_refresh(record, now)
                save(server_key, refreshed)
                return refreshed
            except OAuthReuseDetected:
                _mark_disconnected(server_key, record)
                raise
            except OAuthRefreshFailed:
                if attempt == 0:
                    time.sleep(RETRY_DELAY)
                    continue
                _mark_disconnected(server_key, record)
                raise
            except (OSError, urllib.error.URLError, TimeoutError):
                if attempt == 0:
                    time.sleep(RETRY_DELAY)
                    continue
                _mark_disconnected(server_key, record)
                raise OAuthRefreshFailed("Token endpoint refresh failed") from None
    finally:
        if lock_file is not None:
            try:
                win_lock.unlock(lock_file.fileno())
            finally:
                lock_file.close()
        thread_lock.release()
