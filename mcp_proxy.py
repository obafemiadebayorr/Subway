"""Token-injecting MCP proxy (step 50c-5b).

Workers call /api/mcp/proxy/<server> with a capability token; this proxy
validates the grant, enforces the publish/delete gate, swaps in the real
bearer and forwards to the remote MCP endpoint. The cap token and the real
bearer must never appear in logs, audits, errors or tracebacks.
"""

from __future__ import annotations

import io
import json
import re
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

_LOOPBACK = {"127.0.0.1", "::1"}
_SERVER_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_COPY_REQ_HEADERS = (
    "Content-Type",
    "Accept",
    "Mcp-Session-Id",
    "MCP-Protocol-Version",
    "Last-Event-ID",
)
_COPY_RESP_HEADERS = ("Content-Type", "Mcp-Session-Id", "MCP-Protocol-Version")
_MAX_BODY = 4 * 1024 * 1024
_MAX_RESP = 16 * 1024 * 1024


class _ProxyError(Exception):
    def __init__(self, status, body, rpc_id=None, rpc=False):
        self.status = status
        self.body = body
        self.rpc_id = rpc_id
        self.rpc = rpc


def _send_json(handler, status, obj):
    data = json.dumps(obj).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _send_rpc_deny(handler, rpc_id, server, tool):
    obj = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": -32001,
            "message": f"the user did not allow {server}.{tool}",
        },
    }
    _send_json(handler, 200, obj)


def _server_from_path(path):
    prefix = "/api/mcp/proxy/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix):]
    # allow optional trailing slash but no extra segments
    if rest.endswith("/"):
        rest = rest[:-1]
    if not rest or "/" in rest or not _SERVER_RE.match(rest):
        return None
    return rest


def _read_body(handler, method):
    if method != "POST":
        return b""
    raw_len = handler.headers.get("Content-Length")
    if raw_len is None:
        return b""
    try:
        length = int(raw_len)
    except Exception:
        raise _ProxyError(400, {"error": "invalid Content-Length"})
    if length < 0:
        raise _ProxyError(400, {"error": "invalid Content-Length"})
    if length > _MAX_BODY:
        raise _ProxyError(413, {"error": "request too large"})
    try:
        return handler.rfile.read(length) or b""
    except Exception:
        raise _ProxyError(400, {"error": "failed to read body"})


def _parse_json_body(body):
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def _tool_calls(parsed):
    """Return list of (rpc_id, tool, args, obj) for tools/call elements to gate."""
    calls = []
    if isinstance(parsed, dict):
        if parsed.get("method") == "tools/call":
            params = parsed.get("params") or {}
            if isinstance(params, dict):
                calls.append((parsed.get("id"), params.get("name"), params.get("arguments"), parsed))
    elif isinstance(parsed, list):
        for elem in parsed:
            if isinstance(elem, dict) and elem.get("method") == "tools/call":
                params = elem.get("params") or {}
                if isinstance(params, dict):
                    calls.append((elem.get("id"), params.get("name"), params.get("arguments"), elem))
    return calls


def _find_server_spec(project, server):
    import mcp_hub

    servers = mcp_hub.discover_servers(project or "") or {}
    # Match by key or spec name.
    spec = servers.get(server)
    if spec is None:
        for s in servers.values():
            if isinstance(s, dict) and s.get("name") == server:
                spec = s
                break
    if spec is None:
        return None
    if not isinstance(spec, dict) or not spec.get("url"):
        return None
    return spec


def _real_bearer(server, spec_url):
    import mcp_hub
    import oauth_tokens

    try:
        rec = oauth_tokens.load(server)
    except Exception:
        rec = None
    if isinstance(rec, dict) and rec.get("status") == "connected":
        try:
            fresh = oauth_tokens.ensure_fresh(server)
        except Exception:
            fresh = None
        if isinstance(fresh, dict):
            tok = fresh.get("access_token")
            if tok:
                return tok, True
    try:
        hdr = mcp_hub._oauth_header(spec_url) or {}
    except Exception:
        hdr = {}
    if isinstance(hdr, dict):
        auth = hdr.get("Authorization") or hdr.get("authorization")
        if isinstance(auth, str) and auth.lower().startswith("bearer "):
            return auth.split(" ", 1)[1].strip(), False
    return None, False


def _same_origin(url_a, url_b):
    try:
        a = urllib.parse.urlsplit(url_a)
        b = urllib.parse.urlsplit(url_b)
        return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)
    except Exception:
        return False


def _forward(handler, method, spec, body, real_token):
    target = spec.get("url")
    data = body if method == "POST" else None
    req = urllib.request.Request(target, data=data, method=method)
    for h in _COPY_REQ_HEADERS:
        v = handler.headers.get(h)
        if v is not None:
            req.add_header(h, v)
    req.add_header("Authorization", f"Bearer {real_token}")

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(req, timeout=300)
        return resp, getattr(resp, "status", 200), resp.headers
    except HTTPError as e:
        # HTTPError is also a response-like object with .read()/.headers/.code
        return e, getattr(e, "code", 502), getattr(e, "headers", {})


def _copy_response(handler, resp_obj, status, headers):
    content_type = ""
    if headers:
        content_type = headers.get("Content-Type") or headers.get("content-type") or ""
    if isinstance(content_type, str) and content_type.startswith("text/event-stream"):
        handler.send_response(status)
        for h in _COPY_RESP_HEADERS:
            v = headers.get(h) if headers else None
            if v is not None:
                handler.send_header(h, v)
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True
        # stream line by line, flush after each blank line
        while True:
            line = resp_obj.readline()
            if not line:
                break
            handler.wfile.write(line)
            if line in (b"\n", b"\r\n"):
                handler.wfile.flush()
        try:
            handler.wfile.flush()
        except Exception:
            pass
        return

    try:
        data = resp_obj.read(_MAX_RESP + 1)
    except Exception:
        data = b""
    if data is None:
        data = b""
    if len(data) > _MAX_RESP:
        data = data[:_MAX_RESP]
    handler.send_response(status)
    for h in _COPY_RESP_HEADERS:
        v = headers.get(h) if headers else None
        if v is not None:
            handler.send_header(h, v)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    if data:
        handler.wfile.write(data)


def _audit_call(worker_id, server, tool, kind, args, **kw):
    try:
        import hub_tools

        hub_tools._audit(worker_id, server, tool, kind, args, **kw)
    except Exception:
        pass


def _gate_calls(worker_id, server, calls):
    """Return (allowed, denied_info). denied_info=(rpc_id, tool, kind, args) for first denial."""
    import hub_tools

    decisions = []
    for rpc_id, tool, args, _obj in calls:
        args = args if isinstance(args, dict) else {}
        try:
            kind = hub_tools._classify(tool)
        except Exception:
            kind = "write"
        if kind == "publish_delete":
            t0 = time.time()
            try:
                decision = hub_tools._gate(worker_id, server, tool, args)
            except Exception:
                decision = "gate error"
            ms = int((time.time() - t0) * 1000)
            if decision != "allow":
                _audit_call(worker_id, server, tool, kind, args, decision=str(decision), ok=False, ms=ms)
                return False, (rpc_id, tool, kind, args)
            decisions.append((rpc_id, tool, kind, args, ms))
        else:
            decisions.append((rpc_id, tool, kind, args, 0))
    return True, decisions


def handle(handler, method):
    try:
        _handle(handler, method)
    except _ProxyError as e:
        if e.rpc and e.rpc_id is not None:
            # should not be used currently, but safe
            obj = {"jsonrpc": "2.0", "id": e.rpc_id, "error": {"code": -32001, "message": str(e.body)}}
            _send_json(handler, 200, obj)
        else:
            body = e.body if isinstance(e.body, dict) else {"error": str(e.body)}
            _send_json(handler, e.status, body)
    except Exception as e:
        # Never leak headers/tokens via traceback.
        _send_json(handler, 502, {"error": f"upstream failed: {type(e).__name__}"})


def _handle(handler, method):
    client_ip = ""
    try:
        client_ip = (handler.client_address or ("", 0))[0]
    except Exception:
        client_ip = ""
    if client_ip not in _LOOPBACK:
        raise _ProxyError(403, {"error": "forbidden"})

    path = urllib.parse.urlparse(getattr(handler, "path", "") or "").path
    server = _server_from_path(path)
    if not server:
        raise _ProxyError(404, {"error": "not found"})

    auth = handler.headers.get("Authorization") or ""
    if not (isinstance(auth, str) and auth.startswith("Bearer cap_") and len(auth) > len("Bearer cap_")):
        raise _ProxyError(401, {"error": "unauthorized"})
    cap = auth.split(" ", 1)[1].strip()

    import mcp_grants
    import mcp_tasks

    try:
        grant = mcp_grants.check_grant(cap, server)
    except Exception:
        grant = None
    if not isinstance(grant, dict):
        raise _ProxyError(403, {"error": "grant missing or expired"})

    worker_id = grant.get("worker_id")
    try:
        worker = mcp_tasks._load_worker(worker_id)
    except Exception:
        worker = None
    status = (worker or {}).get("status") if isinstance(worker, dict) else None
    if status not in ("running", "launching"):
        raise _ProxyError(403, {"error": "grant missing or expired"})

    spec = _find_server_spec(grant.get("project") or "", server)
    if spec is None:
        raise _ProxyError(404, {"error": "not found"})
    # The spec URL was pinned when the grant was issued; a .mcp.json rewritten under the
    # project mid-grant must not redirect the station's real bearer (F5).
    granted_url = str(grant.get("url") or "")
    if granted_url and str(spec.get("url") or "") != granted_url:
        raise _ProxyError(403, {"error": "server spec changed since the grant was issued; reconnect the server"})

    body = _read_body(handler, method)
    parsed = _parse_json_body(body) if method == "POST" else None

    # Gate tools/call (single or batch). If any denied, deny whole batch/request.
    gated_meta = []  # (rpc_id, tool, kind, args, ms) for publish_delete allowed; others tracked for audit later
    if method == "POST" and parsed is not None:
        calls = _tool_calls(parsed)
        if calls:
            allowed, info = _gate_calls(worker_id, server, calls)
            if not allowed:
                rpc_id, tool, kind, args = info
                _send_rpc_deny(handler, rpc_id, server, tool)
                return
            gated_meta = info  # list of allowed decisions

    real_token, from_subway = _real_bearer(server, spec.get("url") or "")
    if not real_token:
        raise _ProxyError(502, {"error": f"no sign-in for {server}; connect it in Settings → Shared MCP"})

    # Forward, with one refresh+retry on upstream 401 when using Subway token.
    t0 = time.time()
    resp_obj = None
    try:
        resp_obj, up_status, up_headers = _forward(handler, method, spec, body, real_token)
        if up_status == 401 and from_subway:
            try:
                resp_obj.close()
            except Exception:
                pass
            try:
                import oauth_tokens

                refreshed = oauth_tokens.ensure_fresh(server, skew=10**9)
                if isinstance(refreshed, dict) and refreshed.get("access_token"):
                    real_token = refreshed["access_token"]
                    resp_obj, up_status, up_headers = _forward(handler, method, spec, body, real_token)
            except Exception:
                # let original/failed state fall through as upstream response/error
                resp_obj, up_status, up_headers = resp_obj, 401, up_headers
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        # audit tool calls as failed upstream if present
        if method == "POST" and parsed is not None:
            for rpc_id, tool, args, _obj in _tool_calls(parsed):
                args = args if isinstance(args, dict) else {}
                try:
                    import hub_tools

                    kind = hub_tools._classify(tool)
                except Exception:
                    kind = "write"
                _audit_call(worker_id, server, tool, kind, args, ok=False, ms=ms)
        raise _ProxyError(502, {"error": f"upstream failed: {type(e).__name__}"})

    ms = int((time.time() - t0) * 1000)
    try:
        _copy_response(handler, resp_obj, up_status, up_headers)
    finally:
        try:
            resp_obj.close()
        except Exception:
            pass

    # Audit tools/call outcomes.
    if method == "POST" and parsed is not None:
        calls = _tool_calls(parsed)
        if calls:
            # Build set of publish_delete tools we already audited as allowed decisions.
            audited_pd = set()
            if isinstance(gated_meta, list):
                for item in gated_meta:
                    # allowed publish_delete entries recorded as (rpc_id, tool, kind, args, ms_gate)
                    if len(item) == 5:
                        _rid, tool, kind, args, gms = item
                        if kind == "publish_delete":
                            audited_pd.add(id(item))
                            _audit_call(worker_id, server, tool, kind, args, decision="allow", ok=(200 <= up_status < 300), ms=ms)
            for rpc_id, tool, args, _obj in calls:
                args = args if isinstance(args, dict) else {}
                try:
                    import hub_tools

                    kind = hub_tools._classify(tool)
                except Exception:
                    kind = "write"
                if kind == "publish_delete":
                    # already audited above with decision allow
                    continue
                _audit_call(worker_id, server, tool, kind, args, ok=(200 <= up_status < 300), ms=ms)
