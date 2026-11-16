"""Best-effort OAuth token revocation and local disconnect (RFC 7009)."""

from __future__ import annotations

import ipaddress
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _is_loopback_host(host: Optional[str]) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_allowed_revocation_endpoint(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme == "https":
        return True
    if parsed.scheme == "http" and _is_loopback_host(parsed.hostname):
        return True
    return False


def _post_revoke(url: str, token: str, token_type_hint: str, client_id: Optional[str], timeout: float) -> bool:
    data = {"token": token, "token_type_hint": token_type_hint}
    if client_id:
        data["client_id"] = client_id
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return getattr(resp, "status", None) == 200
    except urllib.error.HTTPError as exc:
        # No redirects; treat any HTTP error response as a non-success revocation.
        return exc.code == 200
    except Exception:
        return False


def disconnect(server_key: str, *, timeout: float = 10.0) -> Dict[str, Any]:
    import oauth_tokens
    import mcp_grants
    import oauth_discovery

    try:
        record = oauth_tokens.load(server_key)
    except Exception:
        record = None
    was_connected = record is not None

    revoked_at_server = False
    if isinstance(record, dict):
        try:
            resource = record.get("resource")
            if isinstance(resource, str) and resource:
                pr = oauth_discovery.discover_protected_resource(resource)
                auth_servers = pr.get("authorization_servers") or []
                if isinstance(auth_servers, list) and auth_servers:
                    issuer = auth_servers[0]
                    if isinstance(issuer, str) and issuer:
                        as_meta = oauth_discovery.discover_authorization_server(issuer)
                        rev_ep = as_meta.get("revocation_endpoint")
                        if isinstance(rev_ep, str) and rev_ep and _is_allowed_revocation_endpoint(rev_ep):
                            client_id = record.get("client_id")
                            if not isinstance(client_id, str):
                                client_id = None
                            refresh_token = record.get("refresh_token")
                            access_token = record.get("access_token")
                            if isinstance(refresh_token, str) and refresh_token:
                                if _post_revoke(rev_ep, refresh_token, "refresh_token", client_id, timeout):
                                    revoked_at_server = True
                            if isinstance(access_token, str) and access_token:
                                if _post_revoke(rev_ep, access_token, "access_token", client_id, timeout):
                                    revoked_at_server = True
        except Exception:
            # Best-effort: discovery, metadata, or revocation problems must not escape.
            pass

    try:
        oauth_tokens.forget(server_key)
    except Exception:
        # forget already ignores missing keys; swallow anything else to stay idempotent.
        pass

    try:
        grants_revoked = mcp_grants.revoke_grants_for(server_key)
    except Exception:
        grants_revoked = 0

    return {
        "server": server_key,
        "revoked_at_server": revoked_at_server,
        "grants_revoked": grants_revoked,
        "was_connected": was_connected,
    }
