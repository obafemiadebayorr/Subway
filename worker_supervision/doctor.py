"""Failure doctor: when a worker fails, queue one cheap doctor that diagnoses and rebriefs.

The doctor reads the failed worker's brief, how it ended, the tail of its log and
the failure digest of repeated past failures. It names the root cause and then
REBRIEFS: it queues exactly ONE fix worker carrying the exact remaining work.
It never edits and never commits itself, and it is never auto-approved: it lands
in PENDING APPROVAL like any other worker, so the user still decides.
"""
from __future__ import annotations

import contextlib
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import server as _s

from .pumps import _role_kind

_LOADED_MTIME = os.path.getmtime(__file__)


def fresh():
    """This module, reloaded if its file changed since import, so doctor fixes apply without a reconnect."""
    import importlib, sys
    mod = sys.modules[__name__]
    try:
        mtime = os.path.getmtime(__file__)
    except OSError:
        return mod
    if getattr(mod, "_LOADED_MTIME", mtime) < mtime:
        mod = importlib.reload(mod)
    mod._LOADED_MTIME = mtime
    return mod


# --- CONTRACT: config.json keys, allowed values and defaults ----------------
DOCTOR_MODE_KEY = "doctor_mode"
DOCTOR_BUDGET_MODE_KEY = "doctor_budget_mode"
DOCTOR_BUDGET_TOKENS_KEY = "doctor_budget_tokens"
DOCTOR_BUDGET_CEILING_KEY = "doctor_budget_ceiling"
DOCTOR_MODEL_KEY = "doctor_model"
DEFAULT_DOCTOR_MODEL = "minimax-m3.1-flash-free"

DOCTOR_MODES = ("off", "on", "auto")
BUDGET_MODES = ("fixed", "dynamic")

DEFAULT_DOCTOR_MODE = "auto"
DEFAULT_BUDGET_MODE = "dynamic"
DEFAULT_BUDGET_TOKENS = 150000
DEFAULT_BUDGET_CEILING = 400000

TOKENS_MIN = 20000
TOKENS_MAX = 2000000
CEILING_MIN = 20000
CEILING_MAX = 4000000
BUDGET_MIN = 20000
BUDGET_MAX = 4000000
DYNAMIC_MIN = 50000  # a dynamic doctor never gets less than this

FAILURE_STATUSES = ("failed", "timed_out", "budget_stopped")
BUDGET_STATUSES = ("budget_stopped", "timed_out")
DOCTOR_ROLE = "Failure Doctor"

# A doctor is a standing helper, never a top model, and never the same model
# that just failed when another cheap one is available.
_TOP_MODEL_RE = re.compile(r"opus|sonnet|gpt-5|gpt-5-pro|o3-pro", re.IGNORECASE)
# "ping" style roles are chatter, not builders: they never need a doctor.
_SKIP_ROLE_RE = re.compile(r"(?:^|[^a-z])(ping|pinger|pinging)(?:[^a-z]|$)", re.IGNORECASE)
_PROVIDER_ALIASES = {
    "claude-code": "claude", "opencode2": "opencode", "agy": "antigravity",
    "ollama": "local", "github-copilot": "copilot", "deepai": "pi",
}


# --- config helpers --------------------------------------------------------
def _config() -> Dict[str, Any]:
    try:
        cfg = _s._load_json("config.json")
    except Exception:
        cfg = None
    return cfg if isinstance(cfg, dict) else {}


def _as_int(value: Any, default: int) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def doctor_mode(cfg: Optional[Dict[str, Any]] = None) -> str:
    """doctor_mode from config.json, defaulting to "auto". Corrupt values fall back."""
    cfg = _config() if cfg is None else (cfg or {})
    val = str(cfg.get(DOCTOR_MODE_KEY, DEFAULT_DOCTOR_MODE) or DEFAULT_DOCTOR_MODE).strip().lower()
    return val if val in DOCTOR_MODES else DEFAULT_DOCTOR_MODE


def doctor_budget_mode(cfg: Optional[Dict[str, Any]] = None) -> str:
    cfg = _config() if cfg is None else (cfg or {})
    val = str(cfg.get(DOCTOR_BUDGET_MODE_KEY, DEFAULT_BUDGET_MODE) or DEFAULT_BUDGET_MODE).strip().lower()
    return val if val in BUDGET_MODES else DEFAULT_BUDGET_MODE


def doctor_budget_tokens(cfg: Optional[Dict[str, Any]] = None) -> int:
    cfg = _config() if cfg is None else (cfg or {})
    val = _as_int(cfg.get(DOCTOR_BUDGET_TOKENS_KEY, DEFAULT_BUDGET_TOKENS), -1)
    if val < TOKENS_MIN or val > TOKENS_MAX:
        return DEFAULT_BUDGET_TOKENS
    return val


def doctor_budget_ceiling(cfg: Optional[Dict[str, Any]] = None) -> int:
    cfg = _config() if cfg is None else (cfg or {})
    val = _as_int(cfg.get(DOCTOR_BUDGET_CEILING_KEY, DEFAULT_BUDGET_CEILING), -1)
    if val < CEILING_MIN or val > CEILING_MAX:
        return DEFAULT_BUDGET_CEILING
    return val


def doctor_model(cfg: Optional[Dict[str, Any]] = None) -> str:
    """doctor_model from config.json. Missing -> the default; empty / "auto" -> let the picker choose."""
    cfg = _config() if cfg is None else (cfg or {})
    if DOCTOR_MODEL_KEY not in cfg:
        return DEFAULT_DOCTOR_MODEL
    raw = cfg.get(DOCTOR_MODEL_KEY)
    return raw.strip() if isinstance(raw, str) else DEFAULT_DOCTOR_MODEL


def _configured_model(failed_model: str) -> Optional[Tuple[str, str]]:
    """(provider, model) for the doctor_model setting when some provider lists it, is not the failed
    model and is not marked bad; else None so the cheap-model picker runs."""
    want = doctor_model()
    if not want or want.lower() in ("auto", "inherit", "default"):
        return None
    for prov in ("opencode", "claude", "antigravity", "copilot", "local", "pi"):
        for cand in _discovered(prov):
            if _same_model(cand, want) and not _same_model(cand, failed_model) and not _model_is_bad(cand):
                return prov, str(cand)
    return None


def worker_doctor_mode(meta: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> str:
    """The per-call override on the worker wins over the config setting."""
    override = str((meta or {}).get("doctor") or "").strip().lower()
    if override in DOCTOR_MODES:
        return override
    return doctor_mode(cfg)


def tokens_used(meta: Dict[str, Any]) -> int:
    """Tokens the worker actually spent (input + output, cache writes included)."""
    usage = (meta or {}).get("usage")
    usage = usage if isinstance(usage, dict) else {}
    total = 0
    for key in ("input", "output", "cache_creation"):
        total += max(0, _as_int(usage.get(key), 0))
    if total <= 0:
        total = max(0, _as_int(usage.get("cache_read"), 0))
    return total


def doctor_budget_for(meta: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> int:
    """Budget for this doctor's own run: fixed, dynamic, or the per-call override."""
    cfg = _config() if cfg is None else (cfg or {})
    override = _as_int((meta or {}).get("doctor_budget"), 0)
    if override > 0:
        return _clamp(override, BUDGET_MIN, BUDGET_MAX)
    if doctor_budget_mode(cfg) == "fixed":
        return doctor_budget_tokens(cfg)
    ceiling = doctor_budget_ceiling(cfg)
    sized = int(tokens_used(meta) * 1.5)
    sized = max(DYNAMIC_MIN, sized)
    return _clamp(sized, BUDGET_MIN, min(ceiling, BUDGET_MAX))


# --- eligibility -----------------------------------------------------------
def _normalize_provider(provider: Any) -> str:
    p = str(provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(p, p)


def _is_doctor_worker(meta: Dict[str, Any]) -> bool:
    """True for a worker that IS a doctor: it never gets a doctor of its own."""
    if (meta or {}).get("is_doctor"):
        return True
    return str((meta or {}).get("role") or "").strip().lower().startswith(DOCTOR_ROLE.lower())


def _is_builder(meta: Dict[str, Any]) -> bool:
    """Builder roles that edit or run tests. Read-only, research, review and ping are not."""
    role = str((meta or {}).get("role") or "")
    if _SKIP_ROLE_RE.search(role.lower()):
        return False
    kind = str((meta or {}).get("role_kind") or "").strip().lower()
    if not kind:
        kind = _role_kind(role, (meta or {}).get("capabilities") or (meta or {}).get("caps") or [])
    if kind != "builder":
        return False
    caps = (meta or {}).get("capabilities")
    if isinstance(caps, (list, tuple, set, frozenset)) and "files:write" not in caps:
        return False
    return True


def should_doctor(meta: Dict[str, Any], status: Any = None,
                  cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Does this failed worker get a doctor?"""
    if not isinstance(meta, dict) or not meta:
        return False
    st = str(status if status is not None else (meta.get("status") or "")).strip().lower()
    if st not in FAILURE_STATUSES:
        return False
    if _is_doctor_worker(meta):
        return False
    if meta.get("doctor_queued"):
        return False
    mode = worker_doctor_mode(meta, cfg)
    if mode == "off":
        return False
    if mode == "on":
        return True
    return _is_builder(meta)


# --- model choice ----------------------------------------------------------
def _is_top_model(model: Any) -> bool:
    return bool(_TOP_MODEL_RE.search(str(model or "").lower()))


def _is_free_opencode(model: Any) -> bool:
    m = str(model or "").strip().lower()
    if not m or m.startswith("beam-"):  # beam models are auth-blocked
        return False
    return "free" in m or m == "big-pickle"


def _model_is_bad(model: Any) -> bool:
    try:
        return bool(_s._model_is_bad(str(model or "")))
    except Exception:
        return False


def _cost_rank(provider: str, model: str) -> int:
    try:
        return int(_s._reroute_cost_rank(provider, model))
    except Exception:
        return 3


def _same_model(a: Any, b: Any) -> bool:
    a = str(a or "").strip().lower()
    b = str(b or "").strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    return a.split("/", 1)[-1] == b.split("/", 1)[-1]


def _cheapest_ok(models: List[str], failed_model: str, provider: str,
                 allow_top: bool = False) -> str:
    """Cheapest candidate that is not the failed model, not known bad, and (by default) not a top model."""
    best = ""
    best_rank = 99
    for cand in models:
        name = str(cand or "").strip()
        if not name or name in ("auto", "inherit", "default"):
            continue
        if _same_model(name, failed_model):
            continue
        if not allow_top and _is_top_model(name):
            continue
        if "opus" in name.lower():  # the top model is never a standing agent
            continue
        if _model_is_bad(name):
            continue
        rank = _cost_rank(provider, name)
        if rank < best_rank or (rank == best_rank and best and _is_free_opencode(name) and not _is_free_opencode(best)):
            best = name
            best_rank = rank
    return best


def _discovered(provider: str) -> List[str]:
    with contextlib.suppress(Exception):
        return [str(m) for m in (_s._discovered_model_ids(provider) or [])]
    return []


def pick_doctor_model(meta: Dict[str, Any]) -> Tuple[str, str]:
    """A cheap (or free) model, different from the one that failed where possible.

    Same provider first, then a free OpenCode model that is not known bad, then
    any other provider's cheap model. Opus is never used.
    """
    provider = _normalize_provider((meta or {}).get("provider"))
    failed_model = str((meta or {}).get("model_used") or (meta or {}).get("model") or "").strip()
    configured = _configured_model(failed_model)
    if configured:
        return configured
    if provider:
        pick = _cheapest_ok(_discovered(provider), failed_model, provider)
        if pick:
            return provider, pick
    # A free OpenCode model that is not currently marked bad.
    free_ids = [m for m in _discovered("opencode") if _is_free_opencode(m)]
    pick = _cheapest_ok(free_ids, failed_model, "opencode")
    if pick:
        return "opencode", pick
    # Any other provider's cheap model.
    for alt in ("antigravity", "copilot", "local", "pi"):
        pick = _cheapest_ok(_discovered(alt), failed_model, alt)
        if pick:
            return alt, pick
    # Last resort: the same provider, one tier below the top model.
    if provider:
        pick = _cheapest_ok(_discovered(provider), failed_model, provider, allow_top=True)
        if pick:
            return provider, pick
    # Nothing cheap is discovered: let the router pick, rather than choosing a top model here.
    return "auto", ""


# --- the brief -------------------------------------------------------------
def _digest_lines(limit: int = 8) -> List[str]:
    """The failure digest: repeated past failures and the advice attached to them."""
    try:
        import worker_digest as _wd
        items = _wd.digest()
    except Exception:
        return []
    lines: List[str] = []
    for item in (items or [])[:limit]:
        line = str((item or {}).get("line") or "").strip()
        if line:
            lines.append(line)
    return lines


def doctor_brief(meta: Dict[str, Any], result: Optional[Dict[str, Any]] = None,
                 digest_lines: Optional[List[str]] = None) -> str:
    """The doctor's brief: the failed brief, how it ended, its log tail and the digest."""
    meta = meta or {}
    result = result or {}
    wid = str(meta.get("id") or "")
    role = str(meta.get("role") or "worker")
    provider = _normalize_provider(meta.get("provider"))
    model = str(meta.get("model_used") or meta.get("model") or "").strip()
    status = str(result.get("status") or meta.get("status") or "failed").strip()
    diagnose_only = status.lower() in BUDGET_STATUSES
    reason = str(result.get("reason") or meta.get("reason") or "").strip()
    brief = str(meta.get("prompt") or "").strip()
    lines = list(digest_lines if digest_lines is not None else _digest_lines())

    log_tail = ""
    with contextlib.suppress(Exception):
        log_tail = str(_s._log_tail(meta.get("log_file"), 40) or "").strip()

    parts = [
        f"You are the failure doctor for worker `{wid}` ({role}, {provider}"
        + (f"/{model}" if model else "")
        + f"), which ended {status}.",
        "",
        "YOUR JOB",
        "1. Read the evidence below: the failed worker's own brief, how it ended, the tail of its log "
        "and the failure digest of repeated past failures.",
        "2. Name the root cause in one or two sentences.",
        *(
            [
                "3. Do NOT queue any worker: this worker hit the budget its user set, so spending more is the user's call.",
                "4. Your final report states the root cause, then the exact remaining edits (paths, symbols, code) so the user can rerun it.",
            ]
            if diagnose_only else
            [
                "3. Then queue EXACTLY ONE fix worker with spin_worker (role, prompt, provider/model, max_tokens) "
                "that carries the exact remaining work: the paths, the symbols, the edit.",
                "4. Your final report states the root cause, the fix worker's id, and the exact remaining edits.",
            ]
        ),
        "",
        "RULES",
        *(
            [
                "- Do NOT queue any worker: diagnose only, the user decides whether to spend more.",
            ]
            if diagnose_only else
            [
                "- Queue exactly ONE fix worker. Never a second one.",
            ]
        ),
        "- Do not edit files and do not commit anything yourself: you rebrief, the fix worker does the work.",
        "- Put the exact code in the fix worker's brief. A fix worker that has to search burns the budget.",
        "- If the task is already done, or a retry would not help, say so and queue nothing.",
        "",
        f"FAILED WORKER BRIEF ({wid}):",
        brief[:20000] or "(no brief recorded)",
        "",
        "HOW IT ENDED:",
        f"status: {status}",
        f"reason: {reason or '(no reason recorded)'}",
    ]
    if log_tail:
        parts += ["", "LOG TAIL:", log_tail[-8000:]]
    if lines:
        parts += ["", "FAILURE DIGEST (repeated past failures):"]
        parts += [f"- {line}" for line in lines]
    else:
        parts += ["", "FAILURE DIGEST: no repeated failure pattern recorded yet."]
    return "\n".join(parts)


# --- queueing --------------------------------------------------------------
def _mark_doctor(meta: Dict[str, Any]) -> None:
    """Mark the doctor worker so it never gets a doctor of its own."""
    try:
        with _s._state_lock("workers"):
            cur = _s._load_worker(str(meta.get("id") or "")) or meta
            cur["is_doctor"] = True
            _s._save_worker(cur)
    except Exception:
        pass


def queue_failure_doctor(worker_id: str, meta: Dict[str, Any],
                         result: Dict[str, Any]) -> Dict[str, Any]:
    """Queue one doctor for a failed worker. Never auto-approved."""
    meta = meta or {}
    result = result or {}
    cfg = _config()
    status = str(result.get("status") or meta.get("status") or "").strip().lower()
    if not should_doctor(meta, status=status, cfg=cfg):
        return {"queued": False, "reason": "not eligible", "worker_id": worker_id}
    if not worker_id:
        return {"queued": False, "reason": "no worker id"}

    budget = doctor_budget_for(meta, cfg)
    provider, model = pick_doctor_model(meta)
    brief = doctor_brief(meta, result)
    failed_budget = meta.get("budget") if isinstance(meta.get("budget"), dict) else {}
    diagnose_only = status.lower() in BUDGET_STATUSES
    spin_role = f"{DOCTOR_ROLE} (diagnose only)" if diagnose_only else DOCTOR_ROLE

    # Claim the failed worker first: exactly one doctor per failed worker.
    try:
        with _s._state_lock("workers"):
            cur = _s._load_worker(worker_id) or meta
            if cur.get("doctor_queued"):
                return {"queued": False, "reason": "already queued", "worker_id": worker_id}
            cur["doctor_queued"] = True
            _s._save_worker(cur)
            meta = cur
    except Exception as exc:
        return {"queued": False, "reason": f"could not claim worker: {exc}", "worker_id": worker_id}

    spin_kwargs: Dict[str, Any] = dict(
        role=spin_role,
        prompt=brief,
        provider=provider or "auto",
        model=model,
        track=str(meta.get("track") or "main"),
        project_dir=str(meta.get("project_dir") or ""),
        timeout_seconds=int(meta.get("timeout_seconds") or 0),
        effort="low" if diagnose_only else "auto",
        max_tokens=budget,
        max_seconds=int(failed_budget.get("max_seconds") or 0),
        private=bool(meta.get("private")),
        permission_mode=str(meta.get("permission_mode") or ""),
        capabilities=["files:read", "terminal:safe", "mcp", "subagents"],
        doctor="off",  # a doctor never gets a doctor
        doctor_budget=0,
        **({"max_tool_calls": 12} if diagnose_only else {}),
    )
    reply = ""
    doctor_id = ""
    try:
        with _s._thread_parent(worker_id, "doctor", {"role": DOCTOR_ROLE, "status": status}):
            reply = str(_s.t_spin_worker(**spin_kwargs))
    except Exception as exc:
        _release_claim(worker_id)
        return {"queued": False, "reason": f"spin_worker failed: {exc}", "worker_id": worker_id}

    match = re.search(r"worker_[0-9a-f]{6,}", reply or "")
    if not match:
        _release_claim(worker_id)
        return {"queued": False, "reason": f"spin_worker refused: {(reply or '').strip()[:200]}",
                "worker_id": worker_id}
    doctor_id = match.group(0)

    doctor_meta = {}
    with contextlib.suppress(Exception):
        doctor_meta = _s._load_worker(doctor_id) or {}
    if doctor_meta:
        _mark_doctor(doctor_meta)
    try:
        with _s._state_lock("workers"):
            cur = _s._load_worker(worker_id) or meta
            cur["doctor_queued"] = True
            cur["doctor_id"] = doctor_id
            cur["doctor_budget_used"] = budget
            _s._save_worker(cur)
    except Exception:
        pass
    return {"queued": True, "doctor_id": doctor_id, "worker_id": worker_id,
            "budget": budget, "provider": provider, "model": model}


def _release_claim(worker_id: str) -> None:
    """Undo the claim when no doctor was queued, so a later attempt can try again."""
    try:
        with _s._state_lock("workers"):
            cur = _s._load_worker(worker_id)
            if cur and cur.get("doctor_queued") and not cur.get("doctor_id"):
                cur.pop("doctor_queued", None)
                _s._save_worker(cur)
    except Exception:
        pass


def maybe_queue_failure_doctor(worker_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Entry point: a worker just ended. Queue its doctor when the setting says so."""
    try:
        meta = _s._load_worker(worker_id)
    except Exception:
        meta = None
    if not meta:
        return {"queued": False, "reason": "no worker record", "worker_id": worker_id}
    try:
        return queue_failure_doctor(worker_id, meta, result or {})
    except Exception as exc:
        return {"queued": False, "reason": f"doctor failed: {exc}", "worker_id": worker_id}


# --- settings --------------------------------------------------------------
SETTING_KEYS = (DOCTOR_MODE_KEY, DOCTOR_BUDGET_MODE_KEY,
                DOCTOR_BUDGET_TOKENS_KEY, DOCTOR_BUDGET_CEILING_KEY, DOCTOR_MODEL_KEY)


def validate_setting(key: str, value: Any) -> Tuple[bool, Any, str]:
    """Validate one doctor setting. Returns (ok, coerced value, error message)."""
    if key == DOCTOR_MODE_KEY:
        val = str(value or "").strip().lower()
        if val not in DOCTOR_MODES:
            return False, None, f"Invalid doctor_mode: {value}. Must be one of: {', '.join(DOCTOR_MODES)}."
        return True, val, ""
    if key == DOCTOR_BUDGET_MODE_KEY:
        val = str(value or "").strip().lower()
        if val not in BUDGET_MODES:
            return False, None, f"Invalid doctor_budget_mode: {value}. Must be one of: {', '.join(BUDGET_MODES)}."
        return True, val, ""
    if key in (DOCTOR_BUDGET_TOKENS_KEY, DOCTOR_BUDGET_CEILING_KEY):
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return False, None, f"Invalid {key}: {value}"
        try:
            num = int(value)
        except (TypeError, ValueError):
            return False, None, f"Invalid {key}: {value}"
        low, high = (TOKENS_MIN, TOKENS_MAX) if key == DOCTOR_BUDGET_TOKENS_KEY else (CEILING_MIN, CEILING_MAX)
        if not (low <= num <= high):
            return False, None, f"{key} out of range (must be {low}..{high}): {num}"
        return True, num, ""
    return False, None, f"Unknown doctor setting: {key}"
