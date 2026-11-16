import json
import os
import threading
import time
import urllib.error
import urllib.request

import mcp_hub
import mcp_tasks


_reaper_started = False
_reaper_lock = threading.Lock()


def _worker_may_use(worker_id, server, project):
    rec = mcp_tasks._load_worker(worker_id)
    if not rec or rec.get("status") != "running":
        return False
    needs = rec.get("needs") or []
    if server in needs:
        return True
    try:
        specs = mcp_hub.discover_servers(project)
    except Exception:
        return False
    for key, spec in specs.items():
        if key == server or spec.get("name") == server:
            return key in needs or spec.get("name") in needs
    return False


def _reaper_loop():
    while True:
        try:
            mcp_hub.stop_idle(600)
        except Exception:
            pass
        try:
            # Lent-MCP config files can hold the owner's tokens: never let them outlive their worker.
            d = mcp_hub.worker_mcp_dir()
            for fn in (os.listdir(d) if os.path.isdir(d) else []):
                wid = fn.split(".", 1)[0]
                rec = mcp_tasks._load_worker(wid)
                if not rec or rec.get("status") not in ("running", "pending_approval", "queued"):
                    os.remove(os.path.join(d, fn))
        except Exception:
            pass
        _revoke_finished_grants()
        time.sleep(60)


def _revoke_finished_grants():
    """Proxy grants end with their worker (50c-7)."""
    try:
        import mcp_grants
        for g in mcp_grants.active_grants():
            rec = mcp_tasks._load_worker(g.get("worker_id"))
            if not rec or rec.get("status") not in ("running", "launching", "pending_approval", "queued"):
                mcp_grants.revoke_grants_for_worker(g.get("worker_id"))
        mcp_grants.prune_expired()
    except Exception:
        pass


def _start_reaper():
    global _reaper_started
    if _reaper_started:
        return
    with _reaper_lock:
        if _reaper_started:
            return
        t = threading.Thread(target=_reaper_loop, daemon=True)
        t.start()
        _reaper_started = True


AUDIT_FILE = "hub_calls.jsonl"
GATE_TIMEOUT = 900


MEDIA_DIR = "hub_media"
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp", "image/gif": "gif"}


def _save_media(result, worker_id, server, tool):
    """Save image blocks to files and swap them for a text pointer, so a text-only hub_call client can use them,
    and the initiator can show the worker's own screenshot to the user."""
    paths = []
    if not isinstance(result, dict) or not isinstance(result.get("content"), list):
        return result, paths
    import base64
    d = os.path.join(mcp_hub._subway_dir(), MEDIA_DIR, worker_id or "owner")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in f"{server}_{tool}")[:60]
    out = []
    for i, block in enumerate(result["content"]):
        if isinstance(block, dict) and block.get("type") == "image" and block.get("data"):
            try:
                os.makedirs(d, exist_ok=True)
                ext = _EXT.get(str(block.get("mimeType") or "").lower(), "png")
                path = os.path.join(d, f"{time.strftime('%Y%m%d-%H%M%S')}_{safe}_{i}.{ext}")
                with open(path, "wb") as fh:
                    fh.write(base64.b64decode(block["data"]))
                paths.append(path)
                out.append({"type": "text", "text": f"[image saved: {path}] Put this path in your final reply if your task asks for a screenshot."})
            except Exception as e:
                out.append({"type": "text", "text": f"[image could not be saved: {type(e).__name__}]"})
            continue
        out.append(block)
    result = dict(result)
    result["content"] = out
    return result, paths


def _classify(tool):
    try:
        from worker_supervision.pumps import classify_mcp_tool
        return classify_mcp_tool(tool)
    except Exception:
        return "write"


def _audit(worker_id, server, tool, kind, args, decision="", ok=True, error="", ms=0, media=None):
    """One line per lent call. Argument names only, never values: they can hold page content or secrets."""
    try:
        rec = {"ts": time.time(), "worker_id": worker_id or "", "server": server, "tool": tool, "kind": kind,
               "arg_keys": sorted(args.keys()) if isinstance(args, dict) else [], "ok": bool(ok)}
        if decision:
            rec["decision"] = decision
        if error:
            rec["error"] = error
        if ms:
            rec["ms"] = ms
        if media:
            rec["media"] = list(media)
        with open(os.path.join(mcp_hub._subway_dir(), AUDIT_FILE), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def _gate(worker_id, server, tool, args):
    """Publish/delete on a lent server asks the user in every mode (roadmap 4a). Returns 'allow' or why not."""
    if not worker_id:
        return "allow"  # the owner's own chat calling its own server
    try:
        import permissions
        meta = mcp_tasks._load_worker(worker_id) or {}
        if not meta.get("id"):
            return "unknown worker"
        detail = f"{server}.{tool} ({', '.join(sorted(args)) or 'no arguments'})"
        rid = permissions.request_permission(meta, f"mcp__{server}__{tool}", detail)
        path = permissions._perm_file(rid)
    except Exception as e:
        return f"could not ask: {type(e).__name__}"
    end = time.time() + GATE_TIMEOUT
    while time.time() < end:
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except Exception:
            rec = {}
        if rec.get("status") == "answered":
            return "allow" if rec.get("decision") == "allow" else str(rec.get("decision") or "deny")
        rem = min(1.0, max(0.05, end - time.time()))
        try:
            import permissions as _perm
            _perm.wait_for_activity(timeout=rem)
        except Exception:
            time.sleep(rem)
    return "no answer in 15 minutes"


def handle_get_servers(project=""):
    _start_reaper()
    return 200, {"servers": mcp_hub.list_servers(project), "running": mcp_hub.status()}


def handle_post(path, payload):
    _start_reaper()
    if path not in ("/api/mcp/hub/tools", "/api/mcp/hub/call"):
        return 404, {"error": "unknown hub route"}
    payload = payload or {}
    server = str(payload.get("server") or "")
    if not server:
        return 400, {"error": "server is required"}
    project = str(payload.get("project") or "")
    wid = payload.get("worker_id")
    if wid and not _worker_may_use(wid, server, project):
        return 403, {"error": f"worker {wid} was not granted {server}: spin it with needs=[...]"}
    try:
        if path == "/api/mcp/hub/tools":
            return 200, {"tools": mcp_hub.tools(server, project_dir=project)}
        tool = payload.get("tool")
        if not tool:
            return 400, {"error": "tool is required"}
        args = payload.get("arguments") or {}
        if isinstance(args, str):
            # Some clients (Gemini) send the arguments as JSON text.
            try:
                args = json.loads(args) if args.strip() else {}
            except ValueError:
                args = None
        if not isinstance(args, dict):
            return 400, {"error": "arguments must be a JSON object"}
        kind = _classify(tool)
        if kind == "publish_delete":
            decision = _gate(wid, server, tool, args)
            if decision != "allow":
                _audit(wid, server, tool, kind, args, decision=decision, ok=False)
                return 403, {"error": f"the user did not allow {server}.{tool} ({decision}). "
                                      "Publish and delete calls on a lent server always ask first."}
        t0 = time.time()
        try:
            result = mcp_hub.call(server, tool, args, timeout=float(payload.get("timeout") or 120),
                                  project_dir=project)
        except Exception as e:
            _audit(wid, server, tool, kind, args, ok=False, error=f"{type(e).__name__}: {str(e)[:200]}",
                   ms=int((time.time() - t0) * 1000))
            raise
        result, media = _save_media(result, wid, server, tool)
        _audit(wid, server, tool, kind, args, decision="allow" if kind == "publish_delete" else "",
               ok=True, ms=int((time.time() - t0) * 1000), media=media)
        return 200, {"result": result, "media": media}
    except KeyError as e:
        return 404, {"error": str(e).strip("'")}
    except mcp_hub.McpError as e:
        return 502, {"error": str(e), "code": getattr(e, "code", None)}
    except TimeoutError:
        return 504, {"error": "MCP server timed out"}


def _hud(method, path, body=None, timeout=150):
    import hud_server.lifecycle as lifecycle

    url = lifecycle._hud_url(lifecycle._hud_port()) + path
    token = lifecycle.hud_token()
    data = None
    headers = {"X-Subway-Token": token}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8") or "{}"
            return json.loads(raw)
        except Exception:
            return {"error": str(e)}
    except (urllib.error.URLError, ConnectionError) as e:
        raise RuntimeError("Subway HUD is not running: call start_hud first.") from e


def t_hub_servers(project: str = ""):
    try:
        data = _hud("GET", "/api/mcp/hub/servers" + (f"?project={urllib.parse.quote(project)}" if project else ""))
    except RuntimeError as e:
        return str(e)
    if "error" in data and "servers" not in data:
        return "Error: " + str(data["error"])
    servers = data.get("servers") or []
    lines = []
    for s in servers:
        key = s.get("key") or s.get("name") or "?"
        owners = ",".join(s.get("owners") or [])
        transport = s.get("transport") or "?"
        line = f"- `{key}` ({owners}) {transport}"
        if not s.get("supported", True):
            line += " (not hosted yet)"
        lines.append(line)
    return "\n".join(lines) if lines else "(no shared MCP servers found)"


def t_hub_tools(server: str, project: str = ""):
    try:
        data = _hud("POST", "/api/mcp/hub/tools", {"server": server, "project": project})
    except RuntimeError as e:
        return str(e)
    if "error" in data:
        return "Error: " + str(data["error"])
    tools = data.get("tools") or []
    blocks = []
    for t in tools:
        name = t.get("name") or "?"
        desc = t.get("description") or ""
        schema = t.get("inputSchema") or {}
        blocks.append(f"- {name}: {desc}\n  input: {json.dumps(schema, separators=(',', ':'))}")
    return "\n\n".join(blocks) if blocks else "(no tools)"


def t_hub_call(server: str, tool: str, arguments: dict = None, project: str = ""):
    try:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except ValueError:
                return "Error: arguments must be a JSON object"
        payload = {"server": server, "tool": tool, "arguments": arguments or {}, "project": project}
        wid = os.environ.get("SUBWAY_WORKER_ID")
        if wid:
            payload["worker_id"] = wid
        data = _hud("POST", "/api/mcp/hub/call", payload, timeout=GATE_TIMEOUT + 180)
    except RuntimeError as e:
        return str(e)
    if "error" in data:
        return "Error: " + str(data["error"])
    result = data.get("result") or {}
    content = result.get("content") or []
    texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    if texts:
        return "\n".join(texts)
    return json.dumps(result)
