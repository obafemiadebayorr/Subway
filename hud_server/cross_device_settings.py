"""Cross-device settings fan-out and remote-downgrade-only clamping.

Pure standard-library helpers for:
1. Filtering remote settings patches so remote callers can only tighten or leave
   host-only security/budget settings unchanged (never weaken them).
2. Fanning out settings patches to ``own`` peers only (never ``partner`` peers).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

HOST_ONLY_KEYS = frozenset({
    "principal_max_reads",
    "read_budget",
    "read_budget_warn_pct",
})

# B5: security-relevant keys a remote station may never weaken, regardless of future
# schema additions. Value-sensitive: tightening values still pass. (A full allowlist
# needs a product call on which settings remote stations may legitimately own.)
_REMOTE_WEAKENING: Dict[str, Any] = {
    "require_approval": False,          # disabling approval gates
    "launch_approval_local": "never",   # launch everything without asking
    "mail.wake_offline": True,          # enabling waking of offline stations
    "worktree_cleanup": "on",           # destructive sweep
    "compaction": "auto",               # consent-gated: never remote-auto
}


def _is_truthy(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("false", "0", "off", "no", ""):
            return False
        if s in ("true", "1", "on", "yes"):
            return True
    return bool(val)


def filter_remote_settings_patch(patch: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Strip host-only or security-weakening keys from a remote settings patch.

    Returns ``(allowed_patch, rejected_keys)``.
    """
    if not isinstance(patch, dict):
        return {}, []
    allowed: Dict[str, Any] = {}
    rejected: List[str] = []
    for key, val in patch.items():
        if key in HOST_ONLY_KEYS:
            rejected.append(key)
            continue
        if key in _REMOTE_WEAKENING:
            want = _REMOTE_WEAKENING[key]
            bad = (want is True and _is_truthy(val)) or (want is False and not _is_truthy(val)) or (
                isinstance(want, str) and str(val or "").strip().lower() == want)
            if bad:
                rejected.append(key)
                continue
        if key == "sandbox.unsandboxed_fallback" and _is_truthy(val):
            rejected.append(key)
            continue
        if key == "god_mode" and _is_truthy(val):
            rejected.append(key)
            continue
        if key == "principal_guard_enabled" and not _is_truthy(val):
            rejected.append(key)
            continue
        if key == "sandbox.exec_default":
            norm = str(val or "").strip().lower()
            if norm in ("off", "worktree"):
                rejected.append(key)
                continue
        if key == "sandbox.net_default":
            norm = str(val or "").strip().lower()
            if norm == "open":
                rejected.append(key)
                continue
        allowed[key] = val
    return allowed, rejected


def _peer_relation(peer: Dict[str, Any]) -> str:
    return str(peer.get("relation") or "partner").strip().lower()


def _is_offline_reason(reason: str) -> bool:
    low = str(reason or "").strip().lower()
    return any(tok in low for tok in ("offline", "unreachable", "timed out", "timeout"))


def fanout_settings(
    patch: Dict[str, Any],
    target: str,
    peers_list: List[Dict[str, Any]],
    *,
    forward_fn: Callable[[Dict[str, Any], Dict[str, Any]], Tuple[bool, str]],
) -> Dict[str, Any]:
    """Fan out a settings patch to ``self``, ``all`` own peers, or one ``<peer_id>``.

    Only peers with ``relation == "own"`` are ever targeted.
    """
    t = str(target or "").strip()
    if not t or t == "self":
        return {"target": "self", "applied": ["self"], "denied": [], "offline": []}

    raw_patch = {k: v for k, v in (patch or {}).items() if k != "target"} if isinstance(patch, dict) else {}
    safe_patch, _ = filter_remote_settings_patch(raw_patch)

    applied: List[str] = []
    denied: List[Dict[str, str]] = []
    offline: List[str] = []

    valid_peers = [p for p in (peers_list or []) if isinstance(p, dict)]

    if t == "all":
        for peer in valid_peers:
            if _peer_relation(peer) != "own":
                continue
            pid = str(peer.get("peer_id") or "")
            if not pid:
                continue
            ok, reason = forward_fn(peer, safe_patch)
            if ok:
                applied.append(pid)
            elif _is_offline_reason(reason):
                offline.append(pid)
            else:
                denied.append({"peer_id": pid, "reason": str(reason or "denied")})
        return {"target": "all", "applied": applied, "denied": denied, "offline": offline}

    match = next(
        (p for p in valid_peers if str(p.get("peer_id") or "") == t and _peer_relation(p) == "own"),
        None,
    )
    if match is None:
        denied.append({"peer_id": t, "reason": "only own stations can receive settings"})
        return {"target": t, "applied": applied, "denied": denied, "offline": offline}

    ok, reason = forward_fn(match, safe_patch)
    if ok:
        applied.append(t)
    elif _is_offline_reason(reason):
        offline.append(t)
    else:
        denied.append({"peer_id": t, "reason": str(reason or "denied")})
    return {"target": t, "applied": applied, "denied": denied, "offline": offline}
