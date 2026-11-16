"""Runtime provider defaults discovered from local config/cache (no hardcoded ids)."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional


def _load_json_file(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_jsonc_file(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return None
    # Best-effort: strip // and /* */ comments for simple config files.
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//.*?$", "", text, flags=re.M)
    # Remove trailing commas before } or ]
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        return json.loads(text)
    except Exception:
        return None


def _opencode_config_model() -> str:
    base = os.path.join(os.path.expanduser("~"), ".config", "opencode")
    for name, loader in (("opencode.json", _load_json_file), ("opencode.jsonc", _load_jsonc_file)):
        data = loader(os.path.join(base, name))
        if not isinstance(data, dict):
            continue
        model = data.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    return ""


def resolve_opencode_default_model(subway_dir: Optional[str] = None) -> str:
    """Return configured OpenCode default model id, or first cached model if no config.

    Resolution order:
    1. ~/.config/opencode/opencode.json / opencode.jsonc "model" key
    2. Cached opencode_models.json first entry (models list) if no config
    Returns "" when unknown.
    """
    configured = _opencode_config_model()
    if configured:
        return configured

    # Fallback: cached opencode_models.json first entry only if no config.
    if subway_dir is None:
        try:
            import subway_home
            subway_dir = getattr(subway_home, "SUBWAY_DIR", "") or os.path.expanduser("~/.subway")
        except Exception:
            subway_dir = os.path.expanduser("~/.subway")
    cache_path = os.path.join(str(subway_dir), "opencode_models.json")
    data = _load_json_file(cache_path)
    if not isinstance(data, dict):
        return ""
    models = data.get("models")
    if not isinstance(models, list) or not models:
        return ""
    first = models[0]
    if isinstance(first, str):
        return first.strip()
    if isinstance(first, dict):
        for key in ("id", "model", "name"):
            val = first.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""
