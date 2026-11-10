from typing import Dict, Any, Optional, List
import json, os, time, threading, urllib.request

_dedupe_cache: Dict[str, float] = {}
_dedupe_lock = threading.Lock()

class BaseNotifier:
    def send(self, title: str, message: str, kind: str = "info", data: Optional[Dict[str, Any]] = None) -> bool:
        raise NotImplementedError

class IDENotifier(BaseNotifier):
    """Pushes notification events to active IDE extensions (VS Code, Antigravity, Cursor, Devin) via filesystem mailbox and SSE."""
    def send(self, title: str, message: str, kind: str = "info", data: Optional[Dict[str, Any]] = None) -> bool:
        delivered = False
        import server as _s
        editor_dir = os.path.join(_s.SUBWAY_DIR, "editor_windows")
        if os.path.isdir(editor_dir):
            try:
                for entry in os.listdir(editor_dir):
                    if entry.endswith(".json") and not entry.endswith(".notify.json") and not entry.endswith(".tmp"):
                        pid = entry.replace(".json", "")
                        notify_file = os.path.join(editor_dir, f"{pid}.notify.json")
                        tmp_file = f"{notify_file}.tmp-{os.getpid()}"
                        payload = {"title": title, "message": message, "kind": kind, "data": data or {}}
                        with open(tmp_file, "w", encoding="utf-8") as f:
                            json.dump(payload, f)
                        os.replace(tmp_file, notify_file)
                        delivered = True
            except Exception:
                pass
        try:
            # Roadmap 38a2: push to /api/events subscribers. This used to test
            # hasattr(_s, "_broadcast_sse_event"), a symbol that has never existed on the
            # server, so the SSE stream carried connect frames and heartbeats and nothing
            # else while the endpoint was documented as a live event feed.
            import sse_stream
            payload = {"title": title, "message": message, "kind": kind, "data": data or {}}
            if sse_stream.publish("notification", payload):
                delivered = True
        except Exception:
            pass
        return delivered

class WebhookNotifier(BaseNotifier):
    """Pushes notification events to headless agents (Devin, external agents) via HTTP webhook."""
    def __init__(self, url: str):
        self.url = url
    def send(self, title: str, message: str, kind: str = "info", data: Optional[Dict[str, Any]] = None) -> bool:
        if not self.url: return False
        try:
            req = urllib.request.Request(self.url, data=json.dumps({"title": title, "message": message, "kind": kind, "data": data or {}}).encode(), headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3)
            return True
        except Exception:
            return False

class DesktopNotifier(BaseNotifier):
    """Desktop OS banner fallback."""
    def send(self, title: str, message: str, kind: str = "info", data: Optional[Dict[str, Any]] = None) -> bool:
        import server as _s
        route = (data or {}).get("route", "workers")
        mech, err = _s._send_desktop_notification(title, message, route=route)
        return err is None

DRIVERS = {
    "ide": IDENotifier,
    "agent": WebhookNotifier,
    "desktop": DesktopNotifier
}

def dispatch_notification(title: str, message: str, kind: str = "info", data: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
    data_dict = data or {}
    key_prefix = data_dict.get('worker_id') or data_dict.get('key') or title
    dedupe_key = f"{key_prefix}:{message}:{kind}"
    now = time.time()

    with _dedupe_lock:
        # Clean up stale keys older than 60 seconds
        stale = [k for k, ts in _dedupe_cache.items() if now - ts > 60.0]
        for k in stale:
            del _dedupe_cache[k]

        last_dispatched = _dedupe_cache.get(dedupe_key)
        if last_dispatched is not None and (now - last_dispatched) < 10.0:
            return {"ide": False, "desktop": False, "webhook": False, "deduped": True}

        _dedupe_cache[dedupe_key] = now

    targets = (config or {}).get("notification_targets", ["ide", "desktop"])
    results = {}
    for target in targets:
        if target == "agent":
            url = (config or {}).get("notification_webhook") or ""
            results["agent"] = WebhookNotifier(url).send(title, message, kind, data)
        elif target in DRIVERS:
            results[target] = DRIVERS[target]().send(title, message, kind, data)
    return results
