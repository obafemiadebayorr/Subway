"""Read-only Theos allowance fetch from the DeepAI API (used by DeepAI Code's in-app gauge).

Reproduces the app's own request:
    GET {DEEPAI_API_ORIGIN}/deepai_code/theos
with headers: Api-Key, X-DeepAI-Code-Theos: 4, X-DeepAI-Code-Instance: <random hex>.

API key is loaded per call from DeepAI Code's local account store and never cached
to disk or logged. Results are cached in memory for 60s.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import server as _s
except Exception:
    import sys
    _s = sys.modules.get("server")

DEEPAI_API_ORIGIN = "https://api.deepai.org"
THEOS_PATH = "/theos"  # resolved against DEEPAI_MODEL_BASE = origin + /deepai_code
_REQUEST_TIMEOUT = 10.0

_ACCOUNT_SECRET_PATHS = (
    os.path.join(os.path.expanduser("~"), ".deepai-code", "userdata", "secrets", "deepai-account.bin"),
)


def _load_api_key() -> Optional[str]:
    """Load DeepAI Code's account apiKey per call. Never cached, never logged."""
    candidates = []
    try:
        home = getattr(_s, "HOME", None) or os.path.expanduser("~")
    except Exception:
        home = os.path.expanduser("~")
    candidates.append(os.path.join(home, ".deepai-code", "userdata", "secrets", "deepai-account.bin"))
    candidates.extend(_ACCOUNT_SECRET_PATHS)
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            key = data.get("apiKey")
            if isinstance(key, str) and key:
                return key
        except Exception:
            continue
    return None


def _iso_to_epoch(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _num(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _map_allowance(payload: Dict[str, Any]) -> Dict[str, Any]:
    granted = _num(payload.get("granted"))
    balance = _num(payload.get("balance"))
    spent = None
    if granted is not None and balance is not None:
        spent = max(0, granted - balance)
    return {
        "allowance": granted,
        "remaining": balance,
        "spent": spent,
        "refresh_at": _iso_to_epoch(payload.get("refresh_at")),
        "source": "deepai_api",
        "fetched_at": time.time(),
    }


def _map_exhausted(body: Any) -> Dict[str, Any]:
    """403 theos_exhausted: remaining 0, refresh_at from the error body/message."""
    refresh_epoch: Optional[float] = None
    if isinstance(body, dict):
        detail = body.get("detail") if isinstance(body.get("detail"), dict) else body
        refresh_epoch = _iso_to_epoch(detail.get("refresh_at") or detail.get("refresh"))
        if refresh_epoch is None:
            msg = detail.get("message") or detail.get("error") or ""
            if isinstance(msg, str):
                import re as _re
                m = _re.search(r"\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:?\d{2})?", msg)
                if m:
                    refresh_epoch = _iso_to_epoch(m.group(0))
    return {
        "allowance": None,
        "remaining": 0,
        "spent": None,
        "refresh_at": refresh_epoch,
        "source": "deepai_api",
        "fetched_at": time.time(),
    }


def _fetch_theos() -> Optional[Dict[str, Any]]:
    key = _load_api_key()
    if not key:
        return None
    url = f"{DEEPAI_API_ORIGIN}/deepai_code{THEOS_PATH}"
    headers = {
        "Content-Type": "application/json",
        "Api-Key": key,
        "X-DeepAI-Code-Theos": "4",
        "X-DeepAI-Code-Instance": secrets.token_hex(16),
    }
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        if not isinstance(payload, dict):
            return None
        return _map_allowance(payload)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:
            body = None
        if exc.code == 403:
            return _map_exhausted(body)
        return None
    except Exception:
        return None


def _theos_uncached() -> Optional[Dict[str, Any]]:
    return _fetch_theos()


# Stale-while-revalidate. The DeepAI API takes up to ~5 s, and this is read while the HUD snapshot
# is built, so a caller never waits on the network: it gets the last answer (None before the first
# one arrives, briefly) and an expired answer starts one background refresh.
_TTL = 60.0
_FIRST_WAIT = 0.4
_swr: Dict[str, Any] = {"at": None, "value": None, "busy": False}
_swr_lock = threading.Lock()
_swr_done = threading.Event()


def _swr_refresh() -> None:
    try:
        value = _theos_uncached()
        with _swr_lock:
            _swr["value"], _swr["at"] = value, time.monotonic()
    finally:
        with _swr_lock:
            _swr["busy"] = False
        _swr_done.set()


def theos_live() -> Optional[Dict[str, Any]]:
    with _swr_lock:
        fresh = _swr["at"] is not None and time.monotonic() - _swr["at"] < _TTL
        start = not fresh and not _swr["busy"]
        if start:
            _swr["busy"] = True
        first = _swr["at"] is None
    if start:
        threading.Thread(target=_swr_refresh, name="theos-refresh", daemon=True).start()
    if first:
        _swr_done.wait(_FIRST_WAIT)
    with _swr_lock:
        return _swr["value"]
