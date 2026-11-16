"""Read, validate and write settings from hud_server/settings_schema.py.

handle_get / handle_post are the whole /api/settings loop for managed keys. handle_post validates every
managed key first and writes nothing when any one is invalid, then returns the payload keys it does not
own so the caller can hand them to the legacy per-key handlers.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import server as _s

from . import settings_schema as schema


def load_config() -> Dict[str, Any]:
    try:
        cfg = _s._load_json("config.json")
    except Exception:
        return {}
    return cfg if isinstance(cfg, dict) else {}


def coerce(spec: Dict[str, Any], raw: Any) -> Tuple[bool, Any]:
    """(True, value) or (False, error message). Strings are accepted so the CLI can pass argv as is."""
    key, kind = spec["key"], spec["type"]
    if kind == "bool":
        if isinstance(raw, bool):
            return True, raw
        text = str(raw).strip().lower()
        if text in ("true", "1", "on", "yes"):
            return True, True
        if text in ("false", "0", "off", "no"):
            return True, False
        return False, f"{key} must be a boolean"
    if kind == "int":
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return (True, None) if spec.get("nullable") else (False, f"{key} is required")
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            return False, f"Invalid {key}: {raw!r}"
        try:
            number = int(str(raw).strip())
        except ValueError:
            return False, f"Invalid {key}: {raw!r}"
        lo, hi = spec.get("min"), spec.get("max")
        if (lo is not None and number < lo) or (hi is not None and number > hi):
            return False, f"{key} out of range (must be {lo}..{hi}): {number}"
        return True, number
    if kind == "enum":
        text = str(raw).strip().lower()
        if text not in spec.get("choices", []):
            return False, f"Invalid {key}: {raw!r} (one of: {', '.join(spec.get('choices', []))})"
        return True, text
    if kind == "multi":
        items = raw.split(",") if isinstance(raw, str) else raw
        if not isinstance(items, (list, tuple)):
            return False, f"{key} must be a list"
        choices = list(spec.get("choices", []))
        picked = []
        for item in items:
            text = str(item).strip().lower()
            if not text:
                continue
            if text not in choices:
                return False, f"Invalid {key}: {item!r} (choose from: {', '.join(choices)})"
            picked.append(text)
        if not picked:
            return False, f"{key} needs at least one of: {', '.join(choices)}"
        return True, [c for c in choices if c in picked]
    if isinstance(raw, str):
        return True, raw.strip()
    return False, f"{key} must be a string"


def value(key: str, cfg: Optional[Dict[str, Any]] = None) -> Any:
    """The effective value: the stored value (or a legacy alias), else the schema default. A stored value
    that no longer validates falls back to the default instead of raising."""
    spec = schema.get(key)
    if spec is None:
        raise KeyError(key)
    cfg = load_config() if cfg is None else cfg
    for name in [key] + list(spec.get("aliases", [])):
        if name in cfg:
            ok, val = coerce(spec, cfg[name])
            if ok and (val is not None or spec.get("nullable")):
                return val
    return spec["default"]


def shape_enabled(name: str) -> bool:
    """True when Settings > Workers > Delegation allows this delegation shape."""
    return str(name) in (value("delegation.shapes") or [])


def read_all(cfg: Optional[Dict[str, Any]] = None, scope: str = "global") -> Dict[str, Any]:
    cfg = load_config() if cfg is None else cfg
    return {k: value(k, cfg) for k in schema.keys(scope=scope)}


def handle_get(query: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    """GET /api/settings: the schema and the effective global values. Optional query: {"group": "..."}."""
    group = str((query or {}).get("group") or "")
    specs = [dict(s) for s in schema.SCHEMA if not group or s["group"] == group]
    return 200, {"schema": specs, "values": read_all()}


def handle_post(payload: Any) -> Tuple[int, Dict[str, Any], Dict[str, Any]]:
    """POST /api/settings for managed keys. Returns (status, body, remaining_payload).
    200 body: {"applied": {key: value}}. 400 body: {"error": msg}, nothing written, remaining == payload."""
    payload = payload if isinstance(payload, dict) else {}
    applied: Dict[str, Any] = {}
    rest: Dict[str, Any] = {}
    for name, raw in payload.items():
        spec = schema.resolve(name)
        if spec is None or not spec.get("managed") or spec["scope"] != "global":
            rest[name] = raw
            continue
        ok, val = coerce(spec, raw)
        if not ok:
            return 400, {"error": val}, payload
        applied[spec["key"]] = val
    if applied:
        with _s._state_lock("config"):
            cfg = _s._load_json("config.json") or {}
            for key, val in applied.items():
                for name in [key] + list(schema.get(key).get("aliases", [])):
                    if val is None:
                        cfg.pop(name, None)
                    else:
                        cfg[name] = val
            _s._save_json("config.json", cfg)
    return 200, {"applied": applied}, rest
