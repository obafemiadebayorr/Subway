"""OAuth discovery for protected resources (RFC 9728 / MCP) and authorization servers (RFC 8414 / OIDC).

Follows strict security controls:
- No logging/printing of response bodies or tokens.
- Timeout 10s.
- Max response size 1 MB.
- Refuses cross-origin redirects.
- Requires HTTPS unless loopback (127.0.0.1, ::1, localhost).
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

MAX_RESPONSE_BYTES = 1024 * 1024  # 1 MB
TIMEOUT_SECONDS = 10.0
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class OAuthDiscoveryError(Exception):
    """Raised when OAuth discovery fails or violates security policy."""
    pass


def _is_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    h = host.split(":")[0].strip("[]").lower()
    return h in LOOPBACK_HOSTS


def _validate_url_security(url: str, context: str = "URL") -> urllib.parse.ParseResult:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as e:
        raise OAuthDiscoveryError(f"Invalid {context}: {url!r}") from e

    scheme = parsed.scheme.lower()
    host = parsed.hostname

    if not scheme or not host:
        raise OAuthDiscoveryError(f"Invalid {context} (missing scheme or host): {url!r}")

    if scheme == "https":
        return parsed
    elif scheme == "http":
        if _is_loopback(host):
            return parsed
        raise OAuthDiscoveryError(f"Insecure HTTP {context} forbidden for non-loopback host {host!r}: {url!r}")
    else:
        raise OAuthDiscoveryError(f"Unsupported scheme {scheme!r} for {context}: {url!r}")


class StrictRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that prevents cross-origin redirects and validates HTTPS/loopback."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Validate target URL
        _validate_url_security(newurl, context="redirect target")

        orig_parsed = urllib.parse.urlparse(req.full_url)
        new_parsed = urllib.parse.urlparse(newurl)

        orig_port = orig_parsed.port or (443 if orig_parsed.scheme == "https" else 80)
        new_port = new_parsed.port or (443 if new_parsed.scheme == "https" else 80)

        orig_origin = (orig_parsed.scheme.lower(), orig_parsed.hostname.lower() if orig_parsed.hostname else "", orig_port)
        new_origin = (new_parsed.scheme.lower(), new_parsed.hostname.lower() if new_parsed.hostname else "", new_port)

        if orig_origin != new_origin:
            raise OAuthDiscoveryError(f"Cross-origin redirect refused: from {req.full_url!r} to {newurl!r}")

        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_json(url: str) -> Dict[str, Any]:
    _validate_url_security(url, context="discovery URL")

    opener = urllib.request.build_opener(StrictRedirectHandler())
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Subway-OAuthDiscovery/1.0",
        },
    )

    try:
        with opener.open(req, timeout=TIMEOUT_SECONDS) as resp:
            # Check content length header if present
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit() and int(cl) > MAX_RESPONSE_BYTES:
                raise OAuthDiscoveryError(f"Response exceeds maximum allowed size of {MAX_RESPONSE_BYTES} bytes")

            # Read with strict cap + 1 to detect oversize
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise OAuthDiscoveryError(f"Response exceeds maximum allowed size of {MAX_RESPONSE_BYTES} bytes")

            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception as e:
                raise OAuthDiscoveryError(f"Invalid JSON returned from {url}") from e

            if not isinstance(data, dict):
                raise OAuthDiscoveryError(f"Expected JSON object from {url}")
            return data

    except urllib.error.HTTPError as e:
        raise OAuthDiscoveryError(f"HTTP {e.code} while fetching {url}") from e
    except urllib.error.URLError as e:
        raise OAuthDiscoveryError(f"Network error while fetching {url}") from e
    except OAuthDiscoveryError:
        raise
    except Exception as e:
        raise OAuthDiscoveryError(f"Failed to fetch {url}: {e}") from e


def _extract_resource_metadata_url(www_authenticate: str) -> Optional[str]:
    if not www_authenticate:
        return None
    # match resource_metadata="<url>" or resource_metadata=<token>
    m = re.search(r'resource_metadata="([^"]+)"', www_authenticate)
    if m:
        return m.group(1)
    m = re.search(r'resource_metadata=([^\s,]+)', www_authenticate)
    if m:
        return m.group(1)
    return None


def discover_protected_resource(server_url: str, www_authenticate: str = "") -> dict:
    """RFC 9728 / MCP protected resource discovery."""
    _validate_url_security(server_url, context="server_url")

    target_url = _extract_resource_metadata_url(www_authenticate)
    candidates: List[str] = []

    if target_url:
        candidates.append(target_url)
    else:
        parsed = urllib.parse.urlparse(server_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip("/")

        # Path-inserted candidate
        if path:
            candidates.append(f"{origin}/.well-known/oauth-protected-resource{path}")
        # Root fallback
        candidates.append(f"{origin}/.well-known/oauth-protected-resource")

    last_error: Optional[Exception] = None
    for cand in candidates:
        try:
            doc = _fetch_json(cand)
            auth_servers = doc.get("authorization_servers")
            if not isinstance(auth_servers, list) or len(auth_servers) == 0:
                raise OAuthDiscoveryError(f"Empty or missing authorization_servers at {cand}")

            scopes = doc.get("scopes_supported", [])
            if not isinstance(scopes, list):
                scopes = []

            resource = doc.get("resource", server_url)

            return {
                "resource": resource,
                "authorization_servers": auth_servers,
                "scopes_supported": scopes,
            }
        except OAuthDiscoveryError as e:
            last_error = e
            # If explicit resource_metadata target was provided, don't fallback
            if target_url:
                break
        except Exception as e:
            last_error = e
            if target_url:
                break

    raise OAuthDiscoveryError(f"OAuth protected resource discovery failed for {server_url}: {last_error}")


def discover_authorization_server(issuer: str) -> dict:
    """RFC 8414 & OIDC discovery."""
    _validate_url_security(issuer, context="issuer")

    parsed = urllib.parse.urlparse(issuer)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")

    candidates: List[str] = []
    # 1. <origin>/.well-known/oauth-authorization-server<path>
    candidates.append(f"{origin}/.well-known/oauth-authorization-server{path}")
    # 2. <origin>/.well-known/openid-configuration<path>
    candidates.append(f"{origin}/.well-known/openid-configuration{path}")
    # 3. <issuer>/.well-known/openid-configuration
    issuer_base = issuer.rstrip("/")
    candidates.append(f"{issuer_base}/.well-known/openid-configuration")

    # Remove duplicates preserving order
    seen = set()
    deduped_candidates = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            deduped_candidates.append(c)

    last_error: Optional[Exception] = None
    for cand in deduped_candidates:
        try:
            doc = _fetch_json(cand)

            # Check issuer match (trailing slash normalised)
            doc_issuer = doc.get("issuer")
            if not isinstance(doc_issuer, str) or doc_issuer.rstrip("/") != issuer.rstrip("/"):
                raise OAuthDiscoveryError("issuer mismatch")

            auth_ep = doc.get("authorization_endpoint")
            token_ep = doc.get("token_endpoint")

            if not auth_ep or not isinstance(auth_ep, str):
                raise OAuthDiscoveryError("Missing authorization_endpoint")
            if not token_ep or not isinstance(token_ep, str):
                raise OAuthDiscoveryError("Missing token_endpoint")

            # Validate security of endpoints
            _validate_url_security(auth_ep, context="authorization_endpoint")
            _validate_url_security(token_ep, context="token_endpoint")

            reg_ep = doc.get("registration_endpoint")
            if reg_ep:
                if not isinstance(reg_ep, str):
                    raise OAuthDiscoveryError("Invalid registration_endpoint")
                _validate_url_security(reg_ep, context="registration_endpoint")
            else:
                reg_ep = None

            rev_ep = doc.get("revocation_endpoint")
            if rev_ep:
                if not isinstance(rev_ep, str):
                    raise OAuthDiscoveryError("Invalid revocation_endpoint")
                _validate_url_security(rev_ep, context="revocation_endpoint")
            else:
                rev_ep = None

            cc_methods = doc.get("code_challenge_methods_supported")
            if not isinstance(cc_methods, list) or "S256" not in cc_methods:
                raise OAuthDiscoveryError("PKCE S256 is required in code_challenge_methods_supported")

            return {
                "issuer": doc_issuer,
                "authorization_endpoint": auth_ep,
                "token_endpoint": token_ep,
                "registration_endpoint": reg_ep,
                "revocation_endpoint": rev_ep,
                "code_challenge_methods_supported": cc_methods,
            }
        except OAuthDiscoveryError as e:
            # If it's an issuer mismatch or bad endpoint on a found doc, raise immediately or track
            last_error = e
            if "issuer mismatch" in str(e) or "PKCE S256" in str(e) or "endpoint" in str(e):
                raise
        except Exception as e:
            last_error = e

    raise OAuthDiscoveryError(f"OAuth authorization server discovery failed for {issuer}: {last_error}")
