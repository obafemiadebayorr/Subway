import base64
import binascii
import os
import re
import secrets
import time
from typing import List, Optional, Tuple

import server as _s

MAX_BYTES = 10 * 1024 * 1024
UPLOAD_MAX_BODY = 14 * 1024 * 1024        # base64 of 10 MB + JSON envelope
KEEP_DAYS = 7
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def attachments_dir() -> str:
    return os.path.join(_s.SUBWAY_DIR, "attachments")


def safe_name(name: Optional[str]) -> str:
    base = re.split(r"[/\\]", str(name or ""))[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    cleaned = cleaned.lstrip(".")
    cleaned = cleaned[:80]
    return cleaned if cleaned else "file"


def prune(now: Optional[float] = None) -> int:
    now_ts = time.time() if now is None else now
    cutoff = now_ts - (KEEP_DAYS * 86400)
    adir = attachments_dir()
    if not os.path.exists(adir):
        return 0

    count = 0
    for root, dirs, files in os.walk(adir, topdown=False):
        for f in files:
            fpath = os.path.join(root, f)
            try:
                mtime = os.path.getmtime(fpath)
                if mtime < cutoff:
                    os.remove(fpath)
                    count += 1
            except OSError:
                pass
        if root != adir:
            try:
                os.rmdir(root)
            except OSError:
                pass
    return count


def is_image(path: Optional[str], mime: str = "") -> bool:
    mime_str = str(mime or "").lower()
    if mime_str.startswith("image/"):
        return True
    ext = os.path.splitext(str(path or ""))[1].lower()
    return ext in IMAGE_EXT


def save_upload(name: str, mime: str, data_b64: str) -> Tuple[Optional[dict], Optional[str]]:
    prune()
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError):
        return (None, "Invalid base64")

    if len(raw) == 0:
        return (None, "Empty file")
    if len(raw) > MAX_BYTES:
        return (None, "File is over 10 MB")

    day_str = time.strftime("%Y%m%d")
    day_dir = os.path.join(attachments_dir(), day_str)
    os.makedirs(day_dir, mode=0o700, exist_ok=True)

    sname = safe_name(name)
    fd = None
    fpath = ""
    for _ in range(10):
        fname = f"{secrets.token_hex(4)}-{sname}"
        fpath = os.path.join(day_dir, fname)
        try:
            fd = os.open(fpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            continue

    if fd is None:
        return (None, "Failed to create attachment file")

    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
    except Exception as e:
        return (None, f"Failed to write attachment: {e}")

    abs_path = os.path.abspath(fpath)
    info = {
        "path": abs_path,
        "name": sname,
        "size": len(raw),
        "type": mime,
        "is_image": is_image(abs_path, mime),
    }
    return (info, None)


def validate(paths: Optional[List[str]]) -> Tuple[List[str], Optional[str]]:
    if not paths:
        return ([], None)
    adir_real = os.path.realpath(attachments_dir())
    valid_paths = []
    for p in paths:
        if not p or not isinstance(p, str):
            return ([], f"Attachment outside the attachments folder: {p}")
        real_p = os.path.realpath(p)
        try:
            if os.path.commonpath([adir_real, real_p]) != adir_real or real_p == adir_real:
                return ([], f"Attachment outside the attachments folder: {p}")
        except (ValueError, OSError):
            return ([], f"Attachment outside the attachments folder: {p}")
        if not os.path.isfile(real_p):
            return ([], f"Attachment outside the attachments folder: {p}")
        valid_paths.append(real_p)
    return (valid_paths, None)


def section(paths: Optional[List[str]]) -> str:
    if not paths:
        return ""
    lines = ["\n\n=== ATTACHMENTS (read these files by absolute path) ==="]
    for p in paths:
        img = " (image)" if is_image(p) else ""
        lines.append(f"- {p}{img}")
    return "\n".join(lines) + "\n"


# Single source of truth for vision input support across providers and model identifiers.
# Maps provider names to the set of vision-capable model names or prefix patterns.
VISION_MODEL_TABLE: dict[str, frozenset[str]] = {
    "claude": frozenset({
        "opus",
        "sonnet",
        "haiku",
        "claude-3-opus",
        "claude-3-5-sonnet",
        "claude-3-5-haiku",
        "claude-3-7-sonnet",
    }),
    "antigravity": frozenset({
        "gemini",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-3.8-flash",
        "gemini-3.8-flash-low",
        "gemini-3.8-flash-medium",
        "gemini-3.8-flash-high",
        "gemini-4-argon",
        "gemini-4-argon-low",
        "gemini-4-argon-medium",
        "gemini-4-argon-high",
    }),
    "local": frozenset({
        "minicpm-v",
        "minicpm-v:latest",
        "llama-vl",
        "llama-vl-something",
        "qwen-vl",
        "qwen2-vl",
    }),
}

# Alias for named constant table compatibility
VISION_CAPABLE_MODELS = VISION_MODEL_TABLE

_IN_CATALOG_CHECK = False


def model_has_vision(provider: Optional[str], model: Optional[str]) -> bool:
    prov = str(provider or "").strip().lower()
    mod = str(model or "").strip().lower()

    # 1. Check if model_catalog() has an explicit vision capability for this provider/model
    global _IN_CATALOG_CHECK
    if not _IN_CATALOG_CHECK:
        _IN_CATALOG_CHECK = True
        try:
            catalog_fn = getattr(_s, "model_catalog", None)
            if callable(catalog_fn):
                catalog = catalog_fn()
                if isinstance(catalog, list):
                    for entry in catalog:
                        if isinstance(entry, dict):
                            cat_prov = str(entry.get("provider", "")).strip().lower()
                            cat_mod = str(entry.get("model", "")).strip().lower()
                            if cat_prov == prov and (cat_mod == mod or (mod and cat_mod and (mod in cat_mod or cat_mod in mod))):
                                if "vision" in entry and entry["vision"] is not None:
                                    return bool(entry["vision"])
        except (AttributeError, TypeError, KeyError):
            pass
        finally:
            _IN_CATALOG_CHECK = False

    # 2. Look up against the single named module-level constant table
    allowed_models = VISION_MODEL_TABLE.get(prov)
    if not allowed_models:
        return False

    if mod in allowed_models:
        return True

    for known in allowed_models:
        if known and (mod.startswith(known) or known.startswith(mod) or known in mod):
            return True

    return False
