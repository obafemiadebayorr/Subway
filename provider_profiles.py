"""Provider profile and BYOK secret management for Subway.

Profiles are stored atomically in ``SUBWAY_DIR/provider_profiles.json`` without
plaintext API keys. API keys are stored in macOS Keychain via
``keychain_native`` when available, falling back to a ``0o600`` secret file in
``SUBWAY_DIR``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from typing import Any

try:
    import keychain_native
except Exception:  # pragma: no cover - optional on non-macOS
    keychain_native = None  # type: ignore[assignment]

KEYCHAIN_SERVICE = "subway.provider_profiles"
_PROFILES_FILENAME = "provider_profiles.json"
_SECRETS_FILENAME = "provider_secrets.json"
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

_IMPORT_SUBWAY_DIR = Path(os.environ.get("SUBWAY_DIR") or (Path.home() / ".subway"))
SUBWAY_DIR: Path | str | None = _IMPORT_SUBWAY_DIR

_STATE_LOCK = threading.RLock()

BASE_PROVIDERS = ("claude", "antigravity", "opencode", "copilot", "local", "custom")
AUTH_MODES = ("api_key", "oauth", "env", "none")

_STR_FIELDS = (
    "name",
    "base_provider",
    "binary_path",
    "home_dir",
    "shadow_home_dir",
    "base_url",
    "default_model",
    "auth_mode",
)


def _resolve_subway_dir(subway_dir: str | Path | None = None) -> Path:
    """Resolve the active Subway state directory, respecting test overrides."""
    if subway_dir is not None:
        return Path(subway_dir)
    if SUBWAY_DIR is not None and Path(SUBWAY_DIR) != _IMPORT_SUBWAY_DIR:
        return Path(SUBWAY_DIR)
    env_dir = os.environ.get("SUBWAY_DIR")
    if env_dir:
        return Path(env_dir)
    if SUBWAY_DIR is not None:
        return Path(SUBWAY_DIR)
    return Path.home() / ".subway"


def _profiles_path(subway_dir: str | Path | None = None) -> Path:
    return _resolve_subway_dir(subway_dir) / _PROFILES_FILENAME


def _secrets_path(subway_dir: str | Path | None = None) -> Path:
    return _resolve_subway_dir(subway_dir) / _SECRETS_FILENAME


def _atomic_write_json(path: Path, payload: Any, mode: int = 0o644) -> None:
    """Atomically write JSON to ``path`` with explicit file permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
        os.chmod(path, mode)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _load_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_fallback_secrets(subway_dir: str | Path | None = None) -> dict[str, str]:
    data = _load_json_dict(_secrets_path(subway_dir))
    out: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str) and v:
            out[k] = v
    return out


def _save_fallback_secrets(
    secrets_map: dict[str, str], subway_dir: str | Path | None = None
) -> None:
    path = _secrets_path(subway_dir)
    if not secrets_map:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    _atomic_write_json(path, secrets_map, mode=0o600)


def _keychain_available() -> bool:
    return keychain_native is not None and hasattr(keychain_native, "set_secret")


def _store_api_key(
    profile_id: str, api_key: str, subway_dir: str | Path | None = None
) -> None:
    """Persist an API key in Keychain when available, else in a 0o600 fallback file."""
    if _keychain_available():
        try:
            keychain_native.set_secret(
                KEYCHAIN_SERVICE, profile_id, api_key.encode("utf-8")
            )
            fb = _load_fallback_secrets(subway_dir)
            if profile_id in fb:
                fb.pop(profile_id, None)
                _save_fallback_secrets(fb, subway_dir)
            return
        except Exception:
            pass

    fb = _load_fallback_secrets(subway_dir)
    fb[profile_id] = api_key
    _save_fallback_secrets(fb, subway_dir)


def _delete_api_key(profile_id: str, subway_dir: str | Path | None = None) -> None:
    """Delete an API key from Keychain (if available) and fallback secret file."""
    if keychain_native is not None and hasattr(keychain_native, "delete_secret"):
        try:
            keychain_native.delete_secret(KEYCHAIN_SERVICE, profile_id)
        except Exception:
            pass
    fb = _load_fallback_secrets(subway_dir)
    if profile_id in fb:
        fb.pop(profile_id, None)
        _save_fallback_secrets(fb, subway_dir)


def resolve_api_key(
    profile_id: str, subway_dir: str | Path | None = None
) -> str | None:
    """Return the stored API key for ``profile_id`` (internal helper; never expose via HTTP)."""
    pid = str(profile_id or "").strip()
    if not pid:
        return None
    with _STATE_LOCK:
        if keychain_native is not None and hasattr(keychain_native, "get_secret"):
            try:
                raw = keychain_native.get_secret(KEYCHAIN_SERVICE, pid)
                if isinstance(raw, (bytes, bytearray)) and raw:
                    return bytes(raw).decode("utf-8")
            except Exception:
                pass
        fb = _load_fallback_secrets(subway_dir)
        val = fb.get(pid)
        return val if val else None


def _has_stored_api_key(
    profile_id: str, subway_dir: str | Path | None = None
) -> bool:
    fb = _load_fallback_secrets(subway_dir)
    if profile_id in fb and fb[profile_id]:
        return True
    if keychain_native is not None and hasattr(keychain_native, "get_secret"):
        try:
            raw = keychain_native.get_secret(KEYCHAIN_SERVICE, profile_id)
            if isinstance(raw, (bytes, bytearray)) and len(raw) > 0:
                return True
        except Exception:
            pass
    return False


def _sanitize_profile(raw: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
    """Normalize a profile dict and guarantee plaintext api_key is never present."""
    pid = str(profile_id or raw.get("id") or "").strip()
    base_provider = str(raw.get("base_provider") or "").strip() or "custom"
    name = str(raw.get("name") or "").strip() or pid
    enabled = bool(raw.get("enabled", True))
    return {
        "id": pid,
        "name": name,
        "base_provider": base_provider,
        "enabled": enabled,
        "binary_path": str(raw.get("binary_path") or "").strip(),
        "home_dir": str(raw.get("home_dir") or "").strip(),
        "shadow_home_dir": str(raw.get("shadow_home_dir") or "").strip(),
        "base_url": str(raw.get("base_url") or "").strip(),
        "default_model": str(raw.get("default_model") or "").strip(),
        "auth_mode": str(raw.get("auth_mode") or "").strip(),
        "has_api_key": bool(raw.get("has_api_key", False)),
    }


def _load_profiles_map(subway_dir: str | Path | None = None) -> dict[str, dict[str, Any]]:
    data = _load_json_dict(_profiles_path(subway_dir))
    raw_profiles = data.get("profiles")
    out: dict[str, dict[str, Any]] = {}
    if isinstance(raw_profiles, dict):
        items = raw_profiles.items()
    elif isinstance(raw_profiles, list):
        items = ((p.get("id"), p) for p in raw_profiles if isinstance(p, dict))
    else:
        items = ()
    for key, val in items:
        if not isinstance(val, dict):
            continue
        pid = str(val.get("id") or key or "").strip()
        if not pid or not _ID_RE.fullmatch(pid):
            continue
        out[pid] = _sanitize_profile(val, pid)
    return out


def _save_profiles_map(
    profiles: dict[str, dict[str, Any]], subway_dir: str | Path | None = None
) -> None:
    clean = {pid: _sanitize_profile(p, pid) for pid, p in profiles.items()}
    _atomic_write_json(
        _profiles_path(subway_dir),
        {"version": 1, "profiles": clean},
        mode=0o644,
    )


def _generate_id(name: str, existing: dict[str, Any]) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(name or "").strip().lower()).strip(".-")
    slug = (slug or "provider")[:48]
    if slug not in existing and _ID_RE.fullmatch(slug):
        return slug
    for _ in range(16):
        candidate = f"{slug[:56]}-{secrets.token_hex(3)}"
        if candidate not in existing and _ID_RE.fullmatch(candidate):
            return candidate
    raise ValueError("unable to generate unique profile id")


def _validate_id(pid: str) -> str:
    pid = str(pid or "").strip()
    if not _ID_RE.fullmatch(pid):
        raise ValueError("id must be 1-64 chars of letters, digits, _ . -")
    return pid


def _apply_fields(
    base: dict[str, Any],
    data: dict[str, Any],
    *,
    is_create: bool,
    subway_dir: str | Path | None = None,
) -> dict[str, Any]:
    prof = dict(base)
    for field in _STR_FIELDS:
        if field in data and data[field] is not None:
            prof[field] = str(data[field]).strip()
    if is_create:
        if not prof.get("base_provider"):
            prof["base_provider"] = "custom"
        if not prof.get("name"):
            prof["name"] = prof["id"]
    if "enabled" in data and data["enabled"] is not None:
        prof["enabled"] = bool(data["enabled"])
    elif is_create:
        prof["enabled"] = True

    if "api_key" in data and data["api_key"] is not None:
        key_val = str(data["api_key"]).strip()
        if key_val:
            _store_api_key(prof["id"], key_val, subway_dir=subway_dir)
            prof["has_api_key"] = True
        else:
            _delete_api_key(prof["id"], subway_dir=subway_dir)
            prof["has_api_key"] = False
    elif is_create:
        prof["has_api_key"] = _has_stored_api_key(prof["id"], subway_dir=subway_dir)

    return _sanitize_profile(prof, prof["id"])


def list_profiles(subway_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """Return all configured provider profiles (never containing plaintext api_key)."""
    with _STATE_LOCK:
        profiles = _load_profiles_map(subway_dir)
        return [_sanitize_profile(p, pid) for pid, p in profiles.items()]


def get_profile(
    profile_id: str, subway_dir: str | Path | None = None
) -> dict[str, Any] | None:
    """Return a single provider profile by ID, or None if not found."""
    pid = str(profile_id or "").strip()
    if not pid:
        return None
    with _STATE_LOCK:
        profiles = _load_profiles_map(subway_dir)
        prof = profiles.get(pid)
        return _sanitize_profile(prof, pid) if prof is not None else None


def resolve_profile_env(
    profile_id: str, subway_dir: str | Path | None = None
) -> tuple[str | None, dict[str, str]]:
    """Resolve binary path and environment variables for a provider profile."""
    prof = get_profile(profile_id, subway_dir=subway_dir)
    if not prof or prof.get("enabled") is False:
        return (None, {})
    env: dict[str, str] = {"SUBWAY_PROFILE_ID": str(prof["id"])}
    raw_home = prof.get("shadow_home_dir") or prof.get("home_dir")
    if raw_home:
        home = os.path.expanduser(str(raw_home))
        env["CODEX_HOME"] = home
        env["CLAUDE_CONFIG_DIR"] = home
    if prof.get("base_url"):
        base_url = str(prof["base_url"])
        env["OPENAI_BASE_URL"] = base_url
        env["ANTHROPIC_BASE_URL"] = base_url
    key = resolve_api_key(str(prof["id"]), subway_dir=subway_dir)
    if key:
        env["ANTHROPIC_API_KEY"] = key
        env["OPENAI_API_KEY"] = key
    binary_path = str(prof.get("binary_path") or "").strip() or None
    return (binary_path, env)


def add_profile(
    data: dict[str, Any], subway_dir: str | Path | None = None
) -> dict[str, Any]:
    """Create a new provider profile and store any provided API key securely."""
    if not isinstance(data, dict):
        raise ValueError("profile data must be a dict")
    with _STATE_LOCK:
        profiles = _load_profiles_map(subway_dir)
        raw_id = data.get("id")
        if raw_id is not None and str(raw_id).strip():
            pid = _validate_id(str(raw_id))
            if pid in profiles:
                raise ValueError(f"profile already exists: {pid}")
        else:
            seed = str(data.get("name") or data.get("base_provider") or "provider")
            pid = _generate_id(seed, profiles)
        base = _sanitize_profile({"id": pid}, pid)
        prof = _apply_fields(base, data, is_create=True, subway_dir=subway_dir)
        profiles[pid] = prof
        _save_profiles_map(profiles, subway_dir)
        return dict(prof)


def update_profile(
    profile_id: str, data: dict[str, Any], subway_dir: str | Path | None = None
) -> dict[str, Any]:
    """Update an existing provider profile and optionally replace/clear its API key."""
    if not isinstance(data, dict):
        raise ValueError("profile data must be a dict")
    pid = _validate_id(profile_id)
    with _STATE_LOCK:
        profiles = _load_profiles_map(subway_dir)
        if pid not in profiles:
            raise KeyError(f"profile not found: {pid}")
        prof = _apply_fields(profiles[pid], data, is_create=False, subway_dir=subway_dir)
        profiles[pid] = prof
        _save_profiles_map(profiles, subway_dir)
        return dict(prof)


def upsert_profile(
    data: dict[str, Any], subway_dir: str | Path | None = None
) -> dict[str, Any]:
    """Create or update a provider profile."""
    if not isinstance(data, dict):
        raise ValueError("profile data must be a dict")
    with _STATE_LOCK:
        raw_id = data.get("id")
        if raw_id is not None and str(raw_id).strip():
            pid = _validate_id(str(raw_id))
            profiles = _load_profiles_map(subway_dir)
            if pid in profiles:
                prof = _apply_fields(
                    profiles[pid], data, is_create=False, subway_dir=subway_dir
                )
            else:
                base = _sanitize_profile({"id": pid}, pid)
                prof = _apply_fields(base, data, is_create=True, subway_dir=subway_dir)
            profiles[pid] = prof
            _save_profiles_map(profiles, subway_dir)
            return dict(prof)
        return add_profile(data, subway_dir=subway_dir)


def toggle_profile(
    profile_id: str,
    enabled: bool | None = None,
    subway_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Toggle or explicitly set the ``enabled`` flag of a provider profile."""
    pid = _validate_id(profile_id)
    with _STATE_LOCK:
        profiles = _load_profiles_map(subway_dir)
        if pid not in profiles:
            raise KeyError(f"profile not found: {pid}")
        prof = dict(profiles[pid])
        prof["enabled"] = (
            (not bool(prof.get("enabled", True))) if enabled is None else bool(enabled)
        )
        prof = _sanitize_profile(prof, pid)
        profiles[pid] = prof
        _save_profiles_map(profiles, subway_dir)
        return dict(prof)


def delete_profile(
    profile_id: str, subway_dir: str | Path | None = None
) -> bool:
    """Delete a provider profile and any stored API key. Returns True if deleted."""
    pid = _validate_id(profile_id)
    with _STATE_LOCK:
        profiles = _load_profiles_map(subway_dir)
        existed = pid in profiles
        if existed:
            profiles.pop(pid, None)
            _save_profiles_map(profiles, subway_dir)
        _delete_api_key(pid, subway_dir=subway_dir)
        return existed
