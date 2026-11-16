#!/usr/bin/env python3
"""DeepAI lane headers, the way the DeepAI Code app (v0.3.x) sends them.

Model requests carry: X-DeepAI-Code-Theos (lane version "4", not a price),
X-DeepAI-Code-Instance (random per process) and X-DeepAI-Code-Machine
(sha256 hex of the OS machine id; the raw id never leaves the machine).
X-DeepAI-Code-Check-In / -Beacon ride on GET /theos only: the answer carries
check_in{nonce,seconds}; the app echoes the nonce and a beacon on its next
/theos call and renews at half the window. pi sends static headers, so a tiny
loopback shim (`pi_lane.py serve`) renews the check-in and injects the lane
headers. API keys are never read from argv or logged.
"""
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time

LANE_VERSION = "4"
ORIGIN = "https://api.deepai.org"
MODEL_BASE = ORIGIN + "/deepai_code"
H_THEOS, H_INSTANCE, H_MACHINE = "X-DeepAI-Code-Theos", "X-DeepAI-Code-Instance", "X-DeepAI-Code-Machine"
H_CHECK_IN, H_BEACON = "X-DeepAI-Code-Check-In", "X-DeepAI-Code-Beacon"
APP_PLIST = "/Applications/DeepAI Code.app/Contents/Info.plist"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_SMBIOS_PLACEHOLDERS = {"00000000-0000-0000-0000-000000000000", "ffffffff-ffff-ffff-ffff-ffffffffffff",
                        "03000200-0400-0500-0006-000700080009"}
_INSTANCE = secrets.token_hex(16)
_STARTED = time.time()
_machine_cache: dict = {}


def _run(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout or ""
    except Exception:
        return ""


def parse_ioplatform_uuid(ioreg: str):
    m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', ioreg or "")
    return m.group(1).lower() if m and _UUID.fullmatch(m.group(1)) else None


def _raw_machine_id(runner=_run, platform=None):
    platform = platform or sys.platform
    if platform == "darwin":
        return parse_ioplatform_uuid(runner(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"]))
    if platform == "win32":
        m = _UUID.search(runner(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                 "(Get-CimInstance Win32_ComputerSystemProduct).UUID"]))
        if m and m.group(0).lower() not in _SMBIOS_PLACEHOLDERS:
            return m.group(0).lower()
        m = re.search(r"MachineGuid\s+REG_SZ\s+(\S+)", runner(
            ["reg", "query", r"HKLM\SOFTWARE\Microsoft\Cryptography", "/v", "MachineGuid"]))
        return m.group(1).lower() if m and _UUID.fullmatch(m.group(1)) else None
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            v = open(p, encoding="utf-8").read().strip().lower()
        except OSError:
            continue
        if re.fullmatch(r"[0-9a-f]{32}", v) and not re.fullmatch(r"0+", v):
            return v
    return None


def machine_id(runner=_run, platform=None, use_cache=True):
    """sha256 hex of the raw machine id, or None when unreadable (header then omitted)."""
    if use_cache and "v" in _machine_cache:
        return _machine_cache["v"]
    raw = _raw_machine_id(runner, platform)
    digest = hashlib.sha256(raw.encode()).hexdigest() if raw else None
    if use_cache:
        _machine_cache["v"] = digest
    return digest


def build_lane_headers(instance=None, machine="auto"):
    h = {H_THEOS: LANE_VERSION, H_INSTANCE: instance or _INSTANCE}
    mid = machine_id() if machine == "auto" else machine
    if mid:
        h[H_MACHINE] = mid
    return h


def app_version(plist=APP_PLIST):
    try:
        import plistlib
        with open(plist, "rb") as f:
            return str(plistlib.load(f).get("CFBundleShortVersionString") or "")
    except Exception:
        return ""


def beacon(pi_active=True, version=None, uptime=None):
    v = app_version() if version is None else version
    up = int(time.time() - _STARTED) if uptime is None else int(uptime)
    parts = ([f"v={v}"] if v else []) + [f"up={up}"]
    if pi_active is not None:
        parts.append(f"pi={1 if pi_active else 0}")
    return ";".join(parts)


def read_check_in(payload):
    v = payload.get("check_in") if isinstance(payload, dict) else None
    if (isinstance(v, dict) and isinstance(v.get("nonce"), str) and re.fullmatch(r"[0-9a-f]{32}", v["nonce"])
            and isinstance(v.get("seconds"), (int, float)) and not isinstance(v.get("seconds"), bool) and v["seconds"] > 0):
        return {"nonce": v["nonce"], "seconds": v["seconds"]}
    return None


class CheckIn:
    """Keeps the app-style /theos check-in fresh. fetch(headers)->(status, json|None) is injectable."""

    MIN_WAIT = 2  # seconds; floor for the heartbeat sleep

    def __init__(self, fetch=None, clock=time.time, logger=None):
        self.state, self.at, self.fetch, self.clock = None, 0.0, fetch or self._http, clock
        self.lock = threading.Lock()
        self._logger_explicit = logger is not None
        self.logger = logger or (lambda _msg: None)

    def _log(self, msg):
        """Debug hook. Never logs the nonce, api key or machine id."""
        try:
            self.logger(msg)
        except Exception:
            pass

    @staticmethod
    def _http(headers):
        import urllib.request, urllib.error
        req = urllib.request.Request(MODEL_BASE + "/theos", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"null")
            except Exception:
                return e.code, None
        except Exception:
            return 0, None

    def ensure(self, api_key, force=False):
        """Renew when missing or past half the window. Never raises; a failed renewal is non-fatal.

        force=True skips the half-window early return (used by the background heartbeat).
        """
        with self.lock:
            if not force and self.state and self.clock() - self.at < self.state["seconds"] / 2:
                return self.state
            h = build_lane_headers()
            h.update({"Api-Key": api_key, H_BEACON: beacon()})
            if self.state:
                h[H_CHECK_IN] = self.state["nonce"]
            status, body = self.fetch(h)
            nxt = read_check_in(body)
            if nxt:
                self.state, self.at = nxt, self.clock()
            # status/seconds/renewed only: no nonce, api key or machine id.
            self._log("checkin status=%s seconds=%s renewed=%s" % (
                int(status or 0),
                (nxt or self.state or {}).get("seconds", 0),
                bool(nxt)))
            return self.state

    def start_heartbeat(self, api_key, stop_event):
        """Renew the check-in in the background so long streams are not cut.

        Sleeps half the current window (floor MIN_WAIT) between renewals. Never raises.
        """
        def loop():
            try:
                # bootstrap: learn the window (no-op when a fresh check-in exists)
                try:
                    self.ensure(api_key)
                except Exception:
                    pass
                while True:
                    state = self.state
                    delay = max(self.MIN_WAIT, (state["seconds"] / 2) if state else 5)
                    if stop_event.wait(delay):
                        return
                    try:
                        self.ensure(api_key, force=True)
                    except Exception:
                        pass
            except Exception:
                return

        t = threading.Thread(target=loop, daemon=True, name="pi-lane-checkin")
        t.start()
        return t


def app_catalog(home=None):
    """The DeepAI Code app's own pi deepai provider (models with theosPerRequest/premier), or None."""
    import glob
    home = home or os.path.expanduser("~")
    for f in sorted(glob.glob(os.path.join(home, ".deepai-code", "userdata", "*", "pi", "*", "models.json"))):
        try:
            prov = json.load(open(f, encoding="utf-8"))["providers"]["deepai"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(prov, dict) and isinstance(prov.get("models"), list) and prov["models"]:
            return prov
    return None


def build_provider(api_key, catalog=None, base_url=MODEL_BASE):
    """Subway's deepai provider: every catalog model (theosPerRequest/premier kept) + lane headers."""
    cat = catalog or {}
    models = [dict(m) for m in cat.get("models", []) if isinstance(m, dict) and m.get("id")]
    for m in models:
        m.pop("headers", None)
    prov = {k: v for k, v in cat.items() if k not in ("models", "baseUrl", "headers", "apiKey")}
    prov.setdefault("api", "openai-completions")
    prov.setdefault("compat", {"supportsDeveloperRole": False, "sendSessionAffinityHeaders": True})
    prov.update({"baseUrl": base_url, "apiKey": api_key,
                 "headers": {"api-key": api_key, **build_lane_headers()}, "models": models})
    return prov


# DeepAI ends long streams with `data: {"error": {"message": "upstream stream interrupted"}}`. The
# DeepAI Code app ships a pi patched to retry that text; stock pi's retry list does not match it, so
# the worker's turn died. Rewrite it to text stock pi already retries ("upstream_stream_read_error").
_INTERRUPTED = b"upstream stream interrupted"
_RETRYABLE_TEXT = b"upstream_stream_read_error: upstream stream interrupted"


def retryable_interrupts(chunks):
    """Yield chunks with the interrupted-stream error message made retryable; safe across chunk splits."""
    carry = b""
    for chunk in chunks:
        data = (carry + chunk).replace(_INTERRUPTED, _RETRYABLE_TEXT)
        keep = len(_INTERRUPTED) - 1
        # hold back a tail that could be the start of the message, unless it is already the rewritten form
        cut = len(data)
        for k in range(min(keep, len(data)), 0, -1):
            if _INTERRUPTED.startswith(data[-k:]):
                cut = len(data) - k
                break
        out, carry = data[:cut], data[cut:]
        if out:
            yield out
    if carry:
        yield carry


_HOP = {"host", "connection", "content-length", "accept-encoding", "transfer-encoding", "keep-alive"}
# The only client headers the DeepAI Code app's proxy carries over to upstream.
_CLIENT_COPY = {"content-type", "x-session-affinity"}
# The app allows one in-flight metered request at a time; ours must do the same.
_METERED = threading.Lock()
_BUSY_RETRIES = 5


def _retry_after_seconds(resp):
    """Retry-After in whole seconds, clamped to 1..30; 2 when absent or unparsable."""
    raw = resp.getheader("Retry-After")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        val = 2
    return min(30, max(1, val))


def make_server(upstream=ORIGIN, checkin=None, idle_seconds=1800, logger=None):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import http.client, urllib.parse
    checkin = checkin or CheckIn(logger=logger)
    if logger is not None and not getattr(checkin, "_logger_explicit", False):
        checkin.logger = logger
    u = urllib.parse.urlparse(upstream)
    last = {"t": time.time()}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _outbound(self):
            """Build the outbound headers exactly like the DeepAI Code app's proxy.

            Only content-type and x-session-affinity are copied from the client; everything
            else the client sent (authorization, pi's user-agent, x-stainless-*, accept,
            accept-encoding, ...) is dropped and no user-agent is set at all.
            """
            client = {k.lower(): v for k, v in self.headers.items()}
            hdr = {}
            if client.get("content-type"):
                hdr["Content-Type"] = client["content-type"]
            if client.get("x-session-affinity"):
                hdr["X-Session-Affinity"] = client["x-session-affinity"]
            key = client.get("api-key", "")
            if key:
                hdr["api-key"] = key
            hdr.update(build_lane_headers())
            if self.path.split("?")[0].rstrip("/").endswith("/theos"):
                # check-in/beacon ride on /theos only, like the app's proxy
                hdr[H_BEACON] = beacon()
                state = getattr(checkin, "state", None)
                if state:
                    hdr[H_CHECK_IN] = state["nonce"]
            return hdr, key

        def _open(self, hdr, body):
            conn = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(u.netloc, timeout=600)
            # skip_accept_encoding: the app's HTTP library sends no accept-encoding either
            conn.putrequest(self.command, self.path, skip_accept_encoding=True)
            for k, v in hdr.items():
                conn.putheader(k, v)
            if self.command in ("POST", "PUT", "PATCH"):
                conn.putheader("Content-Length", str(len(body or b"")))
            conn.endheaders(body if body else None)
            return conn

        def _send_headers(self, status, headers):
            self.send_response(status)
            for k, v in headers:
                if k.lower() not in _HOP:
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()

        def _is_busy(self, resp, raw):
            if b"session_busy" in raw.lower():
                return True
            for k, v in resp.getheaders():
                if "session_busy" in (k + " " + v).lower():
                    return True
            return False

        def _relay(self, hdr, body):
            for attempt in range(_BUSY_RETRIES + 1):
                conn = None
                try:
                    conn = self._open(hdr, body)
                    r = conn.getresponse()
                    if r.status == 409:
                        raw = r.read()
                        if attempt < _BUSY_RETRIES and self._is_busy(r, raw):
                            delay = _retry_after_seconds(r)
                            conn.close()
                            conn = None
                            time.sleep(delay)
                            continue
                        self._send_headers(r.status, r.getheaders())
                        self.wfile.write(raw)
                        self.wfile.flush()
                        return
                    self._send_headers(r.status, r.getheaders())
                    def raw_chunks():
                        while True:
                            c = r.read1(8192) if hasattr(r, "read1") else r.read(8192)
                            if not c:
                                return
                            yield c
                    for chunk in retryable_interrupts(raw_chunks()):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    return
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception:
                    try:
                        self.send_error(502, "lane shim upstream error")
                    except Exception:
                        pass
                    return
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass

        def _go(self):
            last["t"] = time.time()
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            hdr, key = self._outbound()
            if key:
                checkin.ensure(key)
            metered = (self.command == "POST"
                       and self.path.split("?")[0].rstrip("/").endswith("/chat/completions"))
            if metered:
                # The upstream cuts a stream that outlives the check-in window, so renew in
                # the background for the whole lifetime of the request.
                stop = threading.Event()
                if key:
                    try:
                        checkin.start_heartbeat(key, stop)
                    except Exception:
                        pass
                try:
                    with _METERED:
                        self._relay(hdr, body)
                finally:
                    stop.set()
            else:
                self._relay(hdr, body)

        do_GET = do_POST = do_PUT = do_DELETE = _go

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    srv.idle_seconds, srv.last_activity = idle_seconds, last
    return srv


def _state_path():
    import subway_home
    return os.path.join(subway_home.subway_dir(), "pi_lane.json")


def _alive(port):
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _write_private(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def point_models_at(models_path, port):
    """Set the deepai baseUrl in a pi models.json to the shim. Returns True when changed."""
    try:
        data = json.load(open(models_path, encoding="utf-8"))
        prov = data["providers"]["deepai"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    want = f"http://127.0.0.1:{port}/deepai_code"
    if prov.get("baseUrl") == want:
        return False
    prov["baseUrl"] = want
    mode = os.stat(models_path).st_mode & 0o777
    tmp = models_path + f".{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode or 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, models_path)
    return True


def ensure(models_path):
    """Start the shim if not running (detached) and point models_path at it. Returns the port or 0."""
    sp = _state_path()
    try:
        port = int(json.load(open(sp)).get("port", 0))
    except (OSError, ValueError, AttributeError):
        port = 0
    if not (port and _alive(port)):
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "serve"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(50):
            time.sleep(0.1)
            try:
                port = int(json.load(open(sp)).get("port", 0))
            except (OSError, ValueError, AttributeError):
                port = 0
            if port and _alive(port):
                break
        else:
            return 0
    point_models_at(models_path, port)
    return port


def _file_logger(path):
    def log(msg):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), str(msg)[:500]))
        except OSError:
            pass
    return log


def serve():
    srv = make_server(logger=_file_logger(os.path.join(os.path.dirname(_state_path()), "pi_lane.log")))
    _write_private(_state_path(), {"port": srv.server_address[1], "pid": os.getpid()})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    while time.time() - srv.last_activity["t"] < srv.idle_seconds:
        time.sleep(5)
    srv.shutdown()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "serve":
        serve()
    elif cmd == "ensure" and len(sys.argv) > 2:
        sys.exit(0 if ensure(sys.argv[2]) else 1)
    else:
        print("usage: pi_lane.py serve | ensure <models.json>")
        sys.exit(2)
