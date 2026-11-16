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


def _drop_moved(chats):
    """Hide a conversation's later provider segments; the conversation shows in its first chat."""
    try:
        from transplant import engine as _eng
        moved = _eng.moved_copies()
    except Exception:
        return chats
    return [c for c in chats if str(c.get("id") or "") not in moved] if moved else chats
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
import postmail


import fastmail


# Mutating routes whose successful answers are recorded in audit.jsonl (layer 2: who did what).
AUDITED = frozenset({"/api/answer_permission", "/api/approve_worker", "/api/reject_worker", "/api/stop_worker",
    "/api/spawn", "/api/peer/spawn", "/api/peer/approve_worker", "/api/run_concurrent", "/api/thread_action", "/api/home_prefs", "/api/mail", "/api/peer/mail", "/api/chat", "/api/chat/new", "/api/conversation/swap", "/api/peer/hub_call", "/api/peer/file", "/api/broadcast", "/api/settings",
    "/api/remote_workers/approve", "/api/remote_workers/reject", "/api/remote_workers/land",
    "/api/budget", "/api/chat_budget", "/api/pair/approve", "/api/pair/deny", "/api/pair/revoke", "/api/pair/rename",
    "/api/claim", "/api/release", "/api/landing/resolve", "/api/landing/drop", "/api/landing/merge", "/api/landing/discard", "/api/worker_archive", "/api/worker_unarchive", "/api/clear_history",
    "/api/iteration/start", "/api/iteration/stop", "/api/thread/rewind", "/api/thread/fork",
    "/api/peer/prepare", "/api/peer/complete", "/api/peers/allowance", "/api/peers/grants", "/api/peers/controller", "/api/peer/unlink", "/api/peer/link", "/api/peer/screen/request", "/api/screen/answer", "/api/screen/control"})


def _conversation_links() -> list:
    """Linked-provider handoffs: which chats continue as one conversation elsewhere."""
    import fastmail_lines
    raw = fastmail_lines._load_handoff_map()
    links = []
    for k, v in raw.items():
        if k.endswith(":needs_digest") or not isinstance(v, str) or ":" not in k:
            continue
        cid, prov = k.rsplit(":", 1)
        links.append({"from": cid, "to_provider": prov, "to": v})
    return links


def _principal_checkpoints(chat_id: str) -> list:
    """Principal swaps that involve this chat, oldest first, for the HUD's swap dividers."""
    cid = str(chat_id or "").strip().lstrip("@")
    if not cid:
        return []
    out = []
    try:
        threads_all = thread_state.load_all(_s.SUBWAY_DIR)
    except Exception:
        return []
    for tid, entry in threads_all.items():
        for h in entry.get("principal_history") or []:
            a, b = str(h.get("from_principal") or ""), str(h.get("to_principal") or "")
            if not a or a == b or cid not in (a, b, str(tid)):
                continue
            try:
                at = float(h.get("at") or 0)
            except (TypeError, ValueError):
                continue
            out.append({"thread": str(tid), "from": a, "to": b, "at": at, "reason": str(h.get("reason") or "")})
    out.sort(key=lambda x: x["at"])
    return out[-50:]


def _safe_git_ref(ref: str) -> bool:
    return bool(ref) and not ref.startswith("-") and not any(c.isspace() or c == "\x00" for c in ref)


def _uncommitted_diff(repo: str) -> str:
    """Return unified diff of uncommitted tracked changes plus untracked text files in repo."""
    chunks: List[str] = []
    p = subprocess.run(["git", "-C", repo, "diff", "--no-color", "HEAD"], capture_output=True, timeout=10)
    if p.returncode != 0:
        p = subprocess.run(["git", "-C", repo, "diff", "--no-color"], capture_output=True, timeout=10)
    if p.returncode == 0:
        tracked = p.stdout.decode("utf-8", "replace")
        if tracked.strip():
            chunks.append(tracked)
    st = subprocess.run(["git", "-C", repo, "status", "--porcelain", "-uall"], capture_output=True, timeout=5)
    if st.returncode == 0:
        for line in st.stdout.decode("utf-8", "replace").splitlines():
            if not line.startswith("?? "):
                continue
            rel = line[3:].strip().strip('"')
            full = os.path.realpath(os.path.join(repo, rel))
            if not full.startswith(os.path.realpath(repo) + os.sep) or not os.path.isfile(full):
                continue
            if os.path.getsize(full) > 128_000:
                continue
            try:
                raw = open(full, "rb").read()
                if b"\x00" in raw:
                    continue
                lines = raw.decode("utf-8", "replace").splitlines()
            except OSError:
                continue
            header = f"diff --git a/{rel} b/{rel}\nnew file mode 100644\n--- /dev/null\n+++ b/{rel}\n@@ -0,0 +1,{len(lines)} @@\n"
            chunks.append(header + "\n".join(f"+{ln}" for ln in lines) + "\n")
    return "\n".join(chunks)


def _is_git_repo(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    with contextlib.suppress(Exception):
        p = subprocess.run(["git", "-C", path, "rev-parse", "--is-inside-work-tree"], capture_output=True, timeout=3)
        return p.returncode == 0
    return False


def _default_git_repo() -> str:
    cwd = os.getcwd()
    if _is_git_repo(cwd):
        return cwd
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _is_git_repo(here):
        return here
    return cwd


def _landing_diff(query: Dict[str, List[str]]) -> Tuple[int, Dict[str, Any]]:
    """Return unified diff for a landing queue branch, overlap conflict, worker, or physical working tree."""
    cid = str((query.get("id") or [""])[0] or "").strip()
    wid = str((query.get("worker_id") or [""])[0] or "").strip()
    branch = str((query.get("branch") or [""])[0] or "").strip()
    base = str((query.get("base") or [""])[0] or "").strip()
    repo = str((query.get("repo") or [""])[0] or "").strip()
    working = str((query.get("working") or [""])[0] or "").strip() in ("1", "true", "yes")
    patch_path = ""
    worktree = ""
    merge_commit = ""

    if cid:
        import land_conflicts
        row = next((c for c in land_conflicts.list_conflicts(_s.SUBWAY_DIR) if str(c.get("id") or "") == cid), None)
        if not row:
            return 404, {"error": "conflict not found"}
        repo = repo or str(row.get("repo") or "")
        branch = branch or str(row.get("branch") or "")
        base = base or str(row.get("base") or "")
        wid = wid or str(row.get("worker_id") or "")

    if wid:
        if not _s.WORKER_ID_RE.match(wid):
            return 400, {"error": "invalid worker_id"}
        meta = _s._load_worker(wid) or {}
        if not meta:
            arch_json = os.path.join(_s._archive_dir(), f"{wid}.json")
            if os.path.exists(arch_json):
                with contextlib.suppress(Exception):
                    with open(arch_json, "r", encoding="utf-8") as af:
                        meta = json.load(af) or {}
        if meta:
            branch = branch or str(meta.get("branch_name") or "")
            base = base or str(meta.get("base_commit") or "")
            repo = repo or str(meta.get("directory") or meta.get("cwd") or "")
            patch_path = str(meta.get("patch_path") or "")
            worktree = str(meta.get("worktree") or "")
            merge_commit = str(meta.get("merge_commit") or "")

    repo = repo or _default_git_repo()
    if not os.path.isdir(repo):
        return 400, {"error": "repo directory not found"}

    if working:
        diff_text = _uncommitted_diff(repo)
        max_chars = 256_000
        return 200, {
            "ok": True,
            "diff": diff_text[:max_chars],
            "branch": "working-tree",
            "base": "HEAD",
            "repo": repo,
            "truncated": len(diff_text) > max_chars,
        }

    diff_text = ""
    if worktree and os.path.isdir(worktree):
        wt_uncommitted = _uncommitted_diff(worktree)
        wt_committed = ""
        if base and _safe_git_ref(base):
            p = subprocess.run(["git", "-C", worktree, "diff", "--no-color", f"{base}..HEAD"], capture_output=True, timeout=10)
            if p.returncode == 0:
                wt_committed = p.stdout.decode("utf-8", "replace")
        diff_text = "\n".join(x for x in (wt_committed, wt_uncommitted) if x.strip())

    if not diff_text and branch:
        if not _safe_git_ref(branch) or (base and not _safe_git_ref(base)):
            return 400, {"error": "invalid git ref"}
        ref_range = f"{base}..{branch}" if base else f"HEAD..{branch}"
        p = subprocess.run(["git", "-C", repo, "diff", "--no-color", ref_range], capture_output=True, timeout=10)
        if p.returncode != 0:
            p = subprocess.run(["git", "-C", repo, "show", "--no-color", "--format=", branch], capture_output=True, timeout=10)
        if p.returncode == 0:
            diff_text = p.stdout.decode("utf-8", "replace")

    if not diff_text and merge_commit and _safe_git_ref(merge_commit):
        p = subprocess.run(["git", "-C", repo, "show", "--no-color", "--format=", "--first-parent", merge_commit], capture_output=True, timeout=10)
        if p.returncode == 0:
            diff_text = p.stdout.decode("utf-8", "replace")

    if not diff_text and (wid or branch):
        for needle in [x for x in (wid, branch) if x]:
            p = subprocess.run(["git", "-C", repo, "log", "-n", "1", "--fixed-strings", "--grep", needle, "--format=%H"], capture_output=True, timeout=5)
            sha = p.stdout.decode("utf-8", "replace").strip() if p.returncode == 0 else ""
            if sha and _safe_git_ref(sha):
                p2 = subprocess.run(["git", "-C", repo, "show", "--no-color", "--format=", "--first-parent", sha], capture_output=True, timeout=10)
                if p2.returncode == 0 and p2.stdout.strip():
                    diff_text = p2.stdout.decode("utf-8", "replace")
                    break

    if not diff_text and patch_path and os.path.isfile(patch_path) and os.path.getsize(patch_path) <= 4_000_000:
        with contextlib.suppress(OSError):
            with open(patch_path, "r", encoding="utf-8", errors="replace") as pf:
                diff_text = pf.read()

    if not branch and not patch_path and not wid:
        return 400, {"error": "missing branch, id, or worker_id"}
    if not diff_text and not branch and not wid:
        return 404, {"error": "no diff available"}

    max_chars = 256_000
    truncated = len(diff_text) > max_chars
    return 200, {
        "ok": True,
        "diff": diff_text[:max_chars],
        "branch": branch or merge_commit or wid,
        "base": base,
        "repo": repo,
        "truncated": truncated,
    }


_BRANCH_REFS_CACHE: dict = {}  # repo -> (ts, existing:set, merged:set)


def _branch_refs(repo: str):
    """Local branch names and those merged into HEAD, cached 10 s per repo (2 git calls)."""
    now = time.time()
    hit = _BRANCH_REFS_CACHE.get(repo)
    if hit and now - hit[0] < 10:
        return hit[1], hit[2]
    existing, merged = set(), set()
    try:
        r = subprocess.run(["git", "-C", repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None, None
        existing = {l.strip() for l in r.stdout.splitlines() if l.strip()}
        m = subprocess.run(["git", "-C", repo, "branch", "--merged", "HEAD", "--format=%(refname:short)"], capture_output=True, text=True, timeout=5)
        if m.returncode == 0:
            merged = {l.strip() for l in m.stdout.splitlines() if l.strip()}
    except (OSError, subprocess.SubprocessError):
        return None, None
    _BRANCH_REFS_CACHE[repo] = (now, existing, merged)
    return existing, merged


def _settle_worker_branch(wid: str, action: str) -> Tuple[int, Dict[str, Any]]:
    """Merge or discard an unmerged worker branch."""
    if not wid or not _s.WORKER_ID_RE.match(wid):
        return 400, {"error": "invalid worker_id"}
    meta = _s._load_worker(wid)
    arch_json = os.path.join(_s._archive_dir(), f"{wid}.json")
    from_archive = False
    if not meta and os.path.exists(arch_json):
        with contextlib.suppress(Exception):
            with open(arch_json, "r", encoding="utf-8") as af:
                meta = json.load(af) or {}
                from_archive = True
    if not meta:
        return 404, {"error": "worker not found"}
    branch = str(meta.get("branch_name") or "").strip()
    if not branch or not _safe_git_ref(branch):
        return 400, {"error": "worker has no branch"}
    repo = str(meta.get("directory") or meta.get("cwd") or "") or _default_git_repo()
    if not os.path.isdir(repo):
        return 400, {"error": "repo directory not found"}
    if action == "merge":
        st = subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True, timeout=5)
        if st.stdout.strip():
            return 409, {"error": "Working tree has uncommitted changes. Commit or stash them before merging."}
        m = subprocess.run(["git", "-C", repo, "merge", "--no-ff", "-m", f"subway: merge worker '{meta.get('role') or wid}' ({wid})", branch], capture_output=True, timeout=30)
        if m.returncode != 0:
            subprocess.run(["git", "-C", repo, "merge", "--abort"], capture_output=True, timeout=5)
            err = (m.stderr or m.stdout).decode("utf-8", "replace").strip() or "merge conflict"
            return 409, {"error": f"Merge conflict: {err[:200]}"}
        subprocess.run(["git", "-C", repo, "branch", "-D", branch], capture_output=True, timeout=5)
        meta["merged"] = True
        meta["branch_deleted"] = True
    else:
        wt = str(meta.get("worktree") or "")
        if wt and os.path.isdir(wt):
            subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt], capture_output=True, timeout=10)
        subprocess.run(["git", "-C", repo, "branch", "-D", branch], capture_output=True, timeout=5)
        meta["branch_deleted"] = True
        meta["branch_discarded"] = True
    if from_archive:
        with contextlib.suppress(OSError):
            with open(arch_json, "w", encoding="utf-8") as af:
                json.dump(meta, af, indent=2)
    elif hasattr(_s, "_save_worker"):
        _s._save_worker(wid, meta)
    else:
        with contextlib.suppress(OSError):
            with open(os.path.join(_s.WORKERS_DIR, f"{wid}.json"), "w", encoding="utf-8") as wf:
                json.dump(meta, wf, indent=2)
    with contextlib.suppress(Exception):
        sse_stream.publish("worker", {"id": wid, "status": meta.get("status"), "merged": bool(meta.get("merged"))})
    return 200, {"ok": True, "result": f"Merged {branch}" if action == "merge" else f"Discarded {branch}"}


def fastmail_mute_station(station_id: str) -> Dict[str, Any]:
    """Append station_id to mail.muted_senders in config.json (dedupe)."""
    sid = str(station_id or "").strip()
    if not sid:
        raise ValueError("missing station_id")
    with _s._state_lock("config"):
        cfg = _s._load_json("config.json")
        if not isinstance(cfg, dict):
            cfg = {}
        raw = cfg.get("mail.muted_senders")
        if raw is None:
            nested = cfg.get("mail") if isinstance(cfg.get("mail"), dict) else {}
            raw = nested.get("muted_senders", "")
        muted = [s.strip() for s in str(raw or "").split(",") if s.strip()]
        if sid not in muted:
            muted.append(sid)
        cfg["mail.muted_senders"] = ", ".join(muted)
        _s._save_json("config.json", cfg)
    return {"ok": True, "muted_senders": muted}


_STATUS_CACHE = {"key": None, "at": 0.0, "val": None}
_STATUS_LOCK = threading.Lock()


def _status_cached(project: str):
    """Coalesce /api/status builds: the page asks from several places at once; reuse a build for 2 s."""
    with _STATUS_LOCK:
        c = _STATUS_CACHE
        if c["key"] == project and c["val"] is not None and time.monotonic() - c["at"] < 2.0:
            return c["val"]
        val = _s.get_hud_data(project=project)
        c.update(key=project, at=time.monotonic(), val=val)
        return val


class SubwayHUDHandler(http.server.BaseHTTPRequestHandler):
    server_version = "SubwayHUD"
    sys_version = ""

    def log_message(self, format, *args):
        pass

    # ---- request gate ----

    def parse_request(self):
        ok = super().parse_request()
        if ok:
            self._apply_tunnel_origin()
        return ok

    def _apply_tunnel_origin(self) -> None:
        """Requests forwarded by the tunnel connector arrive on loopback but come from a remote device.

        With the connector's secret they are marked remote (client address `tunnel:<device>`), so no
        loopback privilege applies. A wrong secret is refused in `_gate`. Without the header, nothing changes.
        """
        self._tunnel_bad = False
        presented = self.headers.get("X-Subway-Tunnel")
        if presented is None:
            return
        device = re.sub(r"[^0-9a-f]", "", (self.headers.get("X-Subway-Tunnel-Device") or "").lower())[:32] or "unknown"
        peer = (self.client_address or ("", 0))[0]
        self._tunnel_bad = not _tunnel_secret_ok(presented, peer)
        self.client_address = ("tunnel:" + device, 0)

    def _port(self) -> int:
        return int(self.server.server_address[1])

    def _host_ok(self) -> bool:
        """Refuses DNS rebinding: a hostile domain pointed at 127.0.0.1 still sends its own Host."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        allowed = {"127.0.0.1", "localhost", "::1"}
        allowed.update(x.strip().lower().rstrip(".") for x in os.environ.get("SUBWAY_HUD_HOSTS", "").split(",") if x.strip())
        host = host.rstrip(".")
        bound_host = getattr(_s, "HUD_HOST", None)
        if bound_host:
            h = bound_host.strip().lower().rstrip(".")
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

    def _presented_tokens(self) -> List[str]:
        """Every place a token may arrive, in the order `_authorized` checks them."""
        header = self.headers.get("X-Subway-Token", "").strip()
        auth = self.headers.get("Authorization", "").strip()
        bearer = auth[7:].strip() if auth.startswith("Bearer ") else ""
        q_token = ""
        # Query token kept for /api/events until the app sends the header (L1-M1); remove after.
        if hasattr(self, "path") and "?" in self.path and _query_token_allowed(urllib.parse.urlparse(self.path).path):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            q_token = (qs.get("token") or [""])[0].strip()
        return [t for t in (header, bearer, q_token) if t]

    def _authorized(self) -> bool:
        """Set `self._scope` and `self._device_id`; False when the request may not proceed."""
        self._scope = "full"
        self._device_id = ""
        self._revoked = False
        expected = _s.hud_token()
        candidates = self._presented_tokens()
        if expected:
            for cand in candidates:
                if hmac.compare_digest(cand, expected):
                    return True
        # A paired device token. Its scope decides what it may do (38c), and a revoked device
        # is reported as revoked rather than as a wrong token, so the phone can re-pair itself.
        for cand in candidates:
            try:
                device = pairing.find_device(cand)
            except Exception:
                device = None
            if not device:
                continue
            if device.get("revoked"):
                self._revoked = True
                continue
            self._scope = pairing._normalise_scope(device.get("scope"))
            self._device_id = str(device.get("device_id") or "")
            with contextlib.suppress(Exception):
                pairing.touch(cand, platform=pairing.platform_from_ua(self.headers.get("User-Agent", "")))
            return True
        return False

    def _security_headers(self, nonce: str = "") -> None:
        csp = ("default-src 'none'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
               "font-src https://fonts.gstatic.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
               f"script-src 'self' {repr('nonce-' + nonce) if nonce else ''}; "
               "frame-src 'self'; "
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

    def _send_raw_json(self, status: int, payload: bytes, extra_headers: Optional[Dict[str, str]] = None) -> None:
        """Resend a stored JSON answer as-is (idempotent replay)."""
        self.send_response(status)
        cors = self._cors_origin()
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self._security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _audit_actor(self) -> dict:
        dev = getattr(self, "_device_id", "") or ""
        if dev:
            name = next((d.get("name") or d.get("device") or "Device" for d in pairing.list_devices()
                         if d.get("device_id") == dev), "Device")
            return {"kind": "device", "id": dev, "name": name}
        return {"kind": "mac", "id": "mac", "name": "This Mac"}

    def _maybe_audit(self, status: int, data: Any = None) -> None:
        if getattr(self, "command", "") != "POST" or getattr(self, "_audited", False):
            return
        path = urllib.parse.urlparse(self.path).path
        if path not in AUDITED:
            return
        self._audited = True
        try:
            import audit
            target = audit.target_of(getattr(self, "_audit_payload", None))
            if path in audit.SPAWN_ACTIONS and "worker_id" not in target:
                wid = audit.worker_id_in(data)
                if wid:
                    target["worker_id"] = wid
            if "worker_id" not in target and isinstance(getattr(self, "_audit_payload", None), dict):
                pid = str(self._audit_payload.get("id") or "")
                if pid.startswith("worker_"):
                    target["worker_id"] = pid[:120]
            audit.record(self._audit_actor(), path, target, ok=status < 400)
        except Exception:
            pass

    def send_json(self, data: Any, status: int = 200) -> None:
        self._maybe_audit(status, data)
        try:
            payload = json.dumps(data).encode("utf-8")
            idem = getattr(self, "_idem", None)
            if idem:
                # Remember this answer so a retry with the same request key gets it back unchanged.
                self._idem = None
                from hud_server import idempotency
                idempotency.finish(idem[0], idem[1], status, payload)
            from hud_server import wire
            hdrs = getattr(self, "headers", None) or {}
            etag = None
            if status == 200 and getattr(self, "command", "") == "GET":
                etag = wire.etag_for(payload)
                if wire.etag_matches(hdrs.get("If-None-Match"), etag):
                    status, payload = 304, b""
            body, encoding = wire.encode_body(payload, hdrs.get("Accept-Encoding")) if payload else (payload, None)
            self.send_response(status)
            cors = self._cors_origin()
            vary = ["Accept-Encoding"]
            if cors:
                self.send_header("Access-Control-Allow-Origin", cors)
                vary.insert(0, "Origin")
            self.send_header("Vary", ", ".join(vary))
            if etag:
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")  # always revalidate; never serve stale state
            if status != 304:
                self.send_header("Content-Type", "application/json; charset=utf-8")
            if encoding:
                self.send_header("Content-Encoding", encoding)
            self.send_header("Content-Length", str(len(body)))
            self._security_headers()
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _deny(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status)

    def _gate(self, api: bool) -> bool:
        if getattr(self, "_tunnel_bad", False):
            self._deny(403, "Bad tunnel credential")
            return False
        if not self._host_ok():
            self._deny(403, "Host not allowed")
            return False
        if not self._origin_ok():
            self._deny(403, "Origin not allowed")
            return False
        if api and not self._authorized():
            if getattr(self, "_revoked", False):
                # Distinct from a bad token: the device was removed on the Mac, so the phone
                # must throw its key away and pair again rather than retry.
                self.send_json({"error": "This device was removed on the station", "reason": "device_revoked"}, 401)
            else:
                self._deny(401, "Missing or invalid token")
            return False
        if api:
            _scope = getattr(self, "_scope", "full")
            _route = urllib.parse.urlparse(self.path).path
            _method = getattr(self, "command", "GET")
            if pairing._normalise_scope(_scope) == pairing.SCOPE_PEER:
                import peers
                allowed, reason = peers.allows(getattr(self, "_device_id", "") or "", _route, _method)
            else:
                allowed, reason = pairing.scope_allows(_scope, _route, _method)
            if not allowed:
                self._deny(403, reason)
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

    def _hud_next_dir(self) -> str:
        return os.path.realpath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "hud_next"))

    def _serve_hud_next_page(self) -> None:
        hud_next_dir = self._hud_next_dir()
        idx = os.path.join(hud_next_dir, "index.html")
        try:
            with open(idx, "r", encoding="utf-8") as f:
                page = f.read()
        except OSError as e:
            self._deny(500, f"HUD page missing: {e}")
            return
        nonce = secrets.token_urlsafe(16)
        client_ip = ""
        with contextlib.suppress(Exception):
            client_ip = self.client_address[0]
        try:
            remote_loopback = ipaddress.ip_address(client_ip).is_loopback if client_ip else False
        except (ValueError, AttributeError):
            remote_loopback = False
        token = _s.hud_token() if remote_loopback else ""
        page = page.replace('<meta name="subway-token" content="">',
                            f'<meta name="subway-token" content="{token}">', 1)
        shim = (
            '<script nonce="__CSP_NONCE__">(function(){'
            'var m=document.querySelector(\'meta[name="subway-token"]\');'
            'var t=m?m.content:"";function s(u){try{return new URL(u,location.origin)}catch(e){return null}}'
            'var F=window.fetch;window.fetch=function(i,init){var u=typeof i==="string"?i:(i&&i.url)||"";var p=s(u);'
            'if(p&&p.origin===location.origin&&p.pathname.indexOf("/api/")===0&&t){'
            'if(init&&init.headers&&typeof init.headers.append==="function"){init=new Object(init);init.headers=new Headers(init.headers);init.headers.set("X-Subway-Token",t)}'
            'else{init=init?Object.assign({},init):{};var h=new Headers(init.headers||((typeof i!=="string"&&i&&i.headers)?i.headers:undefined));h.set("X-Subway-Token",t);init.headers=h}}'
            'return F.call(this,i,init)};'
            'var E=window.EventSource;if(E){window.EventSource=function(u,o){var p=s(u);'
            'if(p&&p.origin===location.origin&&p.pathname.indexOf("/api/")===0&&t&&!p.searchParams.has("token")){p.searchParams.set("token",t);u=p.toString()}'
            'return new E(u,o)};window.EventSource.prototype=E.prototype;}'
            '})();</script>'
        ).replace("__CSP_NONCE__", nonce)
        if "</head>" in page:
            page = page.replace("</head>", shim + "\n</head>", 1)
        else:
            page = shim + page
        payload = page.encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self._security_headers(nonce)
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _serve_hud_next_asset(self, path: str) -> None:
        """Serve hud_next static root files and allowed subdirs (icons/, wallpaper/)."""
        rel = path.lstrip("/").split("?", 1)[0]
        if os.path.isabs(rel):
            rel = rel[1:]
        hud_next_dir = self._hud_next_dir()
        root = hud_next_dir
        if re.fullmatch(r"wallpaper/custom\.(png|jpg|webp)", rel):
            # The uploaded wallpaper lives in the user's state dir, not the repo.
            root = os.path.realpath(os.path.join(_s.SUBWAY_DIR, "wallpaper"))
            full = os.path.realpath(os.path.join(root, os.path.basename(rel)))
        else:
            full = os.path.realpath(os.path.join(hud_next_dir, rel))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            self._deny(404, "Not found")
            return
        base = os.path.basename(rel)
        if base.startswith(".") or base.endswith(".txt") or base.endswith(".bak"):
            self._deny(404, "Not found")
            return
        if rel.startswith("icons/") or rel.startswith("wallpaper/"):
            allowed_sub = rel.startswith("icons/") or rel.startswith("wallpaper/")
            if not allowed_sub or "/" not in rel.rstrip("/") or rel.count("/") != 1:
                self._deny(404, "Not found")
                return
        elif "/" in rel:
            self._deny(404, "Not found")
            return
        ext = os.path.splitext(rel)[1].lower()
        kinds = {
            ".js": "text/javascript",
            ".css": "text/css",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".woff2": "font/woff2",
        }
        kind = kinds.get(ext)
        if not kind:
            self._deny(404, "Not found")
            return
        if not re.fullmatch(r"(?:icons/|wallpaper/)?[A-Za-z0-9._-]+\.(?:js|css|svg|png|jpg|jpeg|webp|woff2)", rel):
            self._deny(404, "Not found")
            return
        try:
            with open(full, "rb") as f:
                payload = f.read()
        except OSError:
            self._deny(404, "Not found")
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", f"{kind}; charset=utf-8" if kind.startswith("text/") or kind == "image/svg+xml" else kind)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
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
        """Server-sent events via SSEBroadcaster (Roadmap Item 38a2): simultaneous events to multiple concurrent subscribers.

        A reconnecting client sends `Last-Event-ID` (or `?since=`); the frames it missed are
        replayed first, so a phone that was suspended for ten minutes loses nothing.
        """
        b = sse_stream.get_broadcaster()
        sub_q = b.subscribe()
        since_raw = (self.headers.get("Last-Event-ID") or "").strip()
        if not since_raw:
            q = urllib.parse.urlparse(self.path).query
            since_raw = (urllib.parse.parse_qs(q).get("since", [""])[0] or "").strip()
        try:
            since = int(since_raw)
        except (TypeError, ValueError):
            since = -1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        cors = self._cors_origin()
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
            self.send_header("Vary", "Origin")
        self._security_headers()
        self.end_headers()

        replay = b.replay_since(since) if since >= 0 else []
        init_msg = f"event: connect\ndata: {json.dumps({'status': 'connected', 'subscribers': b.subscriber_count(), 'replayed': len(replay), 'last_seq': b.last_seq(), 'gap': _replay_gap(since, b.oldest_seq())})}\n\n"
        try:
            self.wfile.write(init_msg.encode("utf-8"))
            for frame in replay:
                self.wfile.write(frame.encode("utf-8"))
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

    def _stream_chat(self, chat_id: str, source: str) -> None:
        """Live SSE for one chat: a `chat` event (chat_live JSON) whenever the transcript changes."""
        from cli_tools import chats as _chats

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        cors = self._cors_origin()
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
            self.send_header("Vary", "Origin")
        self._security_headers()
        self.end_headers()

        def _sig(p: str):
            try:
                st = os.stat(p)
                return (st.st_mtime, st.st_size)
            except OSError:
                return None

        stop = threading.Event()
        deadline = time.monotonic() + 900.0
        path: Optional[str] = None
        last_sig = None
        last_call = 0.0
        last_json: Optional[str] = None
        last_sent = time.monotonic()
        extra: Optional[str] = None
        last_extra_sig = None
        try:
            while time.monotonic() < deadline:
                now = time.monotonic()
                due = False
                if not path:
                    due = path is None or now - last_call >= 1.5
                else:
                    sig = _sig(path)
                    if sig != last_sig:
                        last_sig = sig
                        due = True
                    if _sig(extra) != last_extra_sig:
                        due = True
                if due:
                    last_call = now
                    try:
                        data = _chats.chat_live(chat_id, source=source)
                    except (ValueError, LookupError, KeyError) as e:
                        msg = f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                        return
                    extra = None
                    try:
                        from transplant import engine as _eng
                        _root, _rec = _eng.conversation_for(chat_id)
                        if _rec and len(_rec.get("segments") or []) > 1:
                            merged, conv = _eng.merge_conversation_turns(chat_id, data.get("turns") or [])
                            data["turns"], data["conversation"], data["total"] = merged, conv, len(merged)
                            cur = _eng.current_segment(_rec) or {}
                            sent = cur.get("sent") or []
                            if sent:
                                last_sent_entry = sent[-1]
                                extra = last_sent_entry.get("log")   # headless run log of the latest send into the current segment
                                if extra:
                                    live_text = _eng._strip_cli_noise(_eng._log_tail(extra, last_sent_entry.get("offset") or 0))
                                    data["live"] = {"text": live_text, "user": last_sent_entry.get("text") or ""}
                    except Exception:
                        pass
                    path = str(data.get("path") or "")
                    if path:
                        last_sig = _sig(path)
                    last_extra_sig = _sig(extra)
                    payload = json.dumps(data, ensure_ascii=False, default=str)
                    if payload != last_json:
                        last_json = payload
                        self.wfile.write(f"event: chat\ndata: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        last_sent = time.monotonic()
                if time.monotonic() - last_sent >= 15.0:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_sent = time.monotonic()
                sleep_s = 0.1 if extra else 0.3
                if path and os.path.exists(path) and hasattr(select, "kqueue"):
                    kq = None
                    fd = -1
                    try:
                        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                        kq = select.kqueue()
                        kq.control([select.kevent(fd, select.KQ_FILTER_VNODE,
                            select.KQ_EV_ADD | select.KQ_EV_CLEAR | select.KQ_EV_ONESHOT,
                            select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_ATTRIB | select.KQ_NOTE_RENAME)], None)
                        woke = bool(kq.control(None, 1, sleep_s))
                        if not woke:
                            stop.wait(max(0.0, sleep_s - (time.monotonic() - now)))
                    except (OSError, ValueError):
                        stop.wait(sleep_s)
                    finally:
                        if kq is not None:
                            try:
                                kq.close()
                            except Exception:
                                pass
                        if fd >= 0:
                            try:
                                os.close(fd)
                            except OSError:
                                pass
                else:
                    stop.wait(0.3)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _stream_worker_log(self, query: Dict[str, List[str]]) -> None:
        """Live SSE for a worker log: a `log` event whenever the tail changes, `done` on terminal status."""
        wid = query.get("id", [""])[0]
        if not _s.WORKER_ID_RE.match(wid):
            self._deny(400, "invalid worker id")
            return

        archive_dir = _s._archive_dir()

        def _read_state() -> Tuple[str, Dict[str, object]]:
            meta = _s._load_worker(wid) or {}
            if not meta:
                arch_json = os.path.join(archive_dir, f"{wid}.json")
                if os.path.exists(arch_json):
                    with contextlib.suppress(Exception):
                        with open(arch_json, "r", encoding="utf-8") as af:
                            meta = json.load(af) or {}
            log_path = meta.get("log_file") or _s._log_file(wid)
            archived = os.path.join(archive_dir, f"{wid}.log")
            if not os.path.exists(log_path) and os.path.exists(archived):
                log_path = archived
            log = ""
            if os.path.exists(log_path):
                log = _s._log_tail(log_path, 200)
            else:
                for gz_path in (f"{log_path}.gz", os.path.join(archive_dir, f"{wid}.log.gz")):
                    if os.path.exists(gz_path):
                        import gzip
                        with contextlib.suppress(Exception):
                            with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as gf:
                                log = "".join(gf.readlines()[-200:]).strip()
                        break
            payload = json.dumps({"id": wid, "log": log, "status": meta.get("status"),
                                  "exit_code": meta.get("exit_code")}, separators=(",", ":"))
            return log_path, meta, payload

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        cors = self._cors_origin()
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
            self.send_header("Vary", "Origin")
        self._security_headers()
        self.end_headers()

        deadline = time.monotonic() + 900.0
        last_payload: Optional[str] = None
        last_sent = time.monotonic()
        terminal = {"completed", "failed", "stopped", "rejected", "cancelled", "error"}
        try:
            while time.monotonic() < deadline:
                try:
                    log_path, meta, payload = _read_state()
                except Exception:
                    log_path, meta, payload = "", {}, json.dumps({"id": wid, "log": "", "status": None,
                                                                 "exit_code": None}, separators=(",", ":"))
                if payload != last_payload:
                    self.wfile.write(f"event: log\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last_payload = payload
                    last_sent = time.monotonic()
                elif time.monotonic() - last_sent >= 15.0:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_sent = time.monotonic()
                if meta.get("status") in terminal and payload == last_payload:
                    self.wfile.write(b"event: done\ndata: {}\n\n")
                    self.wfile.flush()
                    return
                fd = kq = None
                try:
                    if log_path and os.path.exists(log_path) and hasattr(select, "kqueue"):
                        fd = os.open(log_path, os.O_RDONLY)
                        kq = select.kqueue()
                        ev = select.kevent(fd, select.KQ_FILTER_VNODE,
                                           select.KQ_EV_ADD | select.KQ_EV_ENABLE,
                                           select.NOTE_WRITE | select.NOTE_EXTEND | select.NOTE_ATTRIB | select.NOTE_RENAME)
                        kq.control([ev], 0)
                        kq.control(None, 1, 1.0)
                    else:
                        time.sleep(0.5)
                finally:
                    if kq is not None:
                        with contextlib.suppress(Exception):
                            kq.close()
                    if fd is not None:
                        with contextlib.suppress(Exception):
                            os.close(fd)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _stream_diff(self, query: Dict[str, List[str]]) -> None:
        """Live SSE for the landing diff: a `diff` event whenever the patch changes."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        cors = self._cors_origin()
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
            self.send_header("Vary", "Origin")
        self._security_headers()
        self.end_headers()

        deadline = time.monotonic() + 900.0
        last_payload: Optional[str] = None
        last_sent = time.monotonic()
        try:
            while time.monotonic() < deadline:
                code, body = _landing_diff(query)
                payload = json.dumps({**body, "status": code}, separators=(",", ":"))
                if payload != last_payload:
                    self.wfile.write(f"event: diff\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last_payload = payload
                    last_sent = time.monotonic()
                elif time.monotonic() - last_sent >= 15.0:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_sent = time.monotonic()
                time.sleep(0.25)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _stream_diagnostics(self) -> None:
        """Live SSE for the diagnostics panel: a `diag` event whenever the sample changes."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        cors = self._cors_origin()
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
            self.send_header("Vary", "Origin")
        self._security_headers()
        self.end_headers()

        from hud_server import diagnostics as _diag

        deadline = time.monotonic() + 900.0
        try:
            first = _diag.compute()
            last_at = float(_diag._cache.get("at") or 0.0)
            self.wfile.write(f"event: diag\ndata: {json.dumps(first, ensure_ascii=False, default=str)}\n\n".encode("utf-8"))
            self.wfile.flush()
            while time.monotonic() < deadline:
                d = _diag.wait_sample(last_at, timeout=15.0)
                if d is None:
                    self.wfile.write(b": ping\n\n")
                else:
                    last_at = float(_diag._cache.get("at") or last_at)
                    self.wfile.write(f"event: diag\ndata: {json.dumps(d, ensure_ascii=False, default=str)}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_GET(self):
        pr = urllib.parse.urlparse(self.path)
        path = pr.path
        query = urllib.parse.parse_qs(pr.query)

        if path.startswith("/api/mcp/proxy/"):
            if self._gate(api=False):
                import mcp_proxy
                mcp_proxy.handle(self, "GET")
            return
        if path == "/healthz":
            if self._gate(api=False):
                self.send_json({"service": "subway", "version": _s.VERSION, "pid": os.getpid()})
            return
        if path == "/api/mcp/connect":
            if not self._gate(api=True):
                return
            import mcp_connect_api
            status, body = mcp_connect_api.handle_get((query.get("project") or [""])[0])
            self.send_json(body, status=status)
            return
        if path == "/api/mcp/hub/servers":
            if not self._gate(api=True):
                return
            import hub_tools
            status, body = hub_tools.handle_get_servers((query.get("project") or [""])[0])
            self.send_json(body, status=status)
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
                self._serve_hud_next_page()
            return
        if path in ("/classic", "/classic/"):
            if self._gate(api=False):
                self._serve_page()
            return
        if path.startswith("/hud/"):
            if self._gate(api=False):
                self._serve_asset(path)
            return
        if path != "/" and not path.startswith("/api/"):
            # hud_next static files at root and allowed subdirs
            if re.fullmatch(r"/[A-Za-z0-9._-]+\.(?:js|css|svg|png|jpg|jpeg|webp|woff2)", path) or \
               re.fullmatch(r"/(?:icons|wallpaper)/[A-Za-z0-9._-]+\.(?:js|css|svg|png|jpg|jpeg|webp|woff2)", path):
                if self._gate(api=False):
                    self._serve_hud_next_asset(path)
                return
        if not path.startswith("/api/"):
            self._deny(404, "Not found")
            return
        if path == "/api/collab/people" or path.startswith("/api/collab/people/"):
            if not self._gate(api=True):
                return
            from hud_server import collab_people
            actor = str(getattr(self, "_device_id", "") or "master")
            master = not bool(getattr(self, "_device_id", ""))
            try:
                status, body = collab_people.handle_get(self.path, actor, master=master)
                if status is not None:
                    self.send_json(body, status=status)
            except Exception as exc:
                self._deny(500, str(exc))
            return
        if path.startswith("/api/collab/"):
            if not self._gate(api=True):
                return
            from hud_server import collab_api
            res = collab_api.handle_get(path)
            if res is not None:
                self.send_json(res[1], status=res[0])
                return
        if path == "/api/settings":
            if not self._gate(api=True):
                return
            from hud_server import settings_api
            status, body = settings_api.handle_get({"group": (query.get("group") or [""])[0]})
            self.send_json(body, status=status)
            return
        if path == "/api/pair/status":
            # The polling phone has no token yet; the host gate still applies, and nothing is
            # handed over unless the Mac approved this exact request id.
            if self._gate(api=False):
                ip = str(self.client_address[0]) if self.client_address else ""
                if not pairing.status_rate_ok(ip):
                    self.send_json({"error": "Too many pairing checks, wait a minute",
                                    "retry_after": 60}, status=429)
                    return
                st = pairing.status((query.get("request_id") or [""])[0])
                if st.get("status") == "unknown":
                    pairing.note_status_miss(ip)
                self.send_json(st)
            return
        if not self._gate(api=True):
            return

        if path == "/api/events":
            self._stream_events()
            return

        if path == "/api/chat_stream":
            cid = query.get("id", [""])[0]
            if not cid:
                self._deny(400, "Missing chat id")
                return
            self._stream_chat(cid, query.get("source", ["all"])[0])
            return

        if path == "/api/debug/stacks":
            import sys, threading, traceback
            root = os.getcwd()
            tmap = {t.ident: t for t in threading.enumerate()}
            stack_rows = []
            for ident, fr in sys._current_frames().items():
                t = tmap.get(ident)
                stack = []
                for fi in traceback.extract_stack(fr)[-25:]:
                    fn = fi.filename
                    if fn.startswith(root + os.sep):
                        fn = os.path.relpath(fn, root)
                    stack.append(f"{fn}:{fi.lineno} {fi.name}")
                stack_rows.append({"name": t.name if t else None, "ident": ident, "daemon": t.daemon if t else None, "stack": stack})
            self.send_json({"threads": stack_rows, "count": len(stack_rows)})
            return

        if path == "/api/manifest":
            import vfs
            try:
                limit = (query.get("limit") or [str(vfs.MAX_FILES)])[0]
                self.send_json(vfs.manifest(query.get("project", [""])[0], limit=limit))
            except ValueError as e:
                self._deny(400, str(e))
            except FileNotFoundError as e:
                self._deny(404, str(e))
            return

        if path == "/api/blob":
            import vfs
            rel = (query.get("path") or query.get("p") or [""])[0]
            sha = (query.get("sha") or [""])[0]
            try:
                self.send_json(vfs.blob(
                    project_dir=query.get("project", [""])[0],
                    rel=rel,
                    sha=sha,
                    subway_dir=str(_s.SUBWAY_DIR),
                ))
            except FileNotFoundError as e:
                self._deny(404, str(e))
            except ValueError as e:
                self._deny(400, str(e))
            return

        if path == "/api/outbox/idempotency":
            import vfs
            entries = vfs._idem_load(str(_s.SUBWAY_DIR))
            self.send_json({"count": len(entries), "keys": sorted(entries)[:50]})
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
            d = _status_cached(query.get("project", [""])[0])
            if query.get("view", [""])[0] == "next":
                from hud_server import next_view
                models = query.get("models", ["0"])[0] in ("1", "true", "yes")
                self.send_json(next_view.slim(d if isinstance(d, dict) else {}, models=models))
                return
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

        if path == "/api/chat_read":
            from cli_tools import chats as _chats
            chat_wanted = query.get("id", [""])[0]
            if not chat_wanted:
                self._deny(400, "Missing chat id")
                return
            source_wanted = query.get("source", ["all"])[0] or "all"
            try:
                turns_wanted = int(query.get("turns", ["20"])[0] or 20)
            except ValueError:
                turns_wanted = 20
            turns_wanted = min(100, max(1, turns_wanted))
            try:
                payload = _chats.chat_turns(chat_wanted, source=source_wanted, turns=turns_wanted)
                try:
                    from transplant import engine as _eng
                    _merged, _conv = _eng.merge_conversation_turns(chat_wanted, payload.get("turns") or [])
                    if _conv:
                        payload["turns"], payload["conversation"] = _merged, _conv
                except Exception:
                    pass
                payload["checkpoints"] = _principal_checkpoints(chat_wanted)
                self.send_json(payload)
            except ValueError as e:
                self._deny(400, str(e))
            except (LookupError, OSError, KeyError) as e:
                self._deny(404, str(e))
            return

        if path == "/api/conversation/links":
            self.send_json({"links": _conversation_links()})
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


        if path == "/api/delivery_coverage":
            try:
                import fastmail_lines
                self.send_json(fastmail_lines.delivery_coverage())
            except Exception as e:
                self._deny(500, str(e))
            return

        if path == "/api/thread_state":
            tid = query.get("id", [""])[0].strip()
            if not tid:
                self._deny(400, "Missing thread id")
                return
            st_ = thread_state.get(_s.SUBWAY_DIR, tid) or {}
            self.send_json({k: st_.get(k) for k in ("principal_id", "initiator_id", "auto_handoff") if k in st_})
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
            project_filter = (query.get("folder") or query.get("project") or [""])[0].strip()
            open_only = (query.get("open_only") or [""])[0].strip() in ("1", "true")
            try:
                limit = min(2000, max(1, int(query.get("limit", ["200"])[0])))
            except (TypeError, ValueError):
                limit = 200
            project_filter = (query.get("folder") or query.get("project") or [""])[0].strip()
            open_only = (query.get("open_only") or [""])[0].strip() in ("1", "true")
            if query.get("lite", ["0"])[0].lower() in ("1", "true", "yes"):
                # Mentions need only names: skip the worker join and reuse the chat scan for 15 s.
                key = (source, include_subagents)
                cache = self.__class__.__dict__.get("_lite_chats") or {}
                hit = cache.get(key)
                if not hit or time.time() - hit[0] > 15:
                    lc = _s.all_chats(source, include_subagents=include_subagents)
                    lc = _drop_moved(lc)
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
            chats = _drop_moved(chats)
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
            if project_filter:
                thread_items = [t for t in thread_items if str(t.get("project") or "") == project_filter]
            if open_only:
                thread_items = [t for t in thread_items if t.get("open")]
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

        if path == "/api/home_prefs":
            prefs = _s._load_json("home_prefs.json")
            if not isinstance(prefs, dict):
                prefs = {}
            self.send_json({
                "scopes": prefs.get("scopes") if isinstance(prefs.get("scopes"), dict) else {},
                "closed": prefs.get("closed") if isinstance(prefs.get("closed"), dict) else {},
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
                    "small_edit_lines": max(0, int(cfg.get("principal_small_edit_lines", 0) or 0)),
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

        if path == "/api/transplant/providers":
            try:
                from transplant import registry as _treg, probe as _tprobe, learn as _tlearn
                cache = _tprobe._load()
                out = []
                for pid, ad in sorted(_treg.adapters().items()):
                    e = cache.get(_tprobe.fingerprint_key(ad))
                    try:
                        moved, where = _tlearn.drifted(ad)
                    except Exception:
                        moved, where = False, ""
                    lp = ({"ok": bool(e.get("ok")), "ts": float(e.get("ts") or 0), "detail": str(e.get("detail") or "")[-300:]}
                          if isinstance(e, dict) else None)
                    out.append({"id": pid, "origin": ad.origin, "drifted": bool(moved), "drift_detail": str(where or ""), "last_probe": lp})
                for pid in _tlearn.unmapped():
                    out.append({"id": pid, "origin": "unmapped", "drifted": False, "drift_detail": "", "last_probe": None})
                self.send_json({"providers": out})
            except Exception as exc:
                self.send_json({"providers": [], "error": str(exc)})
            return

        if path == "/api/models":
            import local_models as _lm
            cat = _s.model_catalog()
            providers_map = {}
            for m in cat:
                providers_map.setdefault(m.get("provider") or "unknown", []).append(m)
            claude_cfg = _lm._claude_account_catalog() if hasattr(_lm, "_claude_account_catalog") else {}
            if isinstance(claude_cfg, dict) and isinstance(claude_cfg.get("models"), list) and claude_cfg["models"]:
                providers_map["claude"] = [
                    {
                        "provider": "claude",
                        "model": cm.get("id") or "",
                        "name": cm.get("name") or cm.get("id") or "",
                        "efforts": list((cm.get("runtime") or {}).get("effort_levels") or []),
                        "hint": cm.get("description") or "",
                    }
                    for cm in claude_cfg["models"]
                    if isinstance(cm, dict) and cm.get("id")
                ]
            providers_list = []
            for pid, group in providers_map.items():
                models_out = []
                seen_ids = set()
                for m in group:
                    mid = m.get("model") or ""
                    if not mid:
                        continue
                    effs = m.get("efforts")
                    out_id = mid
                    out_name = m.get("label") or m.get("name") or mid
                    if pid == "antigravity" and hasattr(_lm, "_antigravity_base_and_efforts"):
                        out_id, agy_effs = _lm._antigravity_base_and_efforts(mid)
                        if effs is None:
                            effs = agy_effs
                        if effs:
                            out_name = re.sub(r"\s*\([^()]+\)\s*$", "", str(out_name)).strip() or out_id
                    elif effs is None and hasattr(_s, "effort_levels"):
                        effs = _s.effort_levels(pid, mid)
                    if out_id in seen_ids:
                        continue
                    seen_ids.add(out_id)
                    models_out.append({
                        "id": out_id,
                        "name": out_name,
                        "efforts": effs,
                        "hint": m.get("hint") or "",
                        "tag": m.get("tag") or "",
                    })
                providers_list.append({
                    "id": pid,
                    "label": pid.replace("-", " ").title(),
                    "models": models_out,
                })
            self.send_json({"models": cat, "providers": providers_list, "ladder": list(getattr(_s, "LADDER", []))})
            return

        if path == "/api/tunnel/identity":
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing identity needs a full-access device")
                return
            import subway_home
            p = os.path.join(subway_home.subway_dir(), "tunnel", "station.json")
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    info = json.load(fh)
            except (OSError, ValueError):
                self.send_json({"error": "tunnel not set up"}, 404)
                return
            self.send_json({"sid": info.get("sid"), "x25519_pub": info.get("x25519_pub"), "url": info.get("url")})
            return

        if path == "/api/audit":
            if not self._gate(api=True):
                return
            import audit
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            limit = max(1, min(500, int((q.get("limit") or ["100"])[0] or 100)))
            self.send_json({"entries": audit.recent(limit, (q.get("worker_id") or [""])[0], (q.get("actor") or [""])[0])})
            return

        if path == "/api/audit/started":
            if not self._gate(api=True):
                return
            import audit
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ids = [i for i in ((q.get("ids") or [""])[0]).split(",") if i][:200]
            self.send_json({"started": audit.started_by(ids)})
            return

        if path == "/api/peers":
            if not self._gate(api=True):
                return
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers
            self.send_json({"peers": peers.list_peers(), "grant_schema": peers.grant_schema()})
            return

        if path == "/api/skills":
            if not self._gate(api=True):
                return
            import skills as _skills
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            want_all = (q.get("all") or ["0"])[0] in ("1", "true")
            rows = _skills.skills_rows(_skills.index_skills(all=want_all), (q.get("q") or [""])[0])
            self.send_json({"skills": rows, "count": len(rows)})
            return

        if path == "/api/usage/ledger":
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "usage ledger needs a full-access device")
                return
            import usage_ledger
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                since = float((q.get("since") or ["0"])[0] or 0)
            except ValueError:
                since = 0.0
            self.send_json(usage_ledger.summary(since_ts=since, session_id=(q.get("session") or [""])[0]))
            return
        if path in ("/api/screen/active", "/api/screen/session"):
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "screen sharing needs a full-access device")
                return
            import screen_share
            if path == "/api/screen/active":
                self.send_json({"shares": screen_share.active()})
                return
            res = screen_share.take(query.get("session_id", [""])[0], "sharer")
            st = res.pop("status", 400) if "error" in res else 200
            self.send_json(res, status=st)
            return

        if path == "/api/peers/train":
            import peers as _peers
            self.send_json(_peers.my_train())
            return

        if path == "/api/peer/hello":
            if not self._gate(api=True):
                return
            import socket
            import subway_home
            p = os.path.join(subway_home.subway_dir(), "tunnel", "station.json")
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    info = json.load(fh)
            except (OSError, ValueError):
                info = {}
            self.send_json({"ok": True, "station": socket.gethostname(), "sid": (info.get("sid") if isinstance(info, dict) else None)})
            return

        if path in ("/api/peer/chats", "/api/peer/read_chat", "/api/peer/search_chat"):
            if not self._gate(api=True):
                return
            import peers as _peers
            from cli_tools import chats as _chats
            q1 = lambda k, d="": (query.get(k, [d])[0] or d)
            if path == "/api/peer/chats":
                try:
                    lim = min(50, max(1, int(q1("limit", "15"))))
                except ValueError:
                    lim = 15
                cs = [c for c in _s.all_chats("all") if not threads.is_chat_excluded(c, base_dir=_s.SUBWAY_DIR)]
                needle = q1("query").lower()
                if needle:
                    cs = [c for c in cs if needle in " ".join(str(c.get(k) or "") for k in
                                                              ("title", "first_prompt", "last_prompt", "tab_name")).lower()]
                cs.sort(key=lambda c: (bool(c.get("open")), _s.sort_key_ts(c.get("updated"))), reverse=True)
                first_alias: Dict[str, str] = {}
                for alias, cid in _s.load_aliases().items():
                    first_alias.setdefault(str(cid), alias)
                self.send_json({"chats": [{"id": c.get("id"), "alias": first_alias.get(str(c.get("id")), ""),
                                           "title": c.get("title") or "", "source": c.get("source") or "",
                                           "updated": c.get("updated")} for c in cs[:lim]]})
                return
            if path == "/api/peer/search_chat":
                try:
                    lim = min(10, max(1, int(q1("limit", "5"))))
                except ValueError:
                    lim = 5
                self.send_json({"text": _chats.t_search_chat(quote=q1("quote"), limit=lim, peers_too=False)})
                return
            mode = q1("mode", "recent")
            if pairing._normalise_scope(getattr(self, "_scope", "full")) == pairing.SCOPE_PEER:
                me = _peers.by_device(getattr(self, "_device_id", "") or "")
                if me and _peers.effective_grants(me).get("chats") == "decisions":
                    mode = "decisions"
            try:
                turns = int(q1("turns", "6"))
            except ValueError:
                turns = 6
            try:
                text = _chats.t_read_chat(q1("chat_id"), mode=mode, turns=turns, since=q1("since"),
                                          anchor_text=q1("anchor_text"), local_only=True)
            except ValueError as e:
                self.send_json({"error": str(e)}, status=400)
                return
            self.send_json({"text": text})
            return

        if path == "/api/peer/worker":
            if not self._gate(api=True):
                return
            import peers as _peers
            wid = query.get("id", [""])[0]
            meta = _s._load_worker(wid) if re.fullmatch(r"worker_[0-9a-f]{6,}", wid or "") else None
            if not meta:
                self.send_json({"error": "no such worker"}, status=404)
                return
            if pairing._normalise_scope(getattr(self, "_scope", "full")) == pairing.SCOPE_PEER:
                me = _peers.by_device(getattr(self, "_device_id", "") or "")
                mine = me and (meta.get("origin") or {}).get("peer_id") == me.get("peer_id")
                if not me or (_peers.effective_grants(me).get("events") == "own" and not mine):
                    self.send_json({"error": "not shared with this station"}, status=403)
                    return
            try:
                meta = _s._update_worker_status(meta)
            except Exception:
                pass
            report = str(meta.get("report") or meta.get("final_report") or "")[:4000]
            self.send_json({k: meta.get(k) for k in ("id", "status", "role", "provider", "model", "spent", "created_at",
                                                     "started_at", "finished_at", "origin")} | {"report": report} | {"has_patch": bool(meta.get("remote_patch"))})
            return

        if path == "/api/peer/patch":
            if not self._gate(api=True):
                return
            import peers as _peers
            wid = query.get("id", [""])[0]
            meta = _s._load_worker(wid) if re.fullmatch(r"worker_[0-9a-f]{6,}", wid or "") else None
            me = _peers.by_device(getattr(self, "_device_id", "") or "")
            if not meta or not me or (meta.get("origin") or {}).get("peer_id") != me.get("peer_id"):
                self.send_json({"error": "no such worker"}, status=404)
                return
            pp = meta.get("patch_path") or ""
            if not meta.get("remote_patch") or not pp or not os.path.isfile(pp) or os.path.getsize(pp) > 4_000_000:
                self.send_json({"error": "no patch"}, status=404)
                return
            with open(pp, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            ident = _peers.repo_identity(meta.get("repo") or "")
            self.send_json({"patch": text, "root": ident[0] if ident else "", "role": meta.get("role") or ""})
            return

        if path in ("/api/peer/files", "/api/peer/file"):
            if not self._gate(api=True):
                return
            import peers as _peers
            me = _peers.by_device(getattr(self, "_device_id", "") or "")
            if not me:
                self._deny(403, "only linked stations use this route")
                return
            defaults = sorted({str(c.get("project") or "") for c in _s.all_chats("all") if c.get("project")})
            roots = _peers.roots_for(me, defaults)
            raw = query.get("path", [""])[0]
            if path == "/api/peer/files" and not raw:
                self.send_json({"roots": roots})
                return
            real = _peers.safe_path(raw, roots)
            if not real:
                self._deny(403, "outside the folders this station shares")
                return
            if path == "/api/peer/files":
                if not os.path.isdir(real):
                    self.send_json({"error": "not a folder"}, status=400)
                    return
                self.send_json({"path": real, "entries": _peers.list_dir(real)})
                return
            if not os.path.isfile(real) or os.path.getsize(real) > _peers.MAX_FILE:
                self.send_json({"error": "not a file, or too large"}, status=400)
                return
            try:
                off = max(0, int(query.get("offset", ["0"])[0]))
            except ValueError:
                off = 0
            if off == 0:
                try:
                    import audit as _audit
                    _audit.record({"peer_id": me.get("peer_id"), "name": me.get("name")}, "peer.file.read", {"path": real})
                except Exception:
                    pass
            self.send_json(_peers.read_chunk(real, off))
            return

        if path == "/api/peer/media":
            if not self._gate(api=True):
                return
            import peers as _peers
            wid = query.get("id", [""])[0]
            meta = _s._load_worker(wid) if re.fullmatch(r"worker_[0-9a-f]{6,}", wid or "") else None
            me = _peers.by_device(getattr(self, "_device_id", "") or "")
            if not meta or not me or (meta.get("origin") or {}).get("peer_id") != me.get("peer_id"):
                self.send_json({"error": "no such worker"}, status=404)
                return
            self.send_json({"media": _peers.local_media(wid)})
            return

        if path == "/api/remote_workers/media":
            if not self._gate(api=True):
                return
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "needs a full-access device")
                return
            import peers as _peers
            ref = query.get("id", [""])[0]
            name = os.path.basename(query.get("name", [""])[0])
            if not re.fullmatch(r"worker_[0-9a-f]{6,}(@[A-Za-z0-9_.-]+)?", ref or ""):
                self.send_json({"error": "bad request"}, status=400)
                return
            if not name:
                from hud_server import media_watch
                self.send_json({"id": ref, "names": media_watch.list_media(ref)[:12]})
                return
            if not name.lower().endswith(_peers.MEDIA_EXT):
                self.send_json({"error": "bad request"}, status=400)
                return
            p = os.path.join(_peers.media_dir(ref), name)
            if not os.path.isfile(p):
                self.send_json({"error": "not found"}, status=404)
                return
            ctype = {"png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(name.rsplit(".", 1)[-1].lower(), "image/jpeg")
            with open(p, "rb") as fh:
                body = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/api/peer/swarm/manifest", "/api/peer/swarm/bundle"):
            if not self._gate(api=True):
                return
            import peers as _peers
            me = _peers.by_device(getattr(self, "_device_id", "") or "")
            if not me:
                self._deny(403, "only linked stations use this route")
                return
            known = sorted({str(c.get("project") or "") for c in _s.all_chats("all") if c.get("project")})
            proj = _peers.resolve_project(query.get("project", [""])[0], known, os.environ.get("SUBWAY_PROJECT_DIR", "") or os.getcwd())
            repo = _peers.swarm_repo(proj) if proj else ""
            if not repo:
                self._deny(400, "no matching project on this station")
                return
            if path.endswith("/manifest"):
                try:
                    req = max(0, int(query.get("max_tokens", ["0"])[0]))
                except ValueError:
                    req = 0
                status, body = _peers.swarm_manifest(me, repo, _s.load_locks() or {}, req)
                try:
                    import audit as _audit
                    _audit.record({"peer_id": me.get("peer_id"), "name": me.get("name")}, "peer.swarm.checkout", {"repo": repo})
                except Exception:
                    pass
                self.send_json(body, status=status)
                return
            real = _peers.swarm_bundle_file(query.get("head", [""])[0])
            if not real:
                self.send_json({"error": "no such bundle"}, status=404)
                return
            try:
                off = max(0, int(query.get("offset", ["0"])[0]))
            except ValueError:
                off = 0
            self.send_json(_peers.read_chunk(real, off))
            return

        if path == "/api/peer/claims":
            if not self._gate(api=True):
                return
            import peers as _peers
            root = query.get("root", [""])[0]
            rel = query.get("rel", [""])[0]
            if not re.fullmatch(r"[0-9a-f]{7,64}", root or "") or not rel or os.path.isabs(rel) or ".." in rel.split("/"):
                self.send_json({"error": "root and rel are required"}, status=400)
                return
            locks = (_s.load_locks() or {}).get("locks") or {}
            if query.get("train", ["1"])[0] == "0":
                # A coach asking its engine for its own view must not trigger a fan-out back to coaches.
                self.send_json({"claims": _peers.local_claims_for(root, rel, locks)})
                return
            self.send_json({"claims": _peers.claims_with_train(root, rel, locks)})
            return

        if path == "/api/peer/coordination":
            if not self._gate(api=True):
                return
            import peers as _peers
            track = query.get("track", ["main"])[0] or "main"
            station = "peer"
            try:
                me = _peers.by_device(getattr(self, "_device_id", "") or "")
                if me:
                    station = _peers.slug(me)
            except Exception:
                pass
            self.send_json(_peers.local_coordination_state(station=station, track=track))
            return

        if path == "/api/coordination":
            if not self._gate(api=True):
                return
            import peers as _peers
            track = query.get("track", ["main"])[0] or "main"
            peer_slug = query.get("peer", [""])[0] or query.get("station", [""])[0] or ""
            self.send_json(_peers.coordination_view(track=track, peer_slug=peer_slug))
            return
        if path == "/api/coordination/all":
            if not self._gate(api=True):
                return
            import peers as _peers
            track = query.get("track", ["main"])[0] or "main"
            self.send_json(_peers.coordination_view_all(track=track))
            return

        if path == "/api/peer/screen/session":
            if not self._gate(api=True):
                return
            import peers as _peers
            import screen_share
            me = _peers.by_device(getattr(self, "_device_id", "") or "")
            if not me or _peers.effective_grants(me).get("screen") != "live":
                self._deny(403, "this station has not allowed live screen viewing")
                return
            res = screen_share.take(query.get("session_id", [""])[0], "viewer", viewer_peer=str(me.get("peer_id") or ""))
            st = res.pop("status", 400) if "error" in res else 200
            self.send_json(res, status=st)
            return

        if path == "/api/peer/screen":
            if not self._gate(api=True):
                return
            import peers as _peers
            me = _peers.by_device(getattr(self, "_device_id", "") or "")
            if not me:
                self._deny(403, "only linked stations use this route")
                return
            if _peers.effective_grants(me).get("screen") not in ("snapshot", "live"):
                self._deny(403, "this station has not shared its screen")
                return
            import screen_share
            res = screen_share.snapshot(query.get("max_w", ["1600"])[0])
            try:
                import audit
                audit.record({"kind": "peer", "id": str(me.get("peer_id") or ""), "name": str(me.get("name") or "")},
                             "screen.snapshot", ok="error" not in res)
            except Exception:
                pass
            if "error" in res:
                self.send_json({"error": res["error"]}, status=res.get("status", 503))
                return
            self.send_json(res)
            return

        if path in ("/api/peer/skills", "/api/peer/skill"):
            if not self._gate(api=True):
                return
            import peers as _peers
            me = _peers.by_device(getattr(self, "_device_id", "") or "")
            if not me:
                self._deny(403, "only linked stations use this route")
                return
            if _peers.effective_grants(me).get("skills") != "read":
                self._deny(403, "this station has not granted skills")
                return
            import skills as _skills
            line = query.get("line", [""])[0]
            if path == "/api/peer/skills":
                q = query.get("query", [""])[0].lower()
                rows = _skills._all_entries() if line else _skills.index_skills()
                out = [{"name": e.get("name"), "description": str(e.get("description") or "")[:300],
                        "line": e.get("line"), "version": e.get("version"), "has_scripts": bool(e.get("scripts"))}
                       for e in rows
                       if (not line or e.get("line") == line)
                       and (not q or q in str(e.get("name") or "").lower() or q in str(e.get("description") or "").lower())]
                self.send_json({"skills": out[:300]})
                return
            ent = _skills.find_skill(query.get("name", [""])[0], line=line)
            if not ent:
                self.send_json({"error": "no such skill"}, status=404)
                return
            self.send_json({"name": ent.get("name"), "line": ent.get("line"), "version": ent.get("version"),
                            "body": _skills.read_skill_body(ent), "instructions_only": True})
            return

        if path == "/api/peer/allowance":
            if not self._gate(api=True):
                return
            import peers as _peers
            me = _peers.by_device(getattr(self, "_device_id", "") or "")
            if not me:
                self._deny(403, "only linked stations use this route")
                return
            self.send_json(_peers.allowance_status(me["peer_id"]))
            return

        if path == "/api/peers/allowance":
            if not self._gate(api=True):
                return
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers as _peers
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            station = (q.get("station") or [""])[0]
            if station:
                self.send_json(_peers.remote_allowance(station))
                return
            pid = (q.get("peer_id") or [""])[0]
            if not _peers.get(pid):
                self._deny(404, "unknown peer")
                return
            self.send_json({"peer_id": pid, **_peers.allowance_status(pid)})
            return

        if path == "/api/peers/allowance/history":
            if not self._gate(api=True):
                return
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers as _peers
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            pid = (q.get("peer_id") or [""])[0]
            if not _peers.get(pid):
                self._deny(404, "unknown peer")
                return
            self.send_json({"peer_id": pid,
                            "history": _peers.allowance_history_snapshot(pid, (q.get("days") or ["14"])[0])})
            return

        if path == "/api/peer/models":
            if not self._gate(api=True):
                return
            import peers as _peers
            self.send_json({"models": _peers.share_models(_s.model_catalog())})
            return

        if path == "/api/peer/station":
            if not self._gate(api=True):
                return
            import peers as _peers
            me = _peers.by_device(getattr(self, "_device_id", ""))
            # Existing grant: station card is a presence/metadata view, allowed to peers with events visibility.
            if not me or _peers.effective_grants(me).get("events", "off") == "off":
                self._deny(403, "events grant required")
                return
            import station
            self.send_json({"station": station.get_station_card()})
            return

        if path == "/api/peers/models":
            if not self._gate(api=True):
                return
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers as _peers
            st = query.get("station", [""])[0]
            names = [st] if st else [_peers.slug(p) for p in _peers.list_peers()]
            self.send_json({"stations": [_peers.remote_models(n) for n in names]})
            return

        if path in ("/api/peers/chats", "/api/peers/read_chat"):
            if not self._gate(api=True):
                return
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers as _peers
            if path == "/api/peers/chats":
                try:
                    lim = min(50, max(1, int(query.get("limit", ["20"])[0])))
                except ValueError:
                    lim = 20
                groups: Dict[str, Dict[str, Any]] = {}
                for r in _peers.remote_chats("", lim):
                    g = groups.setdefault(r["peer_id"], {"station": r["station"], "peer_id": r["peer_id"],
                                                         "offline": False, "denied": False, "chats": []})
                    if r.get("offline"):
                        g["offline"] = not r.get("denied")
                        g["denied"] = bool(r.get("denied"))
                    else:
                        g["chats"].append({k: r.get(k) for k in ("id", "alias", "title", "source", "updated")})
                self.send_json({"stations": list(groups.values())})
                return
            from cli_tools import chats as _chats
            cid = query.get("id", [""])[0]
            mode = query.get("mode", ["decisions"])[0]
            if mode not in ("recent", "outline", "full", "decisions"):
                mode = "decisions"
            if not _peers.split_remote(cid):
                self.send_json({"error": "not a chat on a linked station"}, status=400)
                return
            self.send_json({"text": _chats.t_read_chat(cid, mode=mode, turns=8)})
            return

        if path == "/api/metrics":
            if not self._gate(api=True):
                return
            rng = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("range", ["30d"])[0] or "30d"
            from hud_server import metrics as _metrics
            self.send_json(_metrics.summary(rng))
            return

        if path == "/api/token_savings":
            if not self._gate(api=True):
                return
            import token_savings as _ts
            try:
                days = int((urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("days") or ["1"])[0])
            except ValueError:
                days = 1
            self.send_json({"today": _ts.summary(1), "range": _ts.summary(max(1, min(90, days)))})
            return

        if path == "/api/remote_workers":
            if not self._gate(api=True):
                return
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers as _peers
            want_archive = (urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("archived") or [""])[0] in ("1", "true")
            workers = _peers.remote_workers(archived=True) if want_archive else _peers.queue_remote_workers()
            self.send_json({"workers": workers, "archived": want_archive})
            return

        if path == "/api/station":
            import station
            card = station.get_station_card()
            self.send_json(card)
            return

        if path == "/api/station/telemetry":
            import station
            self.send_json({"telemetry": station.get_telemetry()})
            return

        if path == "/api/worker_log/stream":
            if not self._gate(api=True):
                return
            self._stream_worker_log(query)
            return

        if path == "/api/worker_log":
            wid = query.get("id", [""])[0]
            if not _s.WORKER_ID_RE.match(wid):
                self._deny(400, "invalid worker id")
                return
            meta = _s._load_worker(wid) or {}
            if not meta:
                arch_json = os.path.join(_s._archive_dir(), f"{wid}.json")
                if os.path.exists(arch_json):
                    with contextlib.suppress(Exception):
                        with open(arch_json, "r", encoding="utf-8") as af:
                            meta = json.load(af) or {}
            log_path = meta.get("log_file") or _s._log_file(wid)
            archived = os.path.join(_s._archive_dir(), f"{wid}.log")
            if not os.path.exists(log_path) and os.path.exists(archived):
                log_path = archived
            if os.path.exists(log_path):
                self.send_json({"id": wid, "log": _s._log_tail(log_path, 200), "status": meta.get("status"),
                                "exit_code": meta.get("exit_code")})
                return
            for gz_path in (f"{log_path}.gz", os.path.join(_s._archive_dir(), f"{wid}.log.gz")):
                if os.path.exists(gz_path):
                    import gzip
                    tail_str = ""
                    with contextlib.suppress(Exception):
                        with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as gf:
                            tail_str = "".join(gf.readlines()[-200:]).strip()
                    self.send_json({"id": wid, "log": tail_str, "status": meta.get("status"),
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

        if path == "/api/diagnostics/stream":
            self._stream_diagnostics()
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
            view = (query.get("view", [""])[0] or "").strip().lower()
            max_limit = 5000 if view == "next" else 200
            try:
                offset = max(0, int(query.get("offset", [0])[0]))
            except (ValueError, TypeError):
                offset = 0
            try:
                limit = min(max_limit, max(1, int(query.get("limit", [100])[0])))
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
            page = history[offset:offset + limit]
            # Reconcile branch state against git (read-only; page items are dict copies).
            for w in page:
                if w.get("branch_name") and not w.get("merged") and not w.get("branch_discarded"):
                    repo = str(w.get("directory") or w.get("cwd") or "") or _default_git_repo()
                    existing, merged = _branch_refs(repo)
                    if existing is None:
                        continue
                    if w["branch_name"] not in existing:
                        w["branch_gone"] = True
                    elif w["branch_name"] in merged:
                        w["merged"] = True
            if view == "next":
                from hud_server import next_view
                page = [next_view._wk(w) for w in page]
            self.send_json({"entries": page, "total": len(history)})
            return

        if path == "/api/mail":
            if not self._gate(api=True):
                return
            state = query.get("state", ["all"])[0]
            track = query.get("track", [""])[0]
            try:
                limit = int(query.get("limit", [100])[0])
            except (ValueError, TypeError):
                limit = 100
            self.send_json(postmail.http_get_mail(state=state, track=track, limit=limit))
            return

        if path == "/api/landing":
            if not self._gate(api=True):
                return
            import land_conflicts
            import landing_queue
            cfg = _s._load_json("config.json") or {}
            dirty = 0
            with contextlib.suppress(Exception):
                st = subprocess.run(["git", "-C", _default_git_repo(), "status", "--porcelain"], capture_output=True, timeout=3)
                if st.returncode == 0:
                    dirty = len([ln for ln in st.stdout.decode("utf-8", "replace").splitlines() if ln.strip()])
            self.send_json({"queue": landing_queue.list_queue(),
                            "conflicts": land_conflicts.list_conflicts(_s.SUBWAY_DIR),
                            "land_mode": str(cfg.get("land_mode") or "merge"),
                            "working_changes": dirty})
            return

        if path == "/api/landing/diff":
            if not self._gate(api=True):
                return
            code, body = _landing_diff(query)
            self.send_json(body, code)
            return

        if path == "/api/landing/diff/stream":
            if not self._gate(api=True):
                return
            self._stream_diff(query)
            return

        if path == "/api/fastmail":
            if not self._gate(api=True):
                return
            scope = query.get("scope", ["outbox"])[0]
            state = query.get("state", [""])[0]
            try:
                limit = int(query.get("limit", [50])[0])
            except (ValueError, TypeError):
                limit = 50
            self.send_json(fastmail.http_get(scope=scope, state=state, limit=limit))
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
        pth = urllib.parse.urlparse(self.path).path
        if pth == "/api/composer/upload":
            limit = attachments.UPLOAD_MAX_BODY
        elif pth == "/api/peer/file":
            limit = max(getattr(_s, "MAX_BODY_BYTES", 0) or 0, 512 * 1024)
        else:
            limit = _s.MAX_BODY_BYTES
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
        self._audit_payload = payload
        return payload

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/mcp/proxy/") and self._gate(api=False):
            import mcp_proxy
            mcp_proxy.handle(self, "DELETE")
            return
        self.send_error(404)

    def do_POST(self):
        self._idem = None
        self._audited = False
        self._audit_payload = None
        try:
            self._do_POST_routes()
        finally:
            idem = getattr(self, "_idem", None)
            if idem:
                # No answer was recorded (the route raised): free the key so the client may retry.
                self._idem = None
                from hud_server import idempotency
                idempotency.abandon(*idem)

    def _idem_guard(self, path: str) -> bool:
        """True when this request was answered here: a duplicate still running, or one already done."""
        from hud_server import idempotency
        key = (self.headers.get("X-Idempotency-Key") or "").strip()
        if not key or path not in idempotency.PATHS or len(key) > idempotency.MAX_KEY_LEN:
            return False
        owner = getattr(self, "_device_id", "") or "master"
        state, stored = idempotency.begin(owner, key)
        if state == "inflight":
            self.send_json({"error": "Request already in progress", "retry_after": 1}, status=409)
            return True
        if state == "done":
            self._send_raw_json(stored[0], stored[1], {"Idempotent-Replayed": "true"})
            return True
        self._idem = (owner, key)
        return False

    def _do_POST_routes(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/pair/request":
            if not self._gate(api=False):
                return
            payload = self._read_payload()
            if payload is None:
                return
            if payload.get("kind") == "station":
                qr = payload.get("qr_code")
                st = payload.get("station") if isinstance(payload.get("station"), dict) else {}
                their_token = str(payload.get("peer_token") or "")
                if not qr or not pairing.consume_qr_code(str(qr)):
                    self.send_json({"error": "pairing code invalid or used"}, status=403)
                    return
                if not their_token or not st.get("sid") or not st.get("pk"):
                    self.send_json({"error": "station identity and peer_token are required"}, status=400)
                    return
                res = pairing.create_request(device=str(st.get("name") or "Station"), platform="station",
                                             ip=str(self.client_address[0]) if self.client_address else "",
                                             note="station link", notify=False)
                if "error" in res:
                    self.send_json(res, status=429)
                    return
                ap = pairing.approve(res["request_id"], scope=pairing.SCOPE_PEER)
                import peers
                peers.add(str(st.get("name") or "Station"), st, their_token, ap.get("device_id", ""), "personal")
                self.send_json(res)
                return
            qr = payload.get("qr_code")
            if qr and not pairing.consume_qr_code(str(qr)):
                self.send_json({"error": "pairing code invalid or used"}, status=403)
                return
            res = pairing.create_request(
                device=str(payload.get("device") or "Unknown device"),
                platform=str(payload.get("platform") or "") or pairing.platform_from_ua(self.headers.get("User-Agent", "")),
                ip=str(self.client_address[0]) if self.client_address else "",
                note=str(payload.get("note") or ""),
                notify=not qr,
            )
            if qr and "error" not in res:
                pairing.approve(res["request_id"], scope="device")
            self.send_json(res, status=429 if "error" in res else 200)
            return
        if path == "/api/pair/qr":
            if not self._gate(api=True):
                return
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import subway_home
            p = os.path.join(subway_home.subway_dir(), "tunnel", "station.json")
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    info = json.load(fh)
            except (OSError, ValueError):
                info = None
            if not isinstance(info, dict) or not info.get("url"):
                self.send_json({"error": "tunnel not set up"}, 404)
                return
            minted = pairing.mint_qr_code()
            link = "subway://pair?" + urllib.parse.urlencode({
                "sid": info.get("sid") or "", "pk": info.get("x25519_pub") or "",
                "relay": info.get("url"), "code": minted["code"]})
            self.send_json({"link": link, "expires_at": minted["expires_at"]})
            return
        if path.startswith("/api/mcp/proxy/"):
            # Workers authenticate with a capability token, not the HUD token (50c-5).
            if self._gate(api=False):
                import mcp_proxy
                mcp_proxy.handle(self, "POST")
            return
        if not self._gate(api=True):
            return
        if self._idem_guard(path):
            return

        if path == "/api/mcp/connect":
            payload = self._read_payload()
            if payload is None:
                return
            import mcp_connect_api
            status, body = mcp_connect_api.handle_post(payload)
            self.send_json(body, status=status)
            return
        if path.startswith("/api/mcp/hub/"):
            payload = self._read_payload()
            if payload is None:
                return
            import hub_tools
            if path == "/api/settings":
                status, body = None, None
            else:
                status, body = hub_tools.handle_post(path, payload)
            if status is not None:
                self.send_json(body, status=status)
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

        if path == "/api/collab/people" or path.startswith("/api/collab/people/"):
            from hud_server import collab_people
            actor = str(getattr(self, "_device_id", "") or "master")
            master = not bool(getattr(self, "_device_id", ""))
            device_name = "Station administrator" if master else "Paired device"
            if not master:
                try:
                    device_name = next((str(d.get("name") or device_name) for d in pairing.list_devices()
                                        if d.get("device_id") == actor), device_name)
                except Exception:
                    pass
            try:
                status, body = collab_people.handle_post(
                    self.path, payload, actor, master=master, actor_name=device_name,
                )
                if status is not None:
                    self.send_json(body, status=status)
            except Exception as exc:
                self._deny(500, str(exc))
            return

        if path.startswith("/api/collab/"):
            from hud_server import collab_api
            res = collab_api.handle_post(path, payload)
            if res is not None:
                self.send_json(res[1], status=res[0])
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

        if path == "/api/coordination/handoff":
            import peers as _peers
            files = payload.get("files") or []
            if isinstance(files, str):
                files = [f for f in files.split(",") if f.strip()]
            res = _peers.record_handoff(to_station=str(payload.get("to") or payload.get("peer") or ""),
                                        subject=str(payload.get("subject") or "handoff"),
                                        text=str(payload.get("text") or ""), files=list(files),
                                        track=str(payload.get("track") or "main"))
            self.send_json(res, status=200 if res.get("ok") else 400)
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
            revoke_id = str(payload.get("device_id") or "")
            res = pairing.revoke(revoke_id)
            if "error" not in res and revoke_id:
                try:
                    import push
                    push.unregister(revoke_id)
                except Exception:
                    pass
            self.send_json(res, status=400 if "error" in res else 200)
            return

        if path == "/api/push/register":
            device_id = getattr(self, "_device_id", "")
            if not device_id:
                self.send_json({"error": "only paired devices can register for pushes"}, status=400)
                return
            import push
            if push.register(device_id, str(payload.get("token") or ""), str(payload.get("platform") or "")):
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "invalid push token"}, status=400)
            return

        if path == "/api/push/unregister":
            device_id = getattr(self, "_device_id", "")
            if device_id:
                import push
                push.unregister(device_id)
            self.send_json({"ok": True})
            return

        if path == "/api/pair/rename":
            res = pairing.rename(str(payload.get("device_id") or ""), str(payload.get("name") or ""))
            self.send_json(res, status=400 if "error" in res else 200)
            return

        if path == "/api/peer/collab/couple":
            import peers as _peers
            from hud_server import collab_api
            p = _peers.by_device(getattr(self, "_device_id", "") or "")
            if not p:
                self._deny(403, "linked station required")
                return
            result = collab_api.couple_from_peer(p["peer_id"], payload or {})
            self.send_json(result[1], status=result[0])
            return

        if path == "/api/peer/collab/plan":
            from hud_server import collab_api
            import peers as _peers
            p = _peers.by_device(getattr(self, "_device_id", "") or "")
            if not p:
                self._deny(403, "linked station required")
                return
            result = collab_api.accept_plan(p["peer_id"], payload or {})
            self.send_json(result[1], status=result[0])
            return

        if path in ("/api/peer/screen/request", "/api/peer/screen/signal"):
            import peers as _peers
            import screen_share
            me = _peers.by_device(getattr(self, "_device_id", "") or "")
            if not me or _peers.effective_grants(me).get("screen") != "live":
                self._deny(403, "this station has not allowed live screen viewing")
                return
            vid = str(me.get("peer_id") or "")
            if path == "/api/peer/screen/request":
                res = screen_share.request(vid, str(me.get("name") or ""))
            else:
                res = screen_share.signal(payload.get("session_id"), "viewer", str(payload.get("kind") or ""),
                                          payload.get("payload"), viewer_peer=vid)
            st = res.pop("status", 400) if "error" in res else 200
            self.send_json(res, status=st)
            return

        if path == "/api/peer/hub_call":
            import peers as _peers
            me = _peers.by_device(getattr(self, "_device_id", "") or "")
            if not me or _peers.effective_grants(me).get("mcp") != "on":
                self._deny(403, "this station has not lent its MCP tools")
                return
            import hub_tools
            server, tool = str(payload.get("server") or ""), str(payload.get("tool") or "")
            if not server or not tool:
                self.send_json({"error": "server and tool are required"}, status=400)
                return
            args = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
            res = hub_tools.t_hub_call(server, tool, args)
            self.send_json(res if isinstance(res, dict) else {"result": res})
            return

        if path == "/api/peer/collab/sync":
            from hud_server import collab_api
            import peers as _peers
            _sender = _peers.by_device(getattr(self, "_device_id", "") or "") or {}
            result = collab_api.apply_peer_sync(payload or {}, from_peer=str(_sender.get("peer_id") or ""))
            self.send_json(result[1], status=result[0])
            return
        if path == "/api/peer/collab/handover":
            import peers as _peers
            from hud_server import collab_api
            p = _peers.by_device(getattr(self, "_device_id", "") or "")
            if not p:
                self._deny(403, "linked station required")
                return
            result = collab_api.handover(str(payload.get("to") or ""), from_peer=p["peer_id"])
            self.send_json(result[1], status=result[0])
            return

        if path == "/api/peer/prepare":
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers
            mine = peers.my_station()
            if not mine:
                self.send_json({"error": "tunnel not set up"}, 404)
                return
            m = pairing.mint_device(str(payload.get("name") or "Station"), "station", pairing.SCOPE_PEER)
            self.send_json({
                "device_id": m["device_id"],
                "token": m["token"],
                "station": mine,
            })
            return

        if path == "/api/peer/link":
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers
            res = peers.link_add(str(payload.get("link") or ""), payload.get("preset") or "personal")
            self.send_json(res, status=400 if "error" in res else 200)
            return

        if path == "/api/peer/complete":
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers
            peer = payload.get("peer") if isinstance(payload.get("peer"), dict) else {}
            res = peers.add(str(peer.get("name") or "Station"), peer, str(payload.get("token") or ""),
                            str(payload.get("device_id") or ""), payload.get("grants") or "personal")
            self.send_json(res)
            return

        if path in ("/api/screen/control", "/api/screen/input"):
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "screen control needs a full-access device")
                return
            import screen_share
            if path == "/api/screen/control":
                res = screen_share.allow_control(payload.get("session_id"), bool(payload.get("on")))
                self.send_json(res, status=res.pop("status", 400) if "error" in res else 200)
                return
            sid = payload.get("session_id")
            ok, why, vp = screen_share.input_allowed(sid)
            if not ok:
                self.send_json({"error": why}, status=403)
                return
            import peers
            import desktop_mcp
            import audit
            rec = next((p for p in peers.list_peers() if p.get("peer_id") == vp), None)
            if not rec or peers.effective_grants(rec).get("control") != "on":
                self.send_json({"error": "this station has not granted control"}, status=403)
                return
            kind = str(payload.get("type") or "")
            if kind == "click":
                desktop_mcp.t_click({"x": int(payload.get("x")), "y": int(payload.get("y"))})
            elif kind == "type":
                desktop_mcp.t_type({"text": str(payload.get("text") or "")[:500]})
            elif kind == "key":
                desktop_mcp.t_key({"key": str(payload.get("key") or "")[:40]})
            else:
                self.send_json({"error": "unknown input type"}, status=400)
                return
            audit.record({"peer": "viewer:" + vp}, "screen.input", target={"type": kind})
            self.send_json({"ok": True})
            return

        if path in ("/api/screen/answer", "/api/screen/signal", "/api/screen/remote"):
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "screen sharing needs a full-access device")
                return
            import screen_share
            if path == "/api/screen/answer":
                res = screen_share.answer(payload.get("session_id"), str(payload.get("action") or ""))
            elif path == "/api/screen/signal":
                res = screen_share.signal(payload.get("session_id"), "sharer", str(payload.get("kind") or ""), payload.get("payload"))
            else:
                import peers as _peers
                from urllib.parse import quote as _q
                pid, op = str(payload.get("peer_id") or ""), str(payload.get("op") or "")
                sid = str(payload.get("session_id") or "")
                if op == "snapshot":
                    r = _peers.call(pid, "GET", "/api/peer/screen?max_w=" + _q(str(payload.get("max_w") or 1600)), timeout=40)
                elif op == "request":
                    r = _peers.call(pid, "POST", "/api/peer/screen/request", {})
                elif op == "signal":
                    r = _peers.call(pid, "POST", "/api/peer/screen/signal",
                                    {"session_id": sid, "kind": str(payload.get("kind") or ""), "payload": payload.get("payload")})
                elif op == "poll":
                    r = _peers.call(pid, "GET", "/api/peer/screen/session?session_id=" + _q(sid))
                else:
                    self.send_json({"error": "unknown op"}, status=400)
                    return
                if "error" in r:
                    self.send_json({"error": r["error"]}, status=502)
                    return
                data = r.get("data") if isinstance(r.get("data"), dict) else {"error": "bad reply"}
                self.send_json(data, status=int(r.get("status") or 200))
                return
            st = res.pop("status", 400) if "error" in res else 200
            self.send_json(res, status=st)
            return

        if path == "/api/peers/grants":
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers
            res = peers.set_grants(str(payload.get("peer_id") or ""), payload.get("grants"))
            self.send_json(res, status=400 if "error" in res else 200)
            return

        if path == "/api/peers/allowance":
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers
            res = peers.set_allowance(str(payload.get("peer_id") or ""), payload.get("tokens_per_day"))
            self.send_json(res, status=400 if "error" in res else 200)
            return

        if path == "/api/peers/relation":
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers
            res = peers.set_relation(str(payload.get("peer_id") or ""), payload.get("relation"),
                                     engine=bool(payload.get("engine")))
            self.send_json(res, status=400 if "error" in res else 200)
            return

        if path == "/api/peers/train":
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers
            if "train_role" in payload:
                res = peers.set_train_role(payload.get("train_role"))
                if "error" in res:
                    self.send_json(res, status=400)
                    return
            if "shared_coaches" in payload:
                res = peers.set_shared_coaches(payload.get("shared_coaches"))
                self.send_json(res, status=400 if "error" in res else 200)
                return
            self.send_json(peers.my_train())
            return

        if path == "/api/peers/controller":
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers
            res = peers.set_controller(payload.get("peer_id") or None)
            self.send_json(res, status=400 if "error" in res else 200)
            return

        if path == "/api/peer/unlink":
            import peers
            if pairing._normalise_scope(getattr(self, "_scope", "full")) == pairing.SCOPE_PEER:
                p = peers.by_device(getattr(self, "_device_id", "") or "")
                if not p:
                    self.send_json({"error": "unknown peer"}, status=400)
                    return
                res = peers.remove(p.get("peer_id"))
            else:
                if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                    self._deny(403, "pairing needs a full-access device")
                    return
                res = peers.unlink(str(payload.get("peer_id") or ""))
            self.send_json(res, status=400 if "error" in res else 200)
            return

        if path == "/api/outbox/sync":
            import vfs
            try:
                result = vfs.sync(
                    payload.get("items"),
                    project_dir=str(payload.get("project_dir") or payload.get("project") or ""),
                    subway_dir=str(_s.SUBWAY_DIR),
                    device=str(getattr(self, "_device_id", "")),
                    idempotency_key=str(self.headers.get("X-Idempotency-Key") or payload.get("idempotency_key") or ""),
                )
                _s.mark_hud_cache_stale()
                self.send_json(result)
            except ValueError as e:
                self._deny(400, str(e))
            except Exception as e:
                self._deny(500, str(e))
            return

        if path in ("/api/relay/rpc", "/api/relay/pings"):
            # Thin MCP relay: a chat's ~15 MB stdio relay forwards here, and we answer as that chat.
            if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                self._deny(403, "relay is loopback-only")
                return
            from chat_readers import dispatch as _disp
            caller = payload.get("caller") if isinstance(payload.get("caller"), dict) else {}
            allowed = ("SUBWAY_STATION_ID", "SUBWAY_WORKER_ID", "PI_CODING_AGENT", "PI_CODING_AGENT_DIR",
                       "ANTIGRAVITY_CONVERSATION_ID", "SUBWAY_NO_NOTIFY")
            raw_env = caller.get("env") if isinstance(caller.get("env"), dict) else {}
            env = {k: str(raw_env[k]) for k in allowed if raw_env.get(k) is not None}
            try:
                ppid = int(caller.get("ppid") or 0)
            except (TypeError, ValueError):
                ppid = 0
            tok = _disp.CALLER.set({"ppid": ppid, "env": env})
            try:
                if path == "/api/relay/pings":
                    cid = _s._self_id()
                    lines = _s.pending_worker_pings(cid, consume=False) if cid else []
                    self.send_json({"cid": cid, "lines": lines})
                    return
                req = payload.get("request")
                if not isinstance(req, dict):
                    self._deny(400, "request must be a JSON-RPC object")
                    return
                if req.get("method") == "initialize" and isinstance(req.get("params"), dict):
                    # The HUD can't reach the chat's UI, so never take on elicitation for it.
                    caps = dict(req["params"].get("capabilities") or {})
                    caps.pop("elicitation", None)
                    req = {**req, "params": {**req["params"], "capabilities": caps}}
                try:
                    resp = _s.handle(req)
                except Exception as e:
                    resp = {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32603, "message": str(e)}}
                self.send_json({"response": resp})
            finally:
                _disp.CALLER.reset(tok)
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
            claim_peer = ""
            try:
                import peers as _peers
                _p = _peers.by_device(getattr(self, "_device_id", "") or "")
                claim_peer = str((_p or {}).get("peer_id") or "")
            except Exception:
                claim_peer = ""
            claim_holder = str(payload.get("holder", ""))
            if claim_peer:
                claim_holder = "@" + _peers.slug(_p)  # a linked station never claims as a local chat
            res = _s.t_claim_file(filepath=str(payload.get("filepath", "")), holder=claim_holder,
                               duration_seconds=payload.get("duration", 300), purpose=str(payload.get("purpose", "")),
                               role=str(payload.get("role", "implementer")), peer_id=claim_peer)
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
            rel_peer = None
            try:
                import peers as _peers
                rel_peer = _peers.by_device(getattr(self, "_device_id", "") or "")
            except Exception:
                rel_peer = None
            if rel_peer:
                # A linked station may release only its own claims.
                res = _s.t_release_file(filepath=str(payload.get("filepath", "")),
                                        holder="@" + _peers.slug(rel_peer), force=False)
            else:
                res = _s.t_release_file(filepath=str(payload.get("filepath", "")), holder="hud-user", force=True)
        elif path == "/api/broadcast":
            res = _s._broadcast_track_internal(
                message=str(payload.get("message", "")), track=str(payload.get("track", "main")),
                files=[str(f) for f in payload.get("files") or []],
                decisions=[str(d) for d in payload.get("decisions") or []],
                station_id="user (HUD)")
        elif path == "/api/peer/swarm/submit":
            import peers as _peers
            me = _peers.by_device(getattr(self, "_device_id", "") or "") if pairing._normalise_scope(getattr(self, "_scope", "full")) == pairing.SCOPE_PEER else None
            if not me:
                self._deny(403, "only linked stations use this route")
                return
            known = sorted({str(c.get("project") or "") for c in _s.all_chats("all") if c.get("project")})
            proj = _peers.resolve_project(str(payload.get("project") or ""), known, os.environ.get("SUBWAY_PROJECT_DIR", "") or os.getcwd())
            repo = _peers.swarm_repo(proj) if proj else ""
            if not repo:
                self._deny(400, "no matching project on this station")
                return
            status, body = _peers.swarm_submit(me, payload, repo, _s.load_locks() or {})
            if status != 200:
                self._deny(status, str(body.get("error") or "refused"))
                return
            self.send_json(body)
            return
        elif path in ("/api/peer/spawn", "/api/peer/approve_worker"):
            import peers as _peers
            me = _peers.by_device(getattr(self, "_device_id", "") or "") if pairing._normalise_scope(getattr(self, "_scope", "full")) == pairing.SCOPE_PEER else None
            if not me:
                self._deny(403, "only linked stations use this route")
                return
            if path == "/api/peer/approve_worker":
                from workers.spawn import t_approve_worker
                res = t_approve_worker(str(payload.get("id", "")), str(payload.get("approval_code", "")))
                if isinstance(res, str) and ("REFUSED" in res or res.startswith("Error")):
                    self._deny(403 if "REFUSED" in res else 400, res.splitlines()[0])
                    return
                self.send_json({"ok": True, "result": str(res)[:300]})
                return
            known = sorted({str(c.get("project") or "") for c in _s.all_chats("all") if c.get("project")})
            proj = _peers.resolve_project(str(payload.get("project") or ""), known, os.environ.get("SUBWAY_PROJECT_DIR", "") or os.getcwd())
            if not proj:
                self._deny(400, "no matching project on this station")
                return
            def _int(k):
                try:
                    return max(0, int(payload.get(k) or 0))
                except (TypeError, ValueError):
                    return 0
            caps_in = payload.get("capabilities") if isinstance(payload.get("capabilities"), list) else []
            caps, why = _peers.check_caps(me, caps_in)
            if why:
                self._deny(403, why)
                return
            code = payload.get("code")
            allowed_tokens, why = _peers.allowance_limit(me["peer_id"], _int("max_tokens"))
            if why:
                self._deny(403, why)
                return
            # Collab dispatch modes (roadmap 38 point 4): Mode A queues for this station's local
            # initiator; Mode B launches under the link's grants. Plain links (no session) are unchanged.
            from hud_server import collab_api as _collab
            try:
                _dm = _collab.dispatch_mode(str(me.get("device_id") or ""), str(me.get("peer_id") or ""),
                                            str(payload.get("collab_device") or "")[:64])
            except Exception:
                _dm = {"mode": "", "review": False, "session_id": "", "local_initiator_chat": "", "paid_by": ""}
            billing_mode = _dm.get("mode") or ""
            res = _s.t_spin_worker(
                role=str(payload.get("role") or "Remote worker")[:80], prompt=str(payload.get("prompt") or ""),
                provider=str(payload.get("provider") or "auto"), model=str(payload.get("model") or ""),
                effort=str(payload.get("effort") or "auto"), mode="standard",
                track=str(payload.get("track") or "main"), project_dir=proj,
                timeout_seconds=_int("timeout_seconds") or 3600, max_tokens=allowed_tokens,
                max_seconds=_int("max_seconds"), max_tool_calls=_int("max_tool_calls"),
                isolate=bool(payload.get("isolate")) or _peers.effective_grants(me).get("land") != "edits",
                code=[str(c) for c in code] if isinstance(code, list) else None,
                capabilities=caps or None,
                route_reason=f"Started from linked station {_peers.slug(me)}", from_link=True)
            m = re.search(r"worker_[0-9a-f]{6,}", str(res))
            wid = m.group(0) if m else ""
            if not wid or str(res).startswith(("Error", "Refused")):
                self._deny(400, str(res).splitlines()[0][:300])
                return
            meta = _s._load_worker(wid) or {}
            if meta:
                meta["origin"] = {"station": _peers.slug(me), "peer_id": me["peer_id"],
                                  "chat": str(payload.get("origin_chat") or "")[:120],
                                  "billing_mode": billing_mode or "link",
                                  "requested_by": _peers.slug(me),
                                  "session_id": _dm.get("session_id") or "",
                                  "paid_by": _dm.get("paid_by") or "this station"}
                if _dm.get("review"):
                    meta["awaiting_local_initiator"] = True
                    meta["local_initiator_chat"] = _dm.get("local_initiator_chat") or "principal"
                if _peers.effective_grants(me).get("land") != "edits":
                    meta["hold_for_peer"] = True
                _s._save_worker(meta)
            status = meta.get("status") or "pending_approval"
            try:
                from hud_server import settings_api as _settings
                remote_ask = _settings.value("launch_approval_remote") == "ask"
            except Exception:
                remote_ask = False
            if _dm.get("review") and status == "pending_approval":
                try:  # tell the local initiator there is a Mode A request to plan and approve
                    _note = getattr(_s, "t_send_note", None)
                    if _note:
                        _note(to=_dm.get("local_initiator_chat") or "principal",
                              text=f"[Mode A] {_peers.slug(me)} asks for a worker: {str(payload.get('role') or 'Remote worker')[:80]} "
                                   f"({wid}). Approve or reject it in Remote workers.")
                except Exception:
                    pass
            if not _dm.get("review") and not remote_ask and _peers.effective_grants(me).get("spawn") == "direct" and status == "pending_approval":
                _s._thread_record(_s._thread_root(meta or {"id": wid}), "approved", wid, data={"by": f"link {_peers.slug(me)} (direct)"})
                _s._launch_worker(wid)
                status = "launching"
            self.send_json({"ok": True, "worker_id": wid, "status": status, "result": str(res)[:400]})
            return
        elif path in ("/api/remote_workers/approve", "/api/remote_workers/reject"):
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "pairing needs a full-access device")
                return
            import peers as _peers
            ref = str(payload.get("ref") or payload.get("id") or "")
            if path == "/api/remote_workers/approve":
                res = _peers.remote_approve(ref, by_user=True)
            else:
                res = _peers.remote_reject(ref, reason=str(payload.get("reason") or ""))
            if isinstance(res, str) and res.startswith("Error"):
                self._deny(400, res)
                return
            self.send_json({"ok": True, "result": res})
            return
        elif path == "/api/remote_workers/land":
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "landing needs a full-access device")
                return
            import peers as _peers
            cands = [os.environ.get("SUBWAY_PROJECT_DIR", ""), os.getcwd()] + sorted({str(c.get("project") or "") for c in _s.all_chats("all") if c.get("project")})
            out = _peers.land_remote(str(payload.get("id", "")), cands)
            if out.startswith("Error"):
                self._deny(400, out)
                return
            self.send_json({"ok": True, "result": out})
            return
        elif path == "/api/approve_worker":
            approve_id = str(payload.get("id", ""))
            import peers as _peers
            if _peers.split_worker(approve_id):
                if pairing._normalise_scope(getattr(self, "_scope", "full")) == pairing.SCOPE_PEER:
                    self._deny(403, "linked stations cannot forward approvals")
                    return
                out = _peers.remote_approve(approve_id, by_user=True)
                if not out.startswith("Approved"):
                    self._deny(400, out)
                    return
                self.send_json({"ok": True, "result": out})
                return
            if _s.WORKER_ID_RE.match(approve_id):
                _s._thread_record(_s._thread_root(_s._load_worker(approve_id) or {"id": approve_id}), "approved", approve_id,
                               data={"by": "hud"})
            res = _s._launch_worker(approve_id)  # The HUD token proves this is the user.
            if isinstance(res, str) and (res.startswith("Error") or res.startswith("Refused")):
                self._deny(400, res)
                return
        elif path == "/api/reject_worker":
            rid = str(payload.get("id", ""))
            reason = str(payload.get("reason", ""))
            import peers as _peers
            if _peers.split_worker(rid):
                if pairing._normalise_scope(getattr(self, "_scope", "full")) == pairing.SCOPE_PEER:
                    self._deny(403, "linked stations cannot forward approvals")
                    return
                self.send_json({"ok": True, "result": _peers.remote_reject(rid, reason)})
                return
            res = _s.t_reject_worker(worker_id=rid, reason=reason)
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
            # Managed schema keys are validated and written by settings_api (all-or-nothing);
            # whatever it does not own stays in the payload for the legacy per-key handlers below.
            try:
                from hud_server import settings_api
                st, body, rest = settings_api.handle_post(payload)
            except Exception:
                st, body, rest = 200, {}, payload
            if st != 200:
                self._deny(st, str((body or {}).get("error") or "Invalid settings"))
                return
            if isinstance(body, dict):
                res.update(body.get("applied") or {})
            payload = rest if isinstance(rest, dict) else {}
            # Defensive validation (schema-managed keys may remain in rest on fallback).
            for _nk in ("nudge_builder_calls", "nudge_other_calls", "nudge_default_calls", "no_edit_tool_calls"):
                if _nk in payload:
                    try:
                        _nv = int(payload[_nk])
                    except (TypeError, ValueError):
                        self._deny(400, f"{_nk} must be an integer between 2 and 50")
                        return
                    if not (2 <= _nv <= 50):
                        self._deny(400, f"{_nk} must be between 2 and 50")
                        return
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
            if "land_mode" in payload:
                lm = str(payload.get("land_mode") or "").strip().lower()
                if lm not in ("merge", "preserve", "queue", "manual"):
                    self._deny(400, f"Invalid land_mode: {lm}")
                    return
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["land_mode"] = lm
                    _s._save_json("config.json", cfg)
                res["land_mode"] = lm
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
            if "private_routing" in payload:
                val = payload["private_routing"]
                if val not in ("local_only", "auto", "off"):
                    self.send_json({"error": "private_routing must be local_only, auto, or off"}, status=400)
                    return
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["private_routing"] = val
                    _s._save_json("config.json", cfg)
                res["private_routing"] = val
            if "hub_concurrency_limits" in payload:
                val = payload["hub_concurrency_limits"]
                b_val = val if isinstance(val, bool) else str(val).lower() in ("true", "1", "on")
                with _s._state_lock("config"):
                    cfg = _s._load_json("config.json")
                    cfg["hub_concurrency_limits"] = b_val
                    _s._save_json("config.json", cfg)
                res["hub_concurrency_limits"] = b_val
            for _hk in ("hub_max_local", "hub_max_remote"):
                if _hk in payload:
                    val = payload[_hk]
                    try:
                        n_val = int(val) if val not in (None, "", "auto", 0, "0") else None
                    except (ValueError, TypeError):
                        n_val = None
                    with _s._state_lock("config"):
                        cfg = _s._load_json("config.json")
                        if n_val and n_val > 0:
                            cfg[_hk] = n_val
                        else:
                            cfg.pop(_hk, None)
                        _s._save_json("config.json", cfg)
                    res[_hk] = n_val if (n_val and n_val > 0) else "auto"
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
                "Subway",
                "Test alert: Notifications are working in your IDE and desktop!",
                config=cfg
            )
            self.send_json({"ok": True, "delivered": dispatch_res})
            return
        elif path == "/api/models":
            res = _s.set_model_rankings(payload.get("models"))
        elif path == "/api/answer_permission":
            res = _s._answer_permission(str(payload.get("id", "")), str(payload.get("decision", "")), "hud-user")
        elif path == "/api/landing/resolve":
            import land_resolver
            self.send_json({"result": land_resolver.t_resolve_conflict(str(payload.get("id", "")),
                                                                       provider=str(payload.get("provider") or "pi"))})
            return
        elif path == "/api/landing/drop":
            import land_conflicts
            ok = land_conflicts.resolve(_s.SUBWAY_DIR, str(payload.get("id", "")), "dropped", note="dropped in the HUD")
            self.send_json({"result": "Dropped. The branch is kept." if ok else "Error: unknown overlap"})
            return
        elif path in ("/api/landing/merge", "/api/landing/discard"):
            act = "merge" if path.endswith("/merge") else "discard"
            code, body = _settle_worker_branch(str(payload.get("worker_id") or payload.get("id") or ""), act)
            self.send_json(body, code)
            return
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
                max_budget_usd=payload.get("max_budget_usd"),
                station=str(payload.get("station") or "").lstrip("@"),
                thread=str(payload.get("thread") or ""),
                attachments=[str(a) for a in payload.get("attachments")] if isinstance(payload.get("attachments"), list) else None)
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
        elif path == "/api/wallpaper":
            import base64 as _b64
            name = str(payload.get("name") or "")
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext == "jpeg":
                ext = "jpg"
            if ext not in ("png", "jpg", "webp"):
                self._deny(400, "PNG, JPG or WebP only")
                return
            try:
                data = _b64.b64decode(payload.get("data") or "", validate=False)
            except Exception:
                self._deny(400, "bad image data")
                return
            if len(data) > 8 * 1024 * 1024:
                self._deny(413, "max 8 MB")
                return
            magic = {"png": data[:4] == b"\x89PNG", "jpg": data[:2] == b"\xff\xd8", "webp": data[:4] == b"RIFF" and data[8:12] == b"WEBP"}
            if not magic[ext]:
                self._deny(400, "not an image")
                return
            wdir = os.path.join(_s.SUBWAY_DIR, "wallpaper")
            os.makedirs(wdir, exist_ok=True)
            for old in os.listdir(wdir):
                if old.startswith("custom."):
                    os.remove(os.path.join(wdir, old))
            with open(os.path.join(wdir, "custom." + ext), "wb") as fh:
                fh.write(data)
            self.send_json({"ok": True, "url": "/wallpaper/custom.%s?v=%d" % (ext, int(time.time()))})
            return
        elif path == "/api/transplant/map":
            pid = str(payload.get("provider") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", pid):
                self._deny(400, "provider must be 1-64 chars of letters, digits, _ . -")
                return
            from transplant import learn as _tlearn
            res = _tlearn.prepare(pid)
            self.send_json({"ok": bool(res.get("ok")), "reason": res.get("reason", ""), "brief_path": res.get("brief_path", "")})
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
        elif path == "/api/station/telemetry":
            # A phone reports its own battery, thermal and network state; the station decides
            # what that permits (see local_models.edge_telemetry_ok).
            import station
            try:
                clean = station.set_telemetry(payload if isinstance(payload, dict) else {})
            except ValueError as e:
                self._deny(400, str(e))
                return
            self.send_json({"ok": True, "telemetry": clean})
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
            if "small_edit_lines" in payload:
                try:
                    n = int(payload.get("small_edit_lines"))
                except (TypeError, ValueError):
                    self._deny(400, "small_edit_lines must be an integer from 0 to 50")
                    return
                if n < 0 or n > 50:
                    self._deny(400, "small_edit_lines must be an integer from 0 to 50")
                    return
                updates["principal_small_edit_lines"] = n
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
                "small_edit_lines": max(0, int(cfg.get("principal_small_edit_lines", 0) or 0)),
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
        elif path == "/api/home_prefs":
            prefs = _s._load_json("home_prefs.json")
            if not isinstance(prefs, dict):
                prefs = {}
            if isinstance(payload.get("scopes"), dict):
                prefs["scopes"] = payload["scopes"]
            if isinstance(payload.get("closed"), dict):
                prefs["closed"] = payload["closed"]
            _s._save_json("home_prefs.json", prefs)
            self.send_json({"ok": True, "scopes": prefs.get("scopes", {}), "closed": prefs.get("closed", {})})
            return
        elif path == "/api/exclusions":
            threads.save_exclusions(_s.SUBWAY_DIR, payload)
            self.send_json({"ok": True, "exclusions": threads.load_exclusions(_s.SUBWAY_DIR)})
            return
        elif path == "/api/peer/file":
            import peers as _peers
            me = _peers.by_device(getattr(self, "_device_id", "") or "") if pairing._normalise_scope(getattr(self, "_scope", "full")) == pairing.SCOPE_PEER else None
            if not me:
                self._deny(403, "only linked stations use this route")
                return
            defaults = sorted({str(c.get("project") or "") for c in _s.all_chats("all") if c.get("project")})
            real = _peers.safe_path(str(payload.get("path") or ""), _peers.roots_for(me, defaults), must_exist=False)
            if not real or os.path.isdir(real):
                self._deny(403, "outside the folders this station shares")
                return
            try:
                off = max(0, int(payload.get("offset") or 0))
            except (TypeError, ValueError):
                off = 0
            res = _peers.write_chunk(real, str(payload.get("data") or ""), off, bool(payload.get("final")), bool(payload.get("overwrite")))
            if res.get("error"):
                self._deny(400, res["error"])
                return
            self.send_json(res)
            return
        elif path == "/api/chat":
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "sending to a chat needs a full-access device")
                return
            chat_id = str(payload.get("chat_id") or "").strip()
            text = str(payload.get("text") or "").strip()
            source = str(payload.get("source") or "").strip().lower()
            model = str(payload.get("model") or "").strip()
            effort = str(payload.get("effort") or "").strip()
            provider = str(payload.get("provider") or "").strip().lower()
            if not chat_id or not text:
                self._deny(400, "chat_id and text are required")
                return
            import fastmail_lines
            res = fastmail_lines.send_to_chat(
                chat_id, text, source=source, model=model, effort=effort, target_provider=provider,
                swap=bool(payload.get("swap"))
            )
            if not res.get("delivered") and not res.get("ok"):
                self._deny(409 if res.get("method") == "unsupported" else 502, str(res.get("detail") or "not delivered"))
                return
            _s.mark_hud_cache_stale()
            self.send_json(res)
            return
        elif path == "/api/chat/new":
            import fastmail_lines
            provider = str(payload.get("provider") or "").strip().lower()
            text = str(payload.get("text") or "").strip()
            if not provider or not text:
                self._deny(400, "provider and text are required")
                return
            model = str(payload.get("model") or "").strip()
            effort = str(payload.get("effort") or "").strip()
            project_dir = str(payload.get("project_dir") or "").strip()
            if not project_dir or not os.path.isdir(project_dir):
                project_dir = str(getattr(_s, "HUD_PROJECT_DIR", "") or getattr(_s, "PROJECT_DIR", "") or os.getcwd())
            ok, detail, info = fastmail_lines._launch_handoff_session(
                provider, target_model=model, project_dir=project_dir, seed_prompt=text, effort=effort
            )
            if not ok:
                self._deny(502, str(detail or "failed to start chat"))
                return
            info = dict(info or {})
            chat_id = str(info.pop("chat_id", "") or info.pop("session_id", "") or info.get("sid") or "")
            res = {"ok": True, "detail": detail, "chat_id": chat_id}
            for k, v in info.items():
                try:
                    json.dumps(v)
                    res[k] = v
                except Exception:
                    res[k] = str(v)
            _s.mark_hud_cache_stale()
            self.send_json(res)
            return
        elif path == "/api/conversation/swap":
            if pairing._normalise_scope(getattr(self, "_scope", "full")) != pairing.SCOPE_FULL:
                self._deny(403, "swapping a conversation needs a full-access device")
                return
            import conversation_swap
            chat_id = str(payload.get("chat_id") or "").strip()
            to_provider = str(payload.get("to_provider") or "").strip()
            if not chat_id or not to_provider:
                self._deny(400, "chat_id and to_provider are required")
                return
            res = conversation_swap.swap(chat_id, to_provider, text=str(payload.get("text") or ""),
                                         model=str(payload.get("model") or ""), effort=str(payload.get("effort") or ""))
            if not res.get("delivered") and not res.get("ok"):
                self._deny(502, str(res.get("detail") or res.get("error") or "swap failed"))
                return
            self.send_json(res)
            return
        elif path == "/api/peer/mail":
            import peers as _peers
            me = _peers.by_device(getattr(self, "_device_id", "") or "") if pairing._normalise_scope(getattr(self, "_scope", "full")) == pairing.SCOPE_PEER else None
            if not me:
                self._deny(403, "only linked stations send mail here")
                return
            to = str(payload.get("to") or "").strip()
            text = str(payload.get("text") or "").strip()
            if not to or not text:
                self._deny(400, "to and text are required")
                return
            if _peers.split_remote(to) or to.count("@") > 1:
                self._deny(400, "notes are not relayed to a third station")
                return
            from cli_tools.notes import t_send_note
            sender = f"{str(payload.get('from') or 'agent').lstrip('@')}@{_peers.slug(me)}"
            try:
                result = t_send_note(to=to, text=text, from_chat=sender, mode=str(payload.get("mode") or ""), linked_sender=True)
            except ValueError as e:
                self._deny(400, str(e).splitlines()[0])
                return
            _s.mark_hud_cache_stale()
            self.send_json({"ok": True, "result": result})
            return
        elif path == "/api/mail":
            status, res = postmail.http_post_mail(payload)
            if status != 200:
                self._deny(status, res.get("error", "Error"))
                return
            _s.mark_hud_cache_stale()
            self.send_json(res)
            return
        elif path == "/api/fastmail/mute":
            try:
                out = fastmail_mute_station(payload.get("station_id"))
            except ValueError as e:
                self._deny(400, str(e))
                return
            _s.mark_hud_cache_stale()
            self.send_json(out)
            return
        # /api/composer/mail is routed via composer_api.dispatch above.
        elif path in ("/api/mail/edit", "/api/mail_edit"):
            import postmail as _pm
            if hasattr(_pm, "handle_composer_edit"):
                res = _pm.handle_composer_edit(payload)
            elif hasattr(_pm, "handle_edit_mail"):
                res = _pm.handle_edit_mail(payload)
            else:
                res = {"error": "edit handler unavailable"}
            err = res.get("error") if isinstance(res, dict) else None
            status = int(res.get("status", 400)) if isinstance(res, dict) and err else 200
            _s.mark_hud_cache_stale()
            self.send_json(res if isinstance(res, dict) else {"result": str(res)}, status=status)
            return
        elif path in ("/api/mail/cancel", "/api/mail_cancel"):
            import postmail as _pm
            if hasattr(_pm, "handle_composer_cancel"):
                res = _pm.handle_composer_cancel(payload)
            elif hasattr(_pm, "handle_cancel_mail"):
                res = _pm.handle_cancel_mail(payload)
            else:
                res = {"error": "cancel handler unavailable"}
            err = res.get("error") if isinstance(res, dict) else None
            status = int(res.get("status", 404 if "not found" in str(err).lower() else 400)) if isinstance(res, dict) and err else 200
            _s.mark_hud_cache_stale()
            self.send_json(res if isinstance(res, dict) else {"result": str(res)}, status=status)
            return
        else:
            self._deny(404, "Not found")
            return
        _s.mark_hud_cache_stale()
        self.send_json({"result": res})


def _tunnel_secret_path() -> str:
    import subway_home
    return os.path.join(subway_home.subway_dir(), "tunnel", "connector.secret")


def _tunnel_secret_ok(presented: str, peer_ip: str) -> bool:
    """The connector runs on this Mac: its header counts only from loopback and only with its 0600 secret."""
    try:
        if not ipaddress.ip_address(peer_ip).is_loopback:
            return False
    except ValueError:
        return False
    try:
        with open(_tunnel_secret_path(), "r", encoding="utf-8") as fh:
            expected = fh.read().strip()
    except OSError:
        return False
    return bool(expected) and hmac.compare_digest(presented.strip(), expected)


def _query_token_allowed(path: str) -> bool:
    """Tokens in URLs leak into logs and proxies; only the phone's event stream may still use one."""
    return path in ("/api/events", "/api/chat_stream", "/api/landing/diff/stream", "/api/worker_log/stream", "/api/diagnostics/stream")


def _replay_gap(since: int, oldest) -> bool:
    """True when a resuming client missed frames that are no longer kept, so it must refetch state."""
    return since >= 0 and oldest is not None and since < oldest - 1


class SubwayHUDServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    max_threads: Optional[int] = None  # None: SUBWAY_HUD_MAX_THREADS, else 128

    def _thread_cap(self):
        cap = getattr(self, "_cap", None)
        if cap is None:
            from hud_server import wire
            limit = self.max_threads if self.max_threads is not None else wire.max_threads_from_env()
            cap = self._cap = wire.ThreadCap(limit)
        return cap

    def process_request(self, request, client_address):
        """Refuse with 503 once `max_threads` requests are running, instead of spawning without bound."""
        cap = self._thread_cap()
        if not cap.try_acquire():
            from hud_server import wire
            with contextlib.suppress(OSError):
                request.sendall(wire.BUSY_RESPONSE)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            cap.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._thread_cap().release()

    def handle_error(self, request, client_address):
        _cls, val, _tb = sys.exc_info()
        if isinstance(val, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)
