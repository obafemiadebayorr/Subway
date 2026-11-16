"""Device sign-in (RFC 8628) with Subway's own published app ID, for servers that don't let apps
register themselves (GitHub). Device flow needs no client secret."""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oauth_builtin_clients.json")
_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_LIMIT = 1024 * 1024


def builtin_client(issuer, as_meta):
    """Subway's own app for this issuer, if it has one and the server offers device sign-in."""
    if not isinstance(as_meta, dict) or not as_meta.get("device_authorization_endpoint"):
        return None
    try:
        with open(_DATA, encoding="utf-8") as fh:
            ent = (json.load(fh) or {}).get(issuer)
    except Exception:
        return None
    if isinstance(ent, dict) and isinstance(ent.get("client_id"), str) and ent["client_id"].strip():
        return {"client_id": ent["client_id"].strip()}
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _post(url, fields, timeout=10.0):
    import oauth_connect
    oauth_connect._validate_token_endpoint(url)
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode("utf-8"), method="POST",
                                 headers={"Accept": "application/json",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    try:
        resp = urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        resp = e
    body = resp.read(_LIMIT + 1)
    if len(body) > _LIMIT:
        raise oauth_connect.OAuthConnectError("response too large")
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception:
        raise oauth_connect.OAuthConnectError(f"unreadable response from {urllib.parse.urlparse(url).hostname}") from None
    if not isinstance(obj, dict):
        raise oauth_connect.OAuthConnectError("unreadable response")
    return obj


class DeviceSession:
    """Same shape as oauth_connect.ConnectSession, plus user_code."""

    def __init__(self, server_key, token_endpoint, client_id, scopes, resource, deadline, start):
        self.verification_uri = start["verification_uri"]
        vc = start.get("verification_uri_complete")
        self.authorize_url = vc if isinstance(vc, str) and vc.startswith("https://") else self.verification_uri
        self.user_code = start["user_code"]
        self.scopes = list(scopes or [])
        self.redirect_uri = ""
        self._server_key = server_key
        self._token_endpoint = token_endpoint
        self._client_id = client_id
        self._device_code = start["device_code"]
        self._resource = resource
        self._interval = max(1, int(start.get("interval") or 5))
        self._deadline = deadline
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def wait(self, timeout=None):
        import oauth_connect
        end = self._deadline if timeout is None else min(self._deadline, time.time() + float(timeout))
        interval = self._interval
        while True:
            if self._cancel.wait(interval):
                raise oauth_connect.OAuthConnectError("sign-in cancelled")
            if time.time() > end:
                raise oauth_connect.OAuthConnectTimeout("connect timed out")
            obj = _post(self._token_endpoint, {"client_id": self._client_id, "device_code": self._device_code,
                                               "grant_type": _GRANT})
            err = obj.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            if err == "access_denied":
                raise oauth_connect.OAuthConnectError("you declined the sign-in")
            if err == "expired_token":
                raise oauth_connect.OAuthConnectTimeout("the code expired; connect again")
            if err:
                raise oauth_connect.OAuthConnectError(f"sign-in failed: {str(err)[:80]}")
            tok = obj.get("access_token")
            if not isinstance(tok, str) or not tok:
                raise oauth_connect.OAuthConnectError("no access token returned")
            if str(obj.get("token_type") or "bearer").lower() != "bearer":
                raise oauth_connect.OAuthConnectError("unsupported token type")
            exp = obj.get("expires_in")
            rt = obj.get("refresh_token")
            scopes = str(obj.get("scope") or "").replace(",", " ").split() or list(self.scopes)
            record = {
                "server": self._server_key,
                "access_token": tok,
                "refresh_token": rt if isinstance(rt, str) and rt else None,
                "expires_at": time.time() + float(exp) if isinstance(exp, (int, float)) and exp > 0 else None,
                "scopes": scopes,
                "token_endpoint": self._token_endpoint,
                "client_id": self._client_id,
                "resource": self._resource,
                "status": "connected",
                "client_auth": "",
            }
            oauth_connect._save_token(self._server_key, record)
            return {"server": self._server_key, "status": "connected", "scopes": scopes,
                    "expires_at": record["expires_at"]}


def start_device(server_key, as_meta, client, scopes=None, resource="", timeout=300.0):
    import oauth_connect
    fields = {"client_id": client["client_id"]}
    if scopes:
        fields["scope"] = " ".join(scopes)
    start = _post(as_meta["device_authorization_endpoint"], fields)
    if start.get("error"):
        raise oauth_connect.OAuthConnectError(f"device sign-in refused: {str(start['error'])[:80]}")
    for k in ("device_code", "user_code", "verification_uri"):
        if not isinstance(start.get(k), str) or not start[k]:
            raise oauth_connect.OAuthConnectError("device sign-in: incomplete response")
    if urllib.parse.urlparse(start["verification_uri"]).scheme != "https":
        raise oauth_connect.OAuthConnectError("device sign-in: verification page is not https")
    life = float(start.get("expires_in") or timeout)
    deadline = time.time() + min(float(timeout), life)
    return DeviceSession(server_key, as_meta["token_endpoint"], client["client_id"], scopes, resource, deadline, start)
