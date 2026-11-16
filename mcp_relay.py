"""Thin MCP stdio relay: forward each JSON-RPC line to the running HUD, which answers as this chat.

Stdlib only, so a chat's MCP process stays ~15 MB instead of loading all of Subway. When the HUD
can't be reached it starts it; if that fails it hands over to the full server (old path)."""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ENV_KEYS = ("SUBWAY_STATION_ID", "SUBWAY_WORKER_ID", "PI_CODING_AGENT", "PI_CODING_AGENT_DIR",
            "ANTIGRAVITY_CONVERSATION_ID", "SUBWAY_NO_NOTIFY")
HERE = os.path.dirname(os.path.abspath(__file__))


def _subway_dir() -> str:
    sys.path.insert(0, HERE)
    import subway_home
    return subway_home.subway_dir()


def enabled() -> bool:
    """Opt-in via config `mcp_relay: true`; SUBWAY_RELAY=0 forces the full server."""
    if os.environ.get("SUBWAY_RELAY") == "0":
        return False
    try:
        with open(os.path.join(_subway_dir(), "config.json"), "r", encoding="utf-8") as f:
            return bool((json.load(f) or {}).get("mcp_relay", False))
    except (OSError, ValueError):
        return False


def _caller() -> dict:
    return {"ppid": os.getppid(), "env": {k: os.environ[k] for k in ENV_KEYS if k in os.environ}}


def _refused(e: Exception) -> bool:
    """True only when nothing was sent (connection refused), so a retry can't run a tool twice."""
    r = getattr(e, "reason", e)
    return isinstance(r, ConnectionRefusedError)


class Relay:
    def __init__(self) -> None:
        self.dir = _subway_dir()
        try:
            with open(os.path.join(self.dir, "hud.json"), "r", encoding="utf-8") as f:
                self.port = int((json.load(f) or {}).get("port") or 7890)
        except (OSError, ValueError, TypeError):
            self.port = 7890
        self.lock = threading.Lock()

    def token(self) -> str:
        try:
            with open(os.path.join(self.dir, "hud_token"), "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def post(self, path: str, body: dict, timeout: float) -> dict:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token()}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def alive(self) -> bool:
        try:
            r = self.post("/api/relay/rpc", {"caller": _caller(), "request": {"jsonrpc": "2.0", "id": "probe", "method": "ping"}}, 3)
            return isinstance(r, dict) and "response" in r
        except Exception:
            return False

    def ensure_hud(self) -> bool:
        if self.alive():
            return True
        try:
            subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"), "hud", str(self.port)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=(sys.platform != "win32"))
        except OSError:
            return False
        end = time.monotonic() + 8.0
        while time.monotonic() < end:
            time.sleep(0.4)
            if self.alive():
                return True
        return False

    def write(self, obj: dict) -> None:
        with self.lock:
            sys.stdout.write(json.dumps(obj) + "\n")
            sys.stdout.flush()

    def dispatch(self, req: dict, slots: threading.Semaphore) -> None:
        with slots:
            body = {"caller": _caller(), "request": req}
            for attempt in (1, 2):
                try:
                    resp = self.post("/api/relay/rpc", body, 3600).get("response")
                    break
                except Exception as e:
                    if attempt == 1 and _refused(e) and self.ensure_hud():
                        continue
                    resp = {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32603,
                            "message": f"Subway HUD unreachable ({e}). Reconnect Subway (/mcp) to fall back to the full server."}}
                    break
            if resp is not None:
                self.write(resp)

    def pings(self) -> None:
        """Push worker notices when a worker record changes (OS file events, no polling)."""
        from fswatch import DirWatch
        wd = os.path.join(self.dir, "workers")
        os.makedirs(wd, exist_ok=True)
        watch, shown = DirWatch(wd), set()
        while True:
            try:
                for line in self.post("/api/relay/pings", {"caller": _caller()}, 10).get("lines") or []:
                    if line in shown:
                        continue
                    if len(shown) > 500:
                        shown.clear()
                    shown.add(line)
                    self.write({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": line}})
            except Exception:
                pass
            watch.wait(60.0)


def _full_server() -> int:
    """Hand over to the full in-process server (old path)."""
    env = dict(os.environ, SUBWAY_RELAY="0")
    cmd = [sys.executable, os.path.join(HERE, "server.py"), "--stdio"]
    if sys.platform == "win32":
        return subprocess.call(cmd, env=env)
    os.execve(sys.executable, cmd, env)
    return 0


def main() -> int:
    r = Relay()
    if not r.token() or not r.ensure_hud():
        return _full_server()
    threading.Thread(target=r.pings, daemon=True).start()
    slots = threading.Semaphore(8)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if not isinstance(req, dict) or req.get("id") is None:
            continue  # notifications (initialized, cancelled) need no answer
        threading.Thread(target=r.dispatch, args=(req, slots), daemon=True).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
