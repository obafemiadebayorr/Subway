"""Notice new worker screenshots (hub_media/<worker id>/) and announce them (HUD event + phone push)."""
import os
import threading
import time
from typing import Callable, Dict, Set

EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
INTERVAL = 5.0
_started = False
_lock = threading.Lock()
_seen: Dict[str, Set[str]] = {}
_last_push: Dict[str, float] = {}


def media_root() -> str:
    import subway_home
    return os.path.join(subway_home.subway_dir(), "hub_media")


def list_media(worker_ref: str):
    d = os.path.join(media_root(), worker_ref)
    if not os.path.isdir(d):
        return []
    names = [f for f in os.listdir(d) if f.lower().endswith(EXT)]
    return sorted(names, key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)


def scan_once(first: bool = False) -> int:
    """Publish one 'worker_media' event per worker that gained images. Returns how many were published."""
    root = media_root()
    if not os.path.isdir(root):
        return 0
    published = 0
    for ref in os.listdir(root):
        names = set(list_media(ref))
        new = names - _seen.get(ref, set())
        _seen[ref] = names
        if first or not new:
            continue
        try:
            import sse_stream
            sse_stream.publish("worker_media", {"id": ref, "names": sorted(new), "count": len(names)})
            published += 1
        except Exception:
            pass
    return published


def _loop(log: Callable[[str], None]) -> None:
    scan_once(first=True)
    while True:
        try:
            import permissions as _perm
            _perm.wait_for_activity(timeout=INTERVAL)
        except Exception:
            time.sleep(INTERVAL)
        try:
            scan_once()
        except Exception as e:
            log(f"media watch: {e}")


def start(log: Callable[[str], None]) -> bool:
    global _started
    with _lock:
        if _started:
            return False
        _started = True
    threading.Thread(target=_loop, args=(log,), daemon=True, name="subway-media-watch").start()
    return True
