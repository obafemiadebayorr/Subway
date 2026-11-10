from __future__ import annotations

from __future__ import annotations

import base64

import concurrent.futures

import contextlib

import contextvars

import datetime as _dt

import difflib

import glob

import hashlib

import hmac

import http.server

import ipaddress

import json

import math

import os

import queue

import re

import secrets

import select

import shutil

import signal

import sqlite3

import subprocess

import sys

import tempfile

import threading

try:
    import ctypes
except ImportError:
    ctypes = None  # type: ignore[assignment]

import time

import traceback

import urllib.error

import urllib.parse

import urllib.request

import uuid


def _wake_rules_error(rules):
    """None when rules is a valid {event: {"wake": bool, "actions": [str]}} override, else the reason."""
    import wake_driver
    if not isinstance(rules, dict):
        return "wake_rules must be an object"
    for key, rule in rules.items():
        if key not in wake_driver.WAKE_EVENT_RULES:
            return f"unknown wake event: {key}"
        if key in wake_driver.LOCKED_RULES:
            return "locked: the user always decides permissions"
        if not isinstance(rule, dict) or set(rule) - {"wake", "actions"}:
            return f"{key}: only 'wake' and 'actions' may be set"
        if "wake" in rule and not isinstance(rule["wake"], bool):
            return f"{key}: wake must be true or false"
        if "actions" in rule and not (isinstance(rule["actions"], list) and all(isinstance(a, str) for a in rule["actions"])):
            return f"{key}: actions must be a list of strings"
    return None

from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

import terminals

import native_terminals

import sse_stream

import threads
import thread_state

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

import guard
import server as _s
from hud_server import attachments
from hud_server import composer_api
import pairing


class SubwayHUDHandler(http.server.BaseHTTPRequestHandler):
    server_version = "SubwayHUD"
    sys_version = ""

    def log_message(self, format, *args):
        pass

    # ---- request gate ----

    def _port(self) -> int:
        return int(self.server.server_address[1])

    def _host_ok(self) -> bool:
        """Refuses DNS rebinding: a hostile domain pointed at 127.0.0.1 still sends its own Host."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        allowed = {"127.0.0.1", "localhost", "::1"}
        bound_host = getattr(_s, "HUD_HOST", None)
        if bound_host:
            h = bound_host.strip().lower()
            if h in ("0.0.0.0", "::"):
                extra = {x.strip().lower() for x in os.environ.get("SUBWAY_HUD_HOSTS", "").split(",") if x.strip()}
                if host in allowed or host in extra:
                    return True
                try:
                    ipaddress.ip_address(host)
                    return True
                except (ValueError, AttributeError):
                    return False
            allowed.add(h)
        return host in allowed

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        port = self._port()
        allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        bound_host = getattr(_s, "HUD_HOST", None)
        if bound_host and bound_host not in ("127.0.0.1", "0.0.0.0", "::"):
            allowed.add(f"http://{bound_host}:{port}")
        allowed.update(o.strip() for o in os.environ.get("SUBWAY_HUD_ORIGINS", "").split(",") if o.strip())
        return origin in allowed

    def _authorized(self) -> bool:
        expected = _s.hud_token()
        if not expected:
            return False
        # 1. Header: X-Subway-Token
        token = self.headers.get("X-Subway-Token", "").strip()
        if token and hmac.compare_digest(token, expected):
            return True
        # 2. Header: Authorization: Bearer <token>
        auth = self.headers.get("Authorization", "").strip()
        if auth.startswith("Bearer "):
            bearer = auth[7:].strip()
            if bearer and hmac.compare_digest(bearer, expected):
                return True
        # 3. Query parameter: ?token=<token>
        if hasattr(self, "path") and "?" in self.path:
            import urllib.parse
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            q_token = qs.get("token", [""])[0].strip()
            if q_token and hmac.compare_digest(q_token, expected):
                return True
        # 4. A paired device token. Scoped to that device and revocable without touching the
        #    master key, which is the whole point of pairing.
        try:
            for cand in (token, auth[7:].strip() if auth.startswith("Bearer ") else "", q_token):
                if cand and pairing.is_device_token(cand):
                    return True
        except Exception:
            pass
        return False

    def _security_headers(self, nonce: str = "") -> None:
        csp = ("default-src 'none'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
               "font-src https://fonts.gstatic.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
               f"script-src 'self' {repr('nonce-' + nonce) if nonce else ''}; "
               "frame-ancestors 'self' vscode-webview: vscode-file:")
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def _cors_origin(self) -> str:
        """Cross-origin access is opt-in per origin, never a wildcard. Empty by default."""
        headers = getattr(self, "headers", None)
        if headers is None:
            return ""
        origin = (headers.get("Origin") or "").strip()
        if not origin:
            return ""
        allowed = {o.strip() for o in os.environ.get("SUBWAY_HUD_CORS_ORIGINS", "").split(",") if o.strip()}
        return origin if origin in allowed else ""

    def send_json(self, data: Any, status: int = 200) -> None:
        try:
            payload = json.dumps(data).encode("utf-8")
            self.send_response(status)
            cors = self._cors_origin()
            if cors:
                self.send_header("Access-Control-Allow-Origin", cors)
                self.send_header("Vary", "Origin")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self._security_headers()
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _deny(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status)

    def _gate(self, api: bool) -> bool:
        if not self._host_ok():
            self._deny(403, "Host not allowed")
            return False
        if not self._origin_ok():
            self._deny(403, "Origin not allowed")
            return False
        if api and not self._authorized():
            self._deny(401, "Missing or invalid token")
            return False
        return True

    # ---- verbs ----

    def do_OPTIONS(self):
        # Cross-origin is opt-in by origin (SUBWAY_HUD_CORS_ORIGINS); everything else gets nothing.
        cors = self._cors_origin() if self._host_ok() else ""
        if not cors:
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", cors)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Subway-Token, Mcp-Method, Mcp-Name")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_HEAD(self):
        if not self._host_ok():
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def _serve_page(self) -> None:
        try:
            with open(_s.HUD_FILE, "r", encoding="utf-8") as f:
                page = f.read()
        except OSError as e:
            self._deny(500, f"HUD page missing: {e}")
            return
        nonce = secrets.token_urlsafe(16)
        try:
            stamp = str(int(os.path.getmtime(os.path.join(_s.HUD_DIR, "app.js"))))
        except OSError:
            stamp = "0"
        client_ip = ""
        with contextlib.suppress(Exception):
            client_ip = self.client_address[0]
        try:
            remote_loopback = ipaddress.ip_address(client_ip).is_loopback if client_ip else False
        except (ValueError, AttributeError):
            remote_loopback = False
        token = _s.hud_token() if remote_loopback else ""
        payload = (page.replace("__SUBWAY_TOKEN__", token)
                       .replace("__ASSET_V__", stamp)
                       .replace("__CSP_NONCE__", nonce).encode("utf-8"))
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self._security_headers(nonce)
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _serve_asset(self, path: str) -> None:
        """The HUD's own modules and styles, with no secrets in them, from hud/ and nowhere else."""
        name = path[len("/hud/"):]
        # Optional version segment (/hud/v<stamp>/app.js): stripped here, but it changes the URL,
        # so browser caches of older deploys are never consulted and relative imports inherit the stamp.
        name = re.sub(r"^v\d+/", "", name)
        full = os.path.realpath(os.path.join(_s.HUD_DIR, name))
        kinds = {".js": "text/javascript", ".css": "text/css"}
        kind = kinds.get(os.path.splitext(name)[1])
        if not kind or not re.fullmatch(r"[a-z0-9_/-]+\.(?:js|css)", name) or \
                not full.startswith(os.path.realpath(_s.HUD_DIR) + os.sep) or not os.path.isfile(full):
            self._deny(404, "Not found")
            return
        with open(full, "rb") as f:
            payload = f.read()
        try:
            self.send_response(200)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")  # modules change on every deploy; never let the browser cache them
            self._security_headers()
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _stream_events(self) -> None:
        """Server-sent events via SSEBroadcaster (Roadmap Item 38a2): simultaneous events to multiple concurrent subscribers."""
        b = sse_stream.get_broadcaster()
        sub_q = b.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self._security_headers()
        self.end_headers()

        init_msg = f"event: connect\ndata: {json.dumps({'status': 'connected', 'subscribers': b.subscriber_count()})}\n\n"
        try:
            self.wfile.write(init_msg.encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    msg = sub_q.get(timeout=15.0)
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            b.unsubscribe(sub_q)

    def do_GET(self):
        pr = urllib.parse.urlparse(self.path)
        path = pr.path
        query = urllib.parse.parse_qs(pr.query)

        if path == "/healthz":
            if self._gate(api=False):
                self.send_json({"service": "subway-context", "version": _s.VERSION, "pid": os.getpid()})
            return
        if path == "/api/reload":
            if not self._gate(api=True):
                return
            reloaded, msg = False, ""
            if hasattr(_s, "check_and_reload"):
                with contextlib.suppress(Exception):
                    reloaded, msg = _s.check_and_reload(force=True)
            self.send_json({"ok": True, "reloaded": reloaded, "message": msg, "version": _s.VERSION, "pid": os.getpid()})
            return
        if path in ("/", "/index.html"):
            if self._gate(api=False):
                self._serve_page()
            return
        if path.startswith("/hud/"):
            if self._gate(api=False):
                self._serve_asset(path)
            return
        if not path.startswith("/api/"):
            self._deny(404, "Not found")
            return
        if path == "/api/pair/status":
            # The polling phone has no token yet; the host gate still applies, and nothing is
            # handed over unless the Mac approved this exact request id.
            if self._gate(api=False):
                self.send_json(pairing.status((query.get("request_id") or [""])[0]))
            return
        if not self._gate(api=True):
            return

        if path == "/api/events":
            self._stream_events()
            return

        if path == "/api/ping":
            now = time.time()
            with _s._usage_lock:
                sources = {k: {"age_s": round(now - v["fetched_at"], 1) if v.get("fetched_at") else None,
                               "latency_ms": v.get("latency_ms"), "error": v.get("error")} for k, v in _s._usage.items()}
            self.send_json({"version": _s.VERSION, "pid": os.getpid(), "time": now, "sources": sources})
            return

        if path == "/api/status":
            _s._viewer_seen = time.time()
            d = _s.get_hud_data(project=query.get("project", [""])[0])
            rev = d.get("models_track_rev") if isinstance(d, dict) else None
            if rev and query.get("models_rev", [""])[0] == rev:
                d = {k: v for k, v in d.items() if k != "models_track"}
            self.send_json(d)
            return

        if path == "/api/iterations":
            import iteration as _iter_mod
            items = _iter_mod.list_iterations() if hasattr(_iter_mod, "list_iterations") else []
            self.send_json({"iterations": items})
            return

        if path == "/api/wake":
            import wake as _wake
            self.send_json(_wake.status())
            return

        if path == "/api/pair/list":
            self.send_json({"requests": pairing.list_requests(include_done=False), "devices": pairing.list_devices()})
            return

        if path == "/api/thread_log":
            self.send_json({"threads": threads.list_threads(_s.SUBWAY_DIR)})
            return

        if path == "/api/routing_weights":
            data = _s.load_routing_weights() if hasattr(_s, "load_routing_weights") else {}
            self.send_json(data)
            return

        if path == "/api/thread_log_read":
            thread_wanted = query.get("id", [""])[0]
            if not thread_wanted:
                self._deny(400, "Missing thread id")
                return
            try:
                self.send_json(threads.read_thread(_s.SUBWAY_DIR, thread_wanted))
            except ValueError as e:
                self._deny(400, str(e))
            except OSError as e:
                self._deny(404, str(e))
            return

        if path == "/api/composer/project_files":
            q = query.get("q", [""])[0]
            project_dir = query.get("project_dir", [""])[0]
            limit_raw = query.get("limit", ["50"])[0]
            try:
                import composer_context
                res = composer_context.project_files(q=q, project_dir=project_dir, limit=int(limit_raw))
                self.send_json(res)
            except ValueError as e:
                self._deny(400, str(e))
            except Exception as e:
                self._deny(500, str(e))
            return

        if path == "/api/composer/thread_export":
            thread = query.get("thread", [""])[0]
            fmt = query.get("format", ["md"])[0]
            if not thread:
                self._deny(400, "Missing required 'thread' query parameter")
                return
            try:
                import composer_context
                res = composer_context.thread_export(thread=thread, fmt=fmt)
                if fmt.lower() == "json":
                    self.send_json(res)
                else:
                    content = res.get("content", "")
                    mime = res.get("mime_type", "text/plain")
                    payload = content.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", f"{mime}; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self._security_headers()
                    self.end_headers()
                    self.wfile.write(payload)
            except ValueError as e:
                self._deny(400, str(e))
            except Exception as e:
                self._deny(500, str(e))
            return


        if path == "/api/thread_settings":
            tid = query.get("id", [""])[0].strip()
            data = _s._load_json("thread_settings.json")
            overrides = data.get(tid, {}) if isinstance(data, dict) else {}
            cfg = _s._load_json("config.json")
            universal = {
                "permission_mode": cfg.get("permission_mode") or "ask",
                "terminal_mode": cfg.get("terminal_mode") or "ask",
                "budget_tokens": cfg.get("budget_tokens") or None,
                "compaction": cfg.get("compaction") or "auto",
                "compact_at_tokens": cfg.get("compact_at_tokens") or 80000,
            }
            ov_pm = overrides.get("permission_mode")
            ov_tm = overrides.get("terminal_mode")
            eff_pm = ov_pm if ov_pm in _s.PERMISSION_MODES else universal["permission_mode"]
            eff_tm = ov_tm if ov_tm in _s.PERMISSION_MODES else universal["terminal_mode"]
            effective = {
                "permission_mode": eff_pm,
                "terminal_mode": eff_tm,
                "budget_tokens": overrides.get("budget_tokens", universal["budget_tokens"]),
                "compaction": overrides.get("compaction") or universal["compaction"],
                "compact_at_tokens": overrides.get("compact_at_tokens", universal["compact_at_tokens"]),
            }
            self.send_json({"id": tid, "effective": effective, "universal": universal, "overrides": overrides})
            return

        if path in ("/api/chats", "/api/threads"):
            scope_default = "all" if ("source" in query or path == "/api/chats") else "subway"
            scope = query.get("scope", [scope_default])[0].lower()
            source = query.get("source", ["all"])[0]
            include_subagents = query.get("include_subagents", ["false"])[0].lower() in ("1", "true", "yes", "on")
            try:
                limit = min(500, max(1, int(query.get("limit", ["200"])[0])))
            except (TypeError, ValueError):
                limit = 200
            if query.get("lite", ["0"])[0].lower() in ("1", "true", "yes"):
                # Mentions need only names: skip the worker join and reuse the chat scan for 15 s.
                key = (source, include_subagents)
                cache = self.__class__.__dict__.get("_lite_chats") or {}
                hit = cache.get(key)
                if not hit or time.time() - hit[0] > 15:
                    lc = _s.all_chats(source, include_subagents=include_subagents)
                    lc.sort(key=lambda c: (bool(c.get("open")), _s.sort_key_ts(c.get("updated"))), reverse=True)
                    hit = (time.time(), lc)
                    cache[key] = hit
                    self.__class__._lite_chats = cache
                first_alias: Dict[str, str] = {}
                for alias, chat_id in _s.load_aliases().items():
                    first_alias.setdefault(str(chat_id), alias)
                self.send_json({"chats": [{"id": c.get("id"), "title": c.get("title") or "", "alias": first_alias.get(str(c.get("id")), ""),
                                           "source": c.get("source") or "", "updated": c.get("updated")} for c in hit[1][:limit]]})
                return
            aliases: Dict[str, List[str]] = {}
            for alias, chat_id in _s.load_aliases().items():
                aliases.setdefault(str(chat_id), []).append(alias)
            chats = _s.all_chats(source, include_subagents=include_subagents)
            chats.sort(key=lambda c: (bool(c.get("open")), _s.sort_key_ts(c.get("updated"))), reverse=True)
            used_chats = threads.load_used_chats(_s.SUBWAY_DIR)
            all_workers = _s._all_workers()
            chat_workers: Dict[str, List[Dict[str, Any]]] = {}
            for w in all_workers:
                root = str(w.get("thread_root") or w.get("launched_by") or "")
                if root:
                    w_info = {
                        "id": w.get("id"),
                        "role": w.get("role") or "",
                        "status": w.get("status") or "",
                        "model": w.get("model") or "",
                        "provider": w.get("provider") or "",
                        "tokens": w.get("tokens") or (w.get("usage", {}).get("input", 0) + w.get("usage", {}).get("output", 0)),
                        "spent": w.get("spent") or w.get("budget", {}).get("max_tokens"),
                        "verdict": w.get("verdict"),
                        "verdict_reason": w.get("verdict_reason"),
                        "created_at": w.get("created_at"),
                        "finished_at": w.get("completed_at"),
                        "parent_id": w.get("parent_id") or w.get("triggered_by") or "",
                        "summary": w.get("start_note") or w.get("summary") or w.get("role") or "",
                    }
                    chat_workers.setdefault(root, []).append(w_info)
            thread_overrides = _s._load_json("thread_settings.json")
            thread_meta_all = thread_state.load_all(_s.SUBWAY_DIR)

            def _thread_requester(chat, w_list):
                """The station that asked for this thread's work, else the chat itself."""
                for w in w_list:
                    who = str(w.get("requested_by") or "").strip()
                    if who:
                        return who
                return str(chat.get("requested_by") or chat.get("id") or "unknown")

            def _thread_payers(w_list):
                """Providers whose quota paid for this thread, most-used first."""
                counts = {}
                for w in w_list:
                    prov = str(w.get("paid_by") or w.get("provider") or "").strip()
                    if prov:
                        counts[prov] = counts.get(prov, 0) + 1
                if not counts:
                    return "unknown"
                return ", ".join(p for p, _ in sorted(counts.items(), key=lambda kv: -kv[1]))

            def _thread_cost(w_list, chat=None):
                """USD for this thread's workers, or None when no price is known.

                Two sources, in order: a provider-reported cost_usd, then tokens
                priced from config.json -> model_prices (USD per 1M tokens, e.g.
                {"claude-opus-5-5": {"input": 15, "output": 75}}). Prices are
                config, never hardcoded, so a new model or a price change needs no
                code edit. Subscription-only providers report no price and stay
                None rather than showing a made-up number.
                """
                prices = _s._load_json("config.json").get("model_prices") or {}
                total = 0.0
                seen = False
                for w in w_list:
                    cost = w.get("cost_usd")
                    if isinstance(cost, (int, float)):
                        total += float(cost)
                        seen = True
                        continue
                    price = prices.get(str(w.get("model") or "")) or prices.get(str(w.get("model_used") or ""))
                    usage = w.get("usage") or {}
                    if isinstance(price, dict) and isinstance(usage, dict):
                        inp = float(usage.get("input") or 0)
                        out = float(usage.get("output") or 0)
                        total += (inp * float(price.get("input") or 0) + out * float(price.get("output") or 0)) / 1_000_000.0
                        seen = True
                if not seen and chat:
                    # No per-worker usage (some providers report none): price the
                    # thread's own token count when a price exists for its model.
                    price = prices.get(str(chat.get("model") or ""))
                    tokens = chat.get("tokens")
                    if isinstance(price, dict) and isinstance(tokens, (int, float)) and tokens:
                        # Transcript tokens are mostly input; use the input rate.
                        total += float(tokens) * float(price.get("input") or 0) / 1_000_000.0
                        seen = True
                return round(total, 4) if seen else None

            def _lifecycle(w_list, chat, meta):
                """Working / monitoring / done / snoozed / settled, from workers then user state."""
                if meta.get("settled") or meta.get("archived"):
                    return "settled"
                if meta.get("snoozed_until") and float(meta["snoozed_until"]) > time.time():
                    return "snoozed"
                if any(w.get("status") in ("launching", "running", "pending_approval", "stopping") for w in w_list):
                    return "working"
                if w_list:
                    return "monitoring"
                return "monitoring" if chat.get("open") else "done"

            def _activity(w_list):
                """The verb this thread is doing right now."""
                live = next((w for w in w_list if w.get("status") in ("launching", "running", "pending_approval")), None)
                if not live:
                    return "idle"
                if live.get("status") == "pending_approval":
                    return "waiting"
                role = str(live.get("role") or "").lower()
                if "plan" in role or "architect" in role or "overseer" in role:
                    return "planning"
                if "review" in role or "verif" in role:
                    return "reviewing"
                return "building"

            def _override_mode(cid: str) -> Optional[str]:
                entry = thread_overrides.get(str(cid or ""))
                mode = entry.get("permission_mode") if isinstance(entry, dict) else None
                return mode if mode in _s.PERMISSION_MODES else None

            thread_items = []
            for c in chats:
                cid = str(c.get("id") or "")
                w_list = chat_workers.get(cid, [])
                in_used = cid in used_chats
                used_subway = in_used or len(w_list) > 0
                used_val = used_chats.get(cid)
                used_at = used_val.get("last_used") if isinstance(used_val, dict) else None
                ov_dict = thread_overrides.get(cid, {}) if isinstance(thread_overrides.get(cid), dict) else {}
                meta = thread_meta_all.get(cid) or thread_state.public(None)
                if meta.get("deleted"):
                    continue
                thread_items.append({
                    "source": c.get("source"), "id": cid, "title": meta.get("title") or c.get("title"),
                    "meta": meta,
                    "lifecycle": _lifecycle(w_list, c, meta),
                    "activity": _activity(w_list),
                    # Who asked for this thread's work and whose quota paid for it:
                    # aggregate from the thread's workers (they record both at spawn).
                    "requested_by": _thread_requester(c, w_list),
                    "paid_by": _thread_payers(w_list),
                    "cost_usd": _thread_cost(w_list, c),
                    "project": c.get("project"), "updated": c.get("updated"), "open": bool(c.get("open")),
                    "tab_name": c.get("tab_name"), "worker": bool(c.get("worker")),
                    "subagent": bool(c.get("subagent")), "parent": c.get("parent") or "",
                    "surface": c.get("surface") or "", "model": c.get("model") or "",
                    "tokens": c.get("tokens"), "usage": c.get("usage"),
                    "aliases": aliases.get(cid, []),
                    "permission_override": _override_mode(cid),
                    "terminal_override": ov_dict.get("terminal_mode"),
                    "budget_override": ov_dict.get("budget_tokens"),
                    "compaction_override": ov_dict.get("compaction"),
                    "thread_settings": ov_dict,
                    "used_subway": used_subway,
                    "used_subway_at": used_at,
                    "workers": w_list,
                    "worker_count": len(w_list),
                })
            thread_items.sort(key=lambda t: (bool((t.get("meta") or {}).get("pinned")), bool(t.get("open"))), reverse=True)
            total_all = len(thread_items)
            subway_items = [t for t in thread_items if t["used_subway"]]
            total_subway = len(subway_items)
            final_items = subway_items[:limit] if scope == "subway" else thread_items[:limit]
            self.send_json({
                "threads": final_items,
                "chats": final_items,
                "scope": scope,
                "total": total_subway if scope == "subway" else total_all,
                "total_subway": total_subway,
                "total_all": total_all
            })
            return

        if path == "/api/archive_settings":
            cfg = _s._load_json("config.json")
            if not isinstance(cfg, dict):
                cfg = {}
            self.send_json({
                "archive": {
                    "compress_days": cfg.get("archive_compress_days", "off"),
                    "delete_days": cfg.get("archive_delete_days", "off"),
                    "branch_days": cfg.get("archive_branch_days", "off"),
                    "choices": ["off", "3", "7", "30", "60", "90"],
                }
            })
            return

        if path == "/api/wake_rules":
            import wake_driver
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            thread = (q.get("thread") or [""])[0].strip()
            provider = (q.get("provider") or [""])[0].strip()
            cfg = _s._load_json("config.json")
            cfg = cfg if isinstance(cfg, dict) else {}
            ts = _s._load_json("thread_settings.json")
            ts = ts if isinstance(ts, dict) else {}
            tentry = ts.get(thread, {}) if thread and isinstance(ts.get(thread), dict) else {}
            kind_for_key = {
                "worker_ended": "completed",
                "worker_failed": "error",
                "budget_stopped": "budget",
                "permission_needed": "permission",
                "approval_needed": "approval",
                "mail_fired": "mail",
                "minor": "minor",
            }
            self.send_json({
                "universal": {k: {**v, **(cfg.get("wake_rules", {}) or {}).get(k, {})} for k, v in wake_driver.WAKE_EVENT_RULES.items()},
                "provider_override": ((cfg.get("wake_rules_by_provider") or {}).get(provider) or {}) if provider else {},
                "thread_override": tentry.get("wake_rules") or {},
                "effective": {k: wake_driver.resolve_wake_rule(kind_for_key[k], provider=provider, thread_id=thread, config=cfg, thread_settings=tentry) for k in wake_driver.WAKE_EVENT_RULES},
                "locked": sorted(wake_driver.LOCKED_RULES),
            })
            return
        if path == "/api/wake_settings":
            cfg = _s._load_json("config.json")
            if not isinstance(cfg, dict):
                cfg = {}
            self.send_json({
                "wake": {
                    "policy": cfg.get("wake_events", "all"),
                    "choices": ["all", "outcomes", "failures", "off"],
                }
            })
            return
        if path == "/api/governor_status":
            try:
                import governor
                st = governor.status()
            except Exception:
                st = []
            self.send_json({"sessions": st})
            return

        if path == "/api/principal_settings":
            cfg = _s._load_json("config.json")
            if not isinstance(cfg, dict):
                cfg = {}
            try:
                max_reads = max(0, int(cfg.get("principal_max_reads", 30) or 0))
            except (TypeError, ValueError):
                max_reads = 30
            self.send_json({
                "principal": {
                    "guard_enabled": bool(cfg.get("principal_guard_enabled", True)),
                    "max_reads": max_reads,
                    "mcp_read_tools": guard.principal_mcp_read_tools(cfg),
                }
            })
            return

        if path == "/api/exclusions":
            self.send_json({"exclusions": threads.load_exclusions(_s.SUBWAY_DIR)})
            return

        if path == "/api/copilot_inline_estimate":
            f = os.path.join(_s.SUBWAY_DIR, "copilot_inline_estimate.json")
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                data = {"kind": "copilot_inline_estimate", "estimate": True, "days": {}}
            self.send_json(data)
            return

        if path == "/api/models":
            self.send_json({"models": _s.model_catalog()})
            return

        if path == "/api/station":
            import station
            card = station.get_station_card()
            self.send_json(card)
            return

        if path == "/api/worker_log":
            wid = query.get("id", [""])[0]
            if not _s.WORKER_ID_RE.match(wid):
                self._deny(400, "invalid worker id")
                return
            meta = _s._load_worker(wid) or {}
            log_path = meta.get("log_file") or _s._log_file(wid)
            archived = os.path.join(_s._archive_dir(), f"{wid}.log")
            if not os.path.exists(log_path) and os.path.exists(archived):
                log_path = archived
            if os.path.exists(log_path):
                self.send_json({"id": wid, "log": _s._log_tail(log_path, 200), "status": meta.get("status"),
                                "exit_code": meta.get("exit_code")})
                return
            self._deny(404, "log not found")
            return

        if path == "/api/token_history":
            """Hourly tokens per provider over a wider window, for the spend chart's paging."""
            try:
                hours = int(query.get("hours", ["24"])[0])
            except (TypeError, ValueError):
                hours = 24
            self.send_json(_s.token_history(hours))
            return

        if path == "/api/stats":
            try:
                from hud_server import stats as _stats
                range_key = query.get("range", ["24h"])[0]
                metric = query.get("metric", ["tokens"])[0]
                group = query.get("group", ["provider"])[0]
                end_raw = query.get("end", [""])[0]
                try:
                    end = float(end_raw) if end_raw else None
                except (TypeError, ValueError):
                    end = None
                self.send_json(_stats.compute(range_key, metric, group, end=end))
            except Exception as exc:
                self.send_json({"error": str(exc), "range": query.get("range", ["24h"])[0]}, 500)
            return

        if path == "/api/diagnostics":
            try:
                from hud_server import diagnostics as _diag
                self.send_json(_diag.compute())
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        if path == "/api/worker_digest":
            try:
                import worker_digest as _wd
                self.send_json({"items": _wd.digest()})
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        if path == "/api/worker_activity":
            """Workers spun per day, for the overview terrain. Served from the ledger."""
            try:
                days = min(370, max(1, int(query.get("days", ["14"])[0])))
            except (TypeError, ValueError):
                days = 14
            self.send_json(_s.get_worker_activity(days))
            return

        if path == "/api/worker_state":
            wid = query.get("id", [""])[0]
            if not _s.WORKER_ID_RE.match(wid):
                self._deny(400, "invalid worker id")
                return
            meta = _s._load_worker(wid) or {}
            if not meta:
                self._deny(404, "worker not found")
                return
            usage = meta.get("usage") or {}
            self.send_json({
                "id": wid, "status": meta.get("status"), "role": meta.get("role"),
                "provider": meta.get("provider"), "model": meta.get("model"),
                "progress": meta.get("progress"),
                "tokens": meta.get("tokens") or (usage.get("input", 0) + usage.get("output", 0)),
                "updated_at": meta.get("completed_at") or meta.get("created_at"),
            })
            return

        if path in ("/api/track", "/api/ledger"):
            tname = query.get("name", ["main"])[0]
            self.send_json({"track": _s._clean_track(tname), "entries": _s.read_track_entries(tname, 50)})
            return

        if path == "/api/history":
            try:
                offset = max(0, int(query.get("offset", [0])[0]))
            except (ValueError, TypeError):
                offset = 0
            try:
                limit = min(200, max(1, int(query.get("limit", [100])[0])))
            except (ValueError, TypeError):
                limit = 100
            # archived=include adds archived workers (each keeps its `archived` flag, so the HUD can
            # offer Unarchive); archived=only lists just those. Absent: live records only, as before.
            archived = (query.get("archived", [""])[0] or "").strip().lower()
            if archived and archived not in ("include", "only"):
                self._deny(400, "archived must be include or only")
                return
            records = _s._all_workers(include_archive=bool(archived))
            if archived == "only":
                records = [w for w in records if w.get("archived")]
            history = []
            for w in records:
                if w.get("status") not in _s.ACTIVE_WORKER_STATES:
                    w = dict(w)
                    w.pop("cmd", None)
                    history.append(w)
            history.sort(key=lambda x: str(x.get("completed_at") or x.get("created_at") or ""), reverse=True)
            self.send_json({"entries": history[offset:offset + limit], "total": len(history)})
            return

        if path == "/api/terminals":
            with _s._state_lock("terminals"):
                _s._tidy_terminals()
                rows = terminals.list_terminals(_s._terminals_dir())
            self.send_json({"terminals": rows, "sharing": _s.terminal_sharing_enabled()})
            return

        if path == "/api/terminal":
            tid = query.get("id", [""])[0]
            if not tid:
                self._deny(400, "missing id")
                return
            with _s._state_lock("terminals"):
                rec = terminals.get_terminal(_s._terminals_dir(), tid)
            if not rec:
                self._deny(404, "terminal not found")
                return
            self.send_json({"terminal": rec})
            return

        if path == "/api/pending_runs":
            tid = query.get("terminal_id", [""])[0]
            with _s._state_lock("terminals"):
                runs = terminals.list_pending_runs(_s._terminals_dir(), terminal_id=tid)
            self.send_json({"runs": runs})
            return

        self._deny(404, "Not found")

    def _read_payload(self) -> Optional[Dict[str, Any]]:
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            self._deny(415, "Content-Type must be application/json")
            return None
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        limit = attachments.UPLOAD_MAX_BODY if urllib.parse.urlparse(self.path).path == "/api/composer/upload" else _s.MAX_BODY_BYTES
        if length < 0 or length > limit:
            self._deny(413, "Request too large")
            return None
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._deny(400, "Invalid JSON")
            return None
        if not isinstance(payload, dict):
            self._deny(400, "Expected a JSON object")
            return None
        return payload

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/pair/request":
            if not self._gate(api=False):
                return
            payload = self._read_payload()
            if payload is None:
                return
            res = pairing.create_request(
                device=str(payload.get("device") or "Unknown device"),
                platform=str(payload.get("platform") or ""),
                ip=str(self.client_address[0]) if self.client_address else "",
                note=str(payload.get("note") or ""),
            )
            self.send_json(res, status=429 if "error" in res else 200)
            return
        if not self._gate(api=True):
            return
        if path in ("/api/budget", "/api/chat_budget", "/api/settings", "/api/thread_settings"):
            ref = self.headers.get("Origin") or self.headers.get("Referer") or ""
            host = self.headers.get("Host") or ""
            parsed = urllib.parse.urlparse(ref).netloc
            if not parsed and "://" not in ref:
                parsed = urllib.parse.urlparse("//" + ref).netloc
            if not ref or not parsed or (host and parsed != host):
                self._deny(403, "Origin not allowed")
                return
        payload = self._read_payload()
        if payload is None:
            return

        if path == "/api/iteration/start":
            import iteration as _iter_mod
            res = _iter_mod.t_spin_iteration(
                task=str(payload.get("task") or payload.get("goal") or payload.get("prompt") or ""),
                participants=payload.get("participants") or [],
                rubric=str(payload.get("rubric") or ""),
                max_rounds=int(payload.get("max_rounds", 3)),
                threshold=float(payload.get("threshold", payload.get("goal_score", 0.85))),
                round_seconds=int(payload.get("round_seconds", 900)),
                round_tokens=int(payload.get("round_tokens", 200000)),
                overseer=payload.get("overseer"),
                track=str(payload.get("track") or "main"),
            )
            if str(res).startswith("Error"):
                self.send_json({"error": str(res)}, status=400)
            else:
                self.send_json({"result": str(res)})
            return

        if path == "/api/iteration/stop":
            import iteration as _iter_mod
            iid = str(payload.get("iteration_id") or payload.get("id") or "").strip()
            if not iid:
                self.send_json({"error": "iteration_id is required"}, status=400)
                return
            res = _iter_mod.t_stop_iteration(iid)
            if str(res).startswith("Error"):
                self.send_json({"error": str(res)}, status=400)
            else:
                self.send_json({"result": str(res)})
            return

        if path == "/api/pair/approve":
            rid = str(payload.get("request_id") or "")
            res = pairing.approve(rid, scope=str(payload.get("scope") or "device"))
            self.send_json(res, status=400 if "error" in res else 200)
            return

        if path == "/api/pair/deny":
            rid = str(payload.get("request_id") or "")
            res = pairing.deny(rid, str(payload.get("reason") or ""))
            self.send_json(res, status=400 if "error" in res else 200)
            return

        if path == "/api/pair/revoke":
            res = pairing.revoke(str(payload.get("device_id") or ""))
            self.send_json(res, status=400 if "error" in res else 200)
            return

        if path == "/api/mcp/call":
            method = self.headers.get("Mcp-Method") or payload.get("method") or "tools/call"
            name = self.headers.get("Mcp-Name") or (payload.get("params", {}).get("name") if isinstance(payload.get("params"), dict) else "") or ""
            params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            args = params.get("arguments", {}) if "arguments" in params else payload.get("arguments", {})
            req_id = payload.get("id", 1)

            if method == "tools/list":
                tools = [dict(name=n, **spec) for n, (_f, spec) in getattr(_s, "TOOLS", {}).items()]
                self.send_json({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}})
                return
            if method == "tools/call":
                if not name:
                    self.send_json({"jsonrpc": "2.0", "error": {"code": -32602, "message": "Missing tool name"}}, status=400)
                    return
                # Check if user confirmation or permission is required (e.g. for protected actions)
                protected_actions = getattr(_s, "PROTECTED_ACTIONS", {"run_terminal", "approve_worker", "reject_worker", "stop_worker"})
                if name in protected_actions:
                    self.send_json({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "type": "input_required",
                            "prompt": "User confirmation required",
                            "name": name,
                            "arguments": args,
                        },
                    })
                    return
                entry = getattr(_s, "TOOLS", {}).get(name)
                if entry is None:
                    self.send_json({"jsonrpc": "2.0", "error": {"code": -32602, "message": f"unknown tool {name}"}}, status=400)
                    return
                tool_func = entry[0]
                if not isinstance(args, dict):
                    self.send_json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "arguments must be an object"}}, status=400)
                    return
                try:
                    res = tool_func(**args)
                except TypeError as e:
                    self.send_json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": str(e)}}, status=400)
                    return
                except Exception as e:
                    self.send_json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}}, status=500)
                    return
                self.send_json({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": str(res)}],
                    },
                })
                return
            self.send_json({"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Method not found: {method}"}}, status=404)
            return
        elif path == "/api/composer/context_slice":
            try:
                import composer_context
                res = composer_context.context_slice(
                    spec=str(payload.get("spec") or ""),
                    path=str(payload.get("path") or ""),
                    start_line=int(payload.get("start_line") or 0),
                    end_line=int(payload.get("end_line") or 0),
                    project_dir=str(payload.get("project_dir") or ""),
                    item=str(payload.get("item") or ""),
                )
                self.send_json(res)
            except FileNotFoundError as e:
                self._deny(404, str(e))
            except ValueError as e:
                msg = str(e)
                code = 422 if "Path exceeds" in msg else 400
                self._deny(code, msg)
            except Exception as e:
                self._deny(500, str(e))
            return
        elif path == "/api/reload":
            reloaded, msg = False, ""
            if hasattr(_s, "check_and_reload"):
                with contextlib.suppress(Exception):
                    reloaded, msg = _s.check_and_reload(force=True)
            self.send_json({"ok": True, "reloaded": reloaded, "message": msg, "version": _s.VERSION, "pid": os.getpid()})
            return
        elif path == "/api/composer/upload":
            info, err = attachments.save_upload(str(payload.get("name", "")), str(payload.get("type", "")), str(payload.get("data_base64", "")))
            if err:
                self.send_json({"error": err}, 400); return
            self.send_json(info); return
        elif path in ("/api/composer/principal", "/api/composer/rewind", "/api/composer/compact", "/api/composer/mail", "/api/composer/initiator"):
            if not self._gate(api=True):
                return
            action = path.rsplit("/", 1)[-1]
            status, obj = composer_api.dispatch(action, payload)
            if 200 <= status < 300:
                self.send_json(obj, status=status)
            else:
                self._deny(status, str(obj.get("error", "composer action failed")))
            return
        elif path == "/api/claim":
            res = _s.t_claim_file(filepath=str(payload.get("filepath", "")), holder=str(payload.get("holder", "")),
                               duration_seconds=payload.get("duration", 300), purpose=str(payload.get("purpose", "")),
                               role=str(payload.get("role", "implementer")))
        elif path == "/api/run_concurrent":
            tasks = payload.get("tasks")
            track = str(payload.get("track", "main"))
            from concurrency import run_concurrent
            res = run_concurrent(tasks=tasks, track=track)
            if res.get("status") == "refused":
                self._deny(400, res.get("error", "Refused concurrent execution"))
                return
            self.send_json({"ok": True, "data": res})
            return
        elif path == "/api/thread/rewind":
            thread_id = payload.get("thread_id")
            if not thread_id:
                self._deny(400, "Missing required 'thread_id'")
                return
            import forks
            at = payload.get("timestamp")
            if at is None:
                at = payload.get("at")
            note = str(payload.get("note") or "")
            res = forks.rewind_thread(
                thread_id=str(thread_id),
                at=float(at) if at is not None else None,
                note=note,
            )
            self.send_json(res)
            return
        elif path == "/api/thread/fork":
            thread_id = payload.get("thread_id")
            if not thread_id:
                self._deny(400, "Missing required 'thread_id'")
                return
            import forks
            at = payload.get("at")
            if at is None:
                at = payload.get("timestamp")
            carry_over = bool(payload.get("carry_over", True))
            base_dir = payload.get("base_dir")
            res = forks.fork_thread(
                thread_id=str(thread_id),
                at=float(at) if at is not None else None,
                carry_over=carry_over,
                base_dir=str(base_dir) if base_dir else None,
            )
            self.send_json(res)
            return
        elif path == "/api/release":
            res = _s.t_release_file(filepath=str(payload.get("filepath", "")), holder="hud-user", force=True)
        elif path == "/api/broadcast":
            res = _s._broadcast_track_internal(
                message=str(payload.get("message", "")), track=str(payload.get("track", "main")),
                files=[str(f) for f in payload.get("files") or []],
                decisions=[str(d) for d in payload.get("decisions") or []],
                station_id="user (HUD)")
        elif path == "/api/approve_worker":
            approve_id = str(payload.get("id", ""))
            if _s.WORKER_ID_RE.match(approve_id):
                _s._thread_record(_s._thread_root(_s._load_worker(approve_id) or {"id": approve_id}), "approved", approve_id,
                               data={"by": "hud"})
            res = _s._launch_worker(approve_id)  # The HUD token proves this is the user.
            if isinstance(res, str) and (res.startswith("Error") or res.startswith("Refused")):
                self._deny(400, res)
                return
        elif path == "/api/reject_worker":
            res = _s.t_reject_worker(worker_id=str(payload.get("id", "")), reason=str(payload.get("reason", "")))
        elif path == "/api/stop_worker":
            res = _s.t_stop_worker(worker_id=str(payload.get("id", "")), reason=str(payload.get("reason", "")))
        elif path == "/api/chat_budget":
            res = _s.set_chat_budget(int(payload.get("tokens") or 0), session_id=str(payload.get("session_id") or "") or None)
        elif path == "/api/budget":
            res = _s.set_budget(str(payload.get("scope", "")), int(payload.get("tokens") or 0))
        elif path == "/api/thread_settings":
            tid = str(payload.get("id") or "").strip()
            if not tid:
                self._deny(400, "Missing required 'id'")
                return
            if "permission_mode" in payload:
                pm = str(payload.get("permission_mode") or "").strip()
                if pm and pm not in _s.PERMISSION_MODES:
                    self._deny(400, f"Invalid permission_mode: {pm}")
                    return
            if "terminal_mode" in payload:
                tm = str(payload.get("terminal_mode") or "").strip()
                if tm and tm not in _s.PERMISSION_MODES:
                    self._deny(400, f"Invalid terminal_mode: {tm}")
                    return
            if "wake_rules" in payload:
                err = _wake_rules_error(payload.get("wake_rules"))
                if err:
                    self._deny(400, err)
                    return
            allowed_keys = {"permission_mode", "terminal_mode", "budget_tokens", "compaction", "compact_at_tokens", "wake_rules"}
            with _s._state_lock("thread_settings"):
                data = _s._load_json("thread_settings.json")
                data = dict(data) if isinstance(data, dict) else {}
                entry = dict(data.get(tid, {})) if isinstance(data.get(tid), dict) else {}
                for k in allowed_keys:
                    if k in payload:
                        v = payload[k]
                        if v == "" or v is None:
                            entry.pop(k, None)
                        else:
                            entry[k] = v
                if entry:
                    data[tid] = entry
                else:
                    data.pop(tid, None)
                _s._save_json("thread_settings.json", data)
            cfg = _s._load_json("config.json")
            universal = {
                "permission_mode": cfg.get("permission_mode") or "ask",
                "terminal_mode": cfg.get("terminal_mode") or "ask",
                "budget_tokens": cfg.get("budget_tokens") or None,
                "compaction": cfg.get("compaction") or "auto",
                "compact_at_tokens": cfg.get("compact_at_tokens") or 80000,
            }
            effective = {
                "permission_mode": entry.get("permission_mode") or universal["permission_mode"],
                "terminal_mode": entry.get("terminal_mode") or universal["terminal_mode"],
                "budget_tokens": entry.get("budget_tokens", universal["budget_tokens"]),
                "compaction": entry.get("compaction") or universal["compaction"],
                "compact_at_tokens": entry.get("compact_at_tokens", universal["compact_at_tokens"]),
            }
            self.send_json({"id": tid, "effective": effective, "universal": universal, "overrides": entry})
            return
        elif path == "/api/settings":
            res = {}
            if "caffeinate" in payload:
                c_val = str(payload.get("caffeinate", "")).lower().strip()
                if c_val not in ("off", "workers", "always"):
                    self._deny(403 if c_val else 400, f"Invalid caffeinate mode: {c_val}")
                    return
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["caffeinate"] = c_val
                    _s._save_json("config.json", cfg)
                _s.sync_caffeinate()
                res["caffeinate"] = c_val
            if "reply_style" in payload:
                val = str(payload.get("reply_style", "")).lower().strip()
                if val not in ("explanatory", "normal", "concise"):
                    self._deny(400, f"Invalid reply_style: {val}")
                    return
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json") or {}
                    rs = cfg.get("reply_style")
                    if not isinstance(rs, dict):
                        rs = {"default": "concise", "providers": {}}
                    rs["default"] = val
                    cfg["reply_style"] = rs
                    _s._save_json("config.json", cfg)
                res["reply_style"] = rs
                try:
                    import reply_styles
                    new_claude = reply_styles.style_for("claude")
                    if reply_styles.current_claude_output_style() != f"Subway {new_claude.capitalize()}":
                        reply_styles.apply_claude(new_claude)
                        res["claude_output_style"] = f"Subway {new_claude.capitalize()}"
                except Exception:
                    pass
            if "reply_style_provider" in payload:
                p = str(payload.get("reply_style_provider", "")).lower().strip()
                v = str(payload.get("reply_style_value", "")).lower().strip()
                if p not in ("claude", "antigravity", "opencode", "pi") or (v and v not in ("explanatory", "normal", "concise")):
                    self._deny(400, "Invalid reply_style_provider or reply_style_value")
                    return
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json") or {}
                    rs = cfg.get("reply_style")
                    if not isinstance(rs, dict):
                        rs = {"default": "concise", "providers": {}}
                    provs = rs.get("providers")
                    if not isinstance(provs, dict):
                        provs = {}
                    if v:
                        provs[p] = v
                    else:
                        provs.pop(p, None)
                    rs["providers"] = provs
                    cfg["reply_style"] = rs
                    _s._save_json("config.json", cfg)
                res["reply_style"] = rs
                try:
                    import reply_styles
                    new_claude = reply_styles.style_for("claude")
                    if reply_styles.current_claude_output_style() != f"Subway {new_claude.capitalize()}":
                        reply_styles.apply_claude(new_claude)
                        res["claude_output_style"] = f"Subway {new_claude.capitalize()}"
                except Exception:
                    pass
            if "git_branch_mode" in payload:
                b_val = bool(payload.get("git_branch_mode"))
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["git_branch_mode"] = b_val
                    _s._save_json("config.json", cfg)
                res["git_branch_mode"] = b_val
            if "governor_enabled" in payload:
                b_val = bool(payload.get("governor_enabled"))
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json") or {}
                    gov = cfg.get("governor")
                    if not isinstance(gov, dict):
                        gov = {"enabled": True, "probe_calls_per_turn": 6, "providers": {"claude": True, "antigravity": True, "opencode": True, "pi": True}}
                    gov["enabled"] = b_val
                    cfg["governor"] = gov
                    _s._save_json("config.json", cfg)
                res["governor_enabled"] = b_val
            if "governor_limit" in payload:
                try:
                    lim = int(payload.get("governor_limit"))
                except (ValueError, TypeError):
                    self._deny(400, "Invalid governor_limit")
                    return
                if not (2 <= lim <= 30):
                    self._deny(400, "governor_limit must be between 2 and 30")
                    return
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json") or {}
                    gov = cfg.get("governor")
                    if not isinstance(gov, dict):
                        gov = {"enabled": True, "probe_calls_per_turn": 6, "providers": {"claude": True, "antigravity": True, "opencode": True, "pi": True}}
                    gov["probe_calls_per_turn"] = lim
                    cfg["governor"] = gov
                    _s._save_json("config.json", cfg)
                res["governor_limit"] = lim
            if "governor_provider" in payload:
                prov = str(payload.get("governor_provider") or "").lower().strip()
                if prov not in ("claude", "antigravity", "opencode", "pi"):
                    self._deny(400, f"Invalid governor_provider: {prov}")
                    return
                p_on = bool(payload.get("governor_provider_on", True))
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json") or {}
                    gov = cfg.get("governor")
                    if not isinstance(gov, dict):
                        gov = {"enabled": True, "probe_calls_per_turn": 6, "providers": {"claude": True, "antigravity": True, "opencode": True, "pi": True}}
                    provs = gov.get("providers")
                    if not isinstance(provs, dict):
                        provs = {"claude": True, "antigravity": True, "opencode": True, "pi": True}
                    provs[prov] = p_on
                    gov["providers"] = provs
                    cfg["governor"] = gov
                    _s._save_json("config.json", cfg)
                res["governor_provider"] = prov
                res["governor_provider_on"] = p_on
            if "read_budget_reset" in payload:
                r_val = str(payload.get("read_budget_reset") or "").lower().strip()
                if r_val not in ("compaction", "forever"):
                    self._deny(400, f"Invalid read_budget_reset: {r_val}")
                    return
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json") or {}
                    rb = cfg.get("read_budget")
                    if not isinstance(rb, dict):
                        rb = {"reset": "compaction", "scope": "all"}
                    rb["reset"] = r_val
                    cfg["read_budget"] = rb
                    _s._save_json("config.json", cfg)
                res["read_budget_reset"] = r_val
            if "read_budget_scope" in payload:
                s_val = str(payload.get("read_budget_scope") or "").lower().strip()
                if s_val not in ("all", "provider"):
                    self._deny(400, f"Invalid read_budget_scope: {s_val}")
                    return
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json") or {}
                    rb = cfg.get("read_budget")
                    if not isinstance(rb, dict):
                        rb = {"reset": "compaction", "scope": "all"}
                    rb["scope"] = s_val
                    cfg["read_budget"] = rb
                    _s._save_json("config.json", cfg)
                res["read_budget_scope"] = s_val
            for int_key in ("nudge_builder_calls", "nudge_default_calls", "no_edit_tool_calls"):
                if int_key in payload:
                    val = payload.get(int_key)
                    try:
                        if isinstance(val, bool) or not isinstance(val, (int, str)):
                            raise ValueError()
                        int_val = int(val)
                    except (ValueError, TypeError):
                        self._deny(400, f"Invalid {int_key}: {val}")
                        return
                    if not (2 <= int_val <= 50):
                        self._deny(400, f"{int_key} out of range (must be 2..50): {int_val}")
                        return
                    with _s._state_lock("config"):
                        cfg = _s._load_json("config.json") or {}
                        cfg[int_key] = int_val
                        _s._save_json("config.json", cfg)
                    res[int_key] = int_val
            if payload.get("read_budget_reset_now") is True:
                guard.reset_principal_reads(None)
                res["read_budget_reset_now"] = True
            if "permission_mode" in payload:
                res["permission_mode"] = _s.set_permission_mode(str(payload.get("permission_mode", "")),
                                                              thread_id=str(payload.get("thread", "")))
            if "terminal_mode" in payload:
                res["terminal_mode"] = _s.set_terminal_mode(str(payload.get("terminal_mode", "")),
                                                          thread_id=str(payload.get("thread_id", "")))
            if payload.get("protected_reset") is True:
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg.pop("protected_commands", None)
                    cfg.pop("protected_exceptions", None)
                    _s._save_json("config.json", cfg)
                res["protected_reset"] = True
            for protected_key in ("protected_commands", "protected_exceptions"):
                if protected_key not in payload:
                    continue
                raw_list = payload.get(protected_key)
                if not isinstance(raw_list, list):
                    self._deny(400, f"{protected_key} must be a list of strings")
                    return
                if len(raw_list) > 200:
                    self._deny(400, f"{protected_key} can have at most 200 entries")
                    return
                cleaned_list = []
                for item in raw_list:
                    if not isinstance(item, str):
                        self._deny(400, f"{protected_key} must contain only strings")
                        return
                    value = item.strip()
                    if not value:
                        self._deny(400, f"{protected_key} entries must not be empty")
                        return
                    if len(value) > 200:
                        self._deny(400, f"{protected_key} entries must be 200 characters or fewer")
                        return
                    cleaned_list.append(value)
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg[protected_key] = cleaned_list
                    _s._save_json("config.json", cfg)
                res[protected_key] = cleaned_list
            if "compaction" in payload:
                c_val = str(payload.get("compaction", "")).lower().strip()
                if c_val not in ("suggest", "auto", "never"):
                    self._deny(400, f"Invalid compaction mode: {payload.get('compaction')}")
                    return
                at_val = payload.get("compact_at_tokens")
                res["compaction"] = _s.set_compaction(c_val, int(at_val) if isinstance(at_val, (int, float)) else None)
            if "hud_open_in" in payload:
                allowed_targets = {"auto", "browser", "app", "editor", "antigravity", "vscode", "cursor", "windsurf"}
                target = str(payload.get("hud_open_in") or "").strip().lower()
                if target not in allowed_targets:
                    self._deny(400, f"hud_open_in must be one of: {', '.join(sorted(allowed_targets))}")
                    return
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["hud_open_in"] = target
                    _s._save_json("config.json", cfg)
                res["hud_open_in"] = target
            if "hud_autostart" in payload:
                val = payload["hud_autostart"]
                if isinstance(val, bool):
                    b_val = val
                elif str(val).lower() in ("true", "1", "on"):
                    b_val = True
                elif str(val).lower() in ("false", "0", "off"):
                    b_val = False
                else:
                    self._deny(400, "hud_autostart must be a boolean")
                    return
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["hud_autostart"] = b_val
                    _s._save_json("config.json", cfg)
                res["hud_autostart"] = b_val
            if "hud_open_on_use" in payload:
                val = payload["hud_open_on_use"]
                if isinstance(val, bool):
                    b_val = val
                elif str(val).lower() in ("true", "1", "on"):
                    b_val = True
                elif str(val).lower() in ("false", "0", "off"):
                    b_val = False
                else:
                    self._deny(400, "hud_open_on_use must be a boolean")
                    return
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["hud_open_on_use"] = b_val
                    _s._save_json("config.json", cfg)
                res["hud_open_on_use"] = b_val
            if "terminal_sharing" in payload:
                val = payload["terminal_sharing"]
                if isinstance(val, bool):
                    b_val = val
                elif str(val).lower() in ("true", "1", "on"):
                    b_val = True
                elif str(val).lower() in ("false", "0", "off"):
                    b_val = False
                else:
                    self._deny(400, "terminal_sharing must be a boolean")
                    return
                _s.set_terminal_sharing(b_val)
                res["terminal_sharing"] = b_val
            if "god_mode" in payload:
                val = payload["god_mode"]
                b_val = val if isinstance(val, bool) else str(val).lower() in ("true", "1", "on")
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["god_mode"] = b_val
                    _s._save_json("config.json", cfg)
                res["god_mode"] = b_val
            if "worker_limits" in payload:
                val = payload["worker_limits"]
                b_val = val if isinstance(val, bool) else str(val).lower() in ("true", "1", "on")
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["worker_limits"] = b_val
                    _s._save_json("config.json", cfg)
                res["worker_limits"] = b_val
            if "max_workers" in payload:
                val = payload["max_workers"]
                try:
                    n_val = int(val) if val not in (None, "", "auto", 0, "0") else None
                except (ValueError, TypeError):
                    n_val = None
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    if n_val and n_val > 0:
                        cfg["max_workers"] = n_val
                    else:
                        cfg.pop("max_workers", None)
                    _s._save_json("config.json", cfg)
                res["max_workers"] = n_val if (n_val and n_val > 0) else "auto"
            if "local_memory_guard" in payload:
                val = payload["local_memory_guard"]
                b_val = val if isinstance(val, bool) else str(val).lower() in ("true", "1", "on")
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["local_memory_guard"] = b_val
                    _s._save_json("config.json", cfg)
                res["local_memory_guard"] = b_val
            if "local_concurrency_limits" in payload:
                val = payload["local_concurrency_limits"]
                b_val = val if isinstance(val, bool) else str(val).lower() in ("true", "1", "on")
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["local_concurrency_limits"] = b_val
                    _s._save_json("config.json", cfg)
                res["local_concurrency_limits"] = b_val
            if "max_local_workers" in payload:
                val = payload["max_local_workers"]
                try:
                    n_val = int(val) if val not in (None, "", "auto", 0, "0") else None
                except (ValueError, TypeError):
                    n_val = None
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    if n_val and n_val > 0:
                        cfg["max_local_workers"] = n_val
                    else:
                        cfg.pop("max_local_workers", None)
                    _s._save_json("config.json", cfg)
                res["max_local_workers"] = n_val if (n_val and n_val > 0) else "auto"
            if "git_branch_mode" in payload:
                val = payload["git_branch_mode"]
                b_val = val if isinstance(val, bool) else str(val).lower() in ("true", "1", "on")
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["git_branch_mode"] = b_val
                    _s._save_json("config.json", cfg)
                res["git_branch_mode"] = b_val
            if "notification_targets" in payload:
                val = payload["notification_targets"]
                if isinstance(val, list):
                    with _s._state_lock("config"):
                        cfg = _s._load_json("config.json")
                        cfg["notification_targets"] = val
                        _s._save_json("config.json", cfg)
                    res["notification_targets"] = val
            if "notification_webhook" in payload:
                val = str(payload["notification_webhook"]).strip()
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["notification_webhook"] = val
                    _s._save_json("config.json", cfg)
                res["notification_webhook"] = val
            if "edge_endpoints" in payload and isinstance(payload["edge_endpoints"], list):
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["edge_endpoints"] = payload["edge_endpoints"]
                    _s._save_json("config.json", cfg)
                res["edge_endpoints"] = payload["edge_endpoints"]
            # The HUD shows the reply as text, so say what changed rather than returning an object.
            names = {"compaction": "Compaction", "hud_autostart": "Start the HUD with Subway",
                     "hud_open_on_use": "Open the HUD when Subway is used",
                     "terminal_sharing": "Terminal sharing",
                     "god_mode": "God mode",
                     "worker_limits": "Worker limits",
                     "max_workers": "Max workers",
                     "local_memory_guard": "Local memory guard",
                     "local_concurrency_limits": "Local concurrency limits",
                     "max_local_workers": "Max local workers",
                     "git_branch_mode": "Git Branch Track Mode",
                     "notification_targets": "Notification channels",
                     "notification_webhook": "Notification webhook",
                     "edge_endpoints": "Edge endpoints",
                     "caffeinate": "Stay awake (caffeinate)"}
            said = [res[k] for k in ("permission_mode", "terminal_mode") if k in res]
            said += [f"{names[k]}: {'on' if v is True else 'off' if v is False else v}" for k, v in res.items() if k in names]
            if "protected_commands" in res:
                said.append(f"Protected commands: {len(res['protected_commands'])}")
            if "protected_exceptions" in res:
                said.append(f"Protected exceptions: {len(res['protected_exceptions'])}")
            if res.get("protected_reset"):
                said.append("Protected commands: back to defaults")
            res = "Saved. " + "; ".join(said) if said else "Nothing to change."
        elif path == "/api/verify":
            wid = str(payload.get("id") or payload.get("worker_id") or "")
            if not wid:
                self._deny(400, "Missing worker id")
                return
            v_res = _s.verify_worker(wid)
            status = v_res.get("status", "FAIL")
            line = v_res.get("line", "")
            self.send_json({"status": status, "line": line, "result": f"[{status}] {line}"})
            return
        elif path == "/api/notify_test":
            from notify_driver import dispatch_notification
            with _s._state_lock("config"):
                cfg = _s._load_json("config.json")
            dispatch_res = dispatch_notification(
                "Subway Context",
                "Test alert: Notifications are working in your IDE and desktop!",
                config=cfg
            )
            self.send_json({"ok": True, "delivered": dispatch_res})
            return
        elif path == "/api/models":
            res = _s.set_model_rankings(payload.get("models"))
        elif path == "/api/answer_permission":
            res = _s._answer_permission(str(payload.get("id", "")), str(payload.get("decision", "")), "hud-user")
        elif path == "/api/preflight":
            provider = str(payload.get("provider", "claude"))
            ok, msg = _s.check_budget_preflight(model=str(payload.get("model", "")), provider=provider,
                                             budget_session_pct=payload.get("budget_session_pct"),
                                             budget_weekly_pct=payload.get("budget_weekly_pct"),
                                             max_budget_usd=payload.get("max_budget_usd"))
            self.send_json({"ok": ok, "result": msg})
            return
        elif path == "/api/spawn":
            caps = payload.get("capabilities")
            res = _s.t_spin_worker(
                role=str(payload.get("role", "")), prompt=str(payload.get("prompt", "")),
                provider=str(payload.get("provider", "auto")), model=str(payload.get("model", "")),
                effort=str(payload.get("effort", "auto")), mode=str(payload.get("mode", "standard")),
                track=str(payload.get("track", "main")), project_dir=str(payload.get("project_dir") or os.environ.get("SUBWAY_PROJECT_DIR", "")),
                permission_mode=str(payload.get("permission_mode", "")),
                timeout_seconds=int(payload.get("timeout_seconds") or 0),
                max_tokens=int(payload.get("max_tokens") or 0), private=bool(payload.get("private")),
                capabilities=[str(c) for c in caps] if isinstance(caps, list) else None,
                budget_session_pct=payload.get("budget_session_pct"),
                budget_weekly_pct=payload.get("budget_weekly_pct"),
                max_budget_usd=payload.get("max_budget_usd"))
            if isinstance(res, str) and (res.startswith("Error") or res.startswith("Refused")):
                self._deny(400, res)
                return
        elif path == "/api/clear_history":
            res = _s.archive_history()
        elif path == "/api/terminal_sharing":
            val = payload.get("enabled")
            if val is None:
                self._deny(400, "missing enabled")
                return
            if isinstance(val, bool):
                b_val = val
            elif str(val).lower() in ("true", "1", "on"):
                b_val = True
            elif str(val).lower() in ("false", "0", "off"):
                b_val = False
            else:
                self._deny(400, "enabled must be a boolean")
                return
            _s.set_terminal_sharing(b_val)
            res = {"ok": True, "terminal_sharing": b_val}
        elif path == "/api/terminal_opt_in":
            tid = str(payload.get("terminal_id", ""))
            if not tid:
                self._deny(400, "missing terminal_id")
                return
            source = str(payload.get("source", ""))
            if source not in ("hook", "editor"):
                source = ""
            raw_app = str(payload.get("app", "") or "")
            app = raw_app if raw_app in ("terminal", "iterm") else ""
            raw_tty = str(payload.get("tty", "") or "")
            tty = raw_tty if re.match(r"^/dev/ttys[0-9]+$", raw_tty) else ""
            try:
                with _s._state_lock("terminals"):
                    rec = terminals.set_terminal_opt_in(
                        _s._terminals_dir(), tid, name=str(payload.get("name", "")),
                        window_id=str(payload.get("window_id", "")), folder=str(payload.get("folder", "")),
                        share_enabled=payload.get("share_enabled"), allow_commands=payload.get("allow_commands"),
                        capture_output=payload.get("capture_output"),
                        shell_integration=payload.get("shell_integration"), closed=payload.get("closed"),
                        source=source, app=app, tty=tty)
            except ValueError:
                self._deny(409, "terminal is closed")
                return
            res = {"ok": True, "terminal": rec}
        elif path == "/api/terminal_log":
            action = str(payload.get("action", ""))
            if action not in ("delete", "archive"):
                self._deny(400, "action must be 'delete' or 'archive'")
                return
            done: List[str] = []
            with _s._state_lock("terminals"):
                if payload.get("all_closed") is True:
                    for r in terminals.list_terminals(_s._terminals_dir()):
                        if r.get("closed") and terminals.close_log_action(_s._terminals_dir(), r["id"], action):
                            done.append(r["id"])
                else:
                    tid = str(payload.get("terminal_id", ""))
                    if not tid:
                        self._deny(400, "missing terminal_id")
                        return
                    if not terminals.close_log_action(_s._terminals_dir(), tid, action):
                        self._deny(404, "terminal not found or not closed")
                        return
                    done.append(tid)
            res = {"done": done, "action": action}
        elif path == "/api/terminal_event":
            if not _s.terminal_sharing_enabled():
                self._deny(403, "terminal sharing is off")
                return
            tid = str(payload.get("terminal_id", ""))
            if not tid:
                self._deny(400, "missing terminal_id")
                return
            raw_app = str(payload.get("app", "") or "")
            app = raw_app if raw_app in ("terminal", "iterm") else ""
            raw_tty = str(payload.get("tty", "") or "")
            tty = raw_tty if re.match(r"^/dev/ttys[0-9]+$", raw_tty) else ""
            cmd_text = str(payload.get("command", ""))
            start_ts = payload.get("start_ts")
            end_ts = payload.get("end_ts")
            with _s._state_lock("terminals"):
                term = terminals.get_terminal(_s._terminals_dir(), tid)
                if not term or not term.get("share_enabled"):
                    self._deny(403, "terminal has not opted in to sharing")
                    return
                terminals.record_command(
                    _s._terminals_dir(), tid, cmd_text,
                    exit_code=payload.get("exit_code"), start_ts=start_ts,
                    end_ts=end_ts, output=str(payload.get("output", "")),
                    caller_id=str(payload.get("caller_id") or "editor"),
                    name=str(payload.get("name", "")), window_id=str(payload.get("window_id", "")),
                    folder=str(payload.get("folder", "")), app=app, tty=tty)
                term = terminals.get_terminal(_s._terminals_dir(), tid)
                eff_app = (term or {}).get("app") or app
                eff_tty = (term or {}).get("tty") or tty
                should_capture = (
                    _s.terminal_sharing_enabled()
                    and bool((term or {}).get("share_enabled"))
                    and bool((term or {}).get("capture_output"))
                    and eff_app in ("terminal", "iterm")
                    and bool(eff_tty)
                )
            if should_capture:
                t = threading.Thread(
                    target=_s._capture_hook_output,
                    args=(_s._terminals_dir(), tid, cmd_text, start_ts, end_ts, eff_app, eff_tty),
                    daemon=True,
                )
                _s._last_capture_thread = t
                t.start()
            res = {"ok": True}
        elif path == "/api/run_result":
            rid = str(payload.get("run_id", ""))
            if not rid:
                self._deny(400, "missing run_id")
                return
            try:
                # Sharing switched off while the command ran: close the request but keep none of its output.
                sharing_on = _s.terminal_sharing_enabled()
                with _s._state_lock("terminals"):
                    rec = terminals.complete_run_request(
                        _s._terminals_dir(), rid, output=str(payload.get("output", "")) if sharing_on else "",
                        exit_code=payload.get("exit_code"),
                        error=str(payload.get("error", "")) if sharing_on else "terminal sharing was turned off; output not kept")
            except ValueError:
                self._deny(400, "invalid run_id")
                return
            if not rec:
                self._deny(404, "run request not found")
                return
            res = {"ok": True}
        elif path == "/api/archive_settings":
            try:
                out = _s.t_archive_settings(
                    compress_days=payload.get("compress_days"),
                    delete_days=payload.get("delete_days"),
                    branch_days=payload.get("branch_days"),
                    action=str(payload.get("action") or ""),
                )
            except ValueError as e:
                self._deny(400, str(e))
                return
            _s.mark_hud_cache_stale()
            self.send_json({"ok": True, "result": out})
            return
        elif path == "/api/wake_rules":
            rules = payload.get("rules")
            err = _wake_rules_error(rules)
            if err:
                self._deny(400, err)
                return
            provider = str(payload.get("provider") or "").strip()
            with _s._state_lock("config"):
                cfg = _s._load_json("config.json")
                cfg = dict(cfg) if isinstance(cfg, dict) else {}
                if provider:
                    byp = dict(cfg.get("wake_rules_by_provider") or {})
                    byp[provider] = {**(byp.get(provider) or {}), **rules}
                    cfg["wake_rules_by_provider"] = byp
                else:
                    cfg["wake_rules"] = {**(cfg.get("wake_rules") or {}), **rules}
                _s._save_json("config.json", cfg)
            _s.mark_hud_cache_stale()
            self.send_json({"ok": True})
            return
        elif path == "/api/wake_settings":
            try:
                out = _s.t_wake_settings(
                    chat_id=str(payload.get("chat_id") or "").strip(),
                    policy=str(payload.get("policy") or "").strip(),
                )
            except ValueError as e:
                self._deny(400, str(e))
                return
            if isinstance(out, str) and out.startswith("Error"):
                self._deny(400, out)
                return
            _s.mark_hud_cache_stale()
            self.send_json({"ok": True, "result": out})
            return
        elif path == "/api/principal_settings":
            updates = {}
            if "guard_enabled" in payload:
                v = payload.get("guard_enabled")
                if isinstance(v, str):
                    v = v.strip().lower() in ("1", "true", "yes", "on")
                updates["principal_guard_enabled"] = bool(v)
            if "max_reads" in payload:
                raw = payload.get("max_reads")
                try:
                    n = int(raw)
                except (TypeError, ValueError):
                    self._deny(400, "max_reads must be a non-negative integer")
                    return
                if n < 0:
                    self._deny(400, "max_reads must be a non-negative integer")
                    return
                updates["principal_max_reads"] = n
            if "mcp_read_tools" in payload:
                raw = payload.get("mcp_read_tools")
                if isinstance(raw, str):
                    tools = [x.strip() for x in raw.split(",") if x.strip()]
                elif isinstance(raw, list) and all(isinstance(x, str) for x in raw):
                    tools = [x.strip() for x in raw if x.strip()]
                else:
                    self._deny(400, "mcp_read_tools must be a comma-separated string or a list of strings")
                    return
                updates["principal_mcp_read_tools"] = tools
            if updates:
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    if not isinstance(cfg, dict):
                        cfg = {}
                    cfg.update(updates)
                    _s._save_json("config.json", cfg)
            _s.mark_hud_cache_stale()
            cfg = _s._load_json("config.json")
            if not isinstance(cfg, dict):
                cfg = {}
            self.send_json({"ok": True, "principal": {
                "guard_enabled": bool(cfg.get("principal_guard_enabled", True)),
                "max_reads": max(0, int(cfg.get("principal_max_reads", 30) or 0)),
                "mcp_read_tools": guard.principal_mcp_read_tools(cfg),
            }})
            return
        elif path == "/api/worker_unarchive":
            wid = str(payload.get("worker_id") or payload.get("id") or "").strip()
            if not wid:
                self._deny(400, "Missing required 'worker_id'")
                return
            try:
                out = _s.t_worker_unarchive(wid)
            except ValueError as e:
                self._deny(400, str(e))
                return
            if isinstance(out, str) and out.startswith("Error"):
                self._deny(400, out)
                return
            _s.mark_hud_cache_stale()
            self.send_json({"ok": True, "result": out})
            return
        elif path == "/api/worker_archive":
            wid = str(payload.get("worker_id") or payload.get("id") or "").strip()
            if not wid:
                self._deny(400, "Missing required 'worker_id'")
                return
            try:
                out = _s.t_worker_archive(wid)
            except ValueError as e:
                self._deny(400, str(e))
                return
            if isinstance(out, str) and (out.startswith("Error") or out.startswith("Refused")):
                self._deny(400, out)
                return
            _s.mark_hud_cache_stale()
            self.send_json({"ok": True, "result": out})
            return
        elif path == "/api/thread_action":
            tid = str(payload.get("id") or "").strip()
            action = str(payload.get("action") or "").strip().lower()
            if not tid:
                self._deny(400, "Missing required 'id'")
                return
            try:
                out = thread_state.apply_action(_s.SUBWAY_DIR, tid, action, payload.get("value"))
            except ValueError as e:
                self._deny(400, str(e))
                return
            _s.mark_hud_cache_stale()
            self.send_json(out)
            return
        elif path == "/api/exclusions":
            threads.save_exclusions(_s.SUBWAY_DIR, payload)
            self.send_json({"ok": True, "exclusions": threads.load_exclusions(_s.SUBWAY_DIR)})
            return
        else:
            self._deny(404, "Not found")
            return
        _s.mark_hud_cache_stale()
        self.send_json({"result": res})

class SubwayHUDServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        _cls, val, _tb = sys.exc_info()
        if isinstance(val, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)
