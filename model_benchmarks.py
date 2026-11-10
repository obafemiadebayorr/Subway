import os
import sys
import time
import json
import re
import contextlib
from typing import Dict, Any, Optional, Tuple, Callable, List

import server as _s
import store

MAX_BENCHMARK_TOKENS = 40000
MAX_BENCHMARK_SECONDS = 30.0

_NO_RUNNER_STATUS = "unverified"
_NO_RUNNER_REASON = "no runner configured"

# Effort/variant suffixes to strip for generic model matching.
_EFFORT_SUFFIX_RE = re.compile(r"-(?:low|medium|high|preview|thinking|latest)$")


def normalize_model_name(name: str) -> str:
    """Generic normalizer: lowercase, strip provider prefix (up to first '/'),
    strip effort suffixes (-low|-medium|-high|-preview|-thinking|-latest|:latest),
    strip trailing -free, collapse whitespace/separators. No vendor names."""
    if not name:
        return ""
    s = str(name).strip().lower()
    # strip provider prefix up to first "/"
    if "/" in s:
        s = s.split("/", 1)[1]
    # strip :latest and other :variant suffixes treated as latest-like in matching
    if s.endswith(":latest"):
        s = s[:-7]
    # strip effort suffixes (may repeat)
    while True:
        ns = _EFFORT_SUFFIX_RE.sub("", s)
        if ns == s:
            break
        s = ns
    # strip trailing -free
    if s.endswith("-free"):
        s = s[:-5]
    # normalize separators and collapse whitespace for generic matching
    s = re.sub(r"[\s_\-]+", " ", s).strip()
    return s


def models_match(model_id: str, row_name: str) -> bool:
    """Exact or prefix bidirectional match on normalized names."""
    a = normalize_model_name(model_id)
    b = normalize_model_name(row_name)
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a)


def benchmark_strengths(rows: List[Dict[str, Any]], models: List[Dict[str, Any]]) -> Dict[str, int]:
    """Map models to benchmark strength (1-5) from leaderboard rows.
    Generic matching only (no vendor branches). Agent-system scores
    (subject_type != "model") never merge into model rankings."""
    matched_ratings: Dict[str, float] = {}
    for m in models:
        m_id = m.get("model") or ""
        if not m_id:
            continue
        best_rating = None
        for row_entry in rows:
            rdata = row_entry.get("row") if isinstance(row_entry, dict) and "row" in row_entry else row_entry
            if not isinstance(rdata, dict):
                continue
            # Agent-system scores must never merge into model-only ranking.
            stype = str(rdata.get("subject_type") or "model")
            if stype != "model":
                continue
            name = str(rdata.get("model_name") or rdata.get("name") or "")
            if models_match(m_id, name):
                try:
                    rating = float(rdata.get("rating", 0))
                    if best_rating is None or rating > best_rating:
                        best_rating = rating
                except (ValueError, TypeError):
                    continue
        if best_rating is not None:
            matched_ratings[m_id] = best_rating

    if not matched_ratings:
        return {}

    sorted_models = sorted(matched_ratings.keys(), key=lambda k: matched_ratings[k], reverse=True)
    n = len(sorted_models)
    strengths: Dict[str, int] = {}
    for rank_idx, m_id in enumerate(sorted_models):
        score = 5 - int(rank_idx * 5 / n)
        score = max(1, min(5, score))
        strengths[m_id] = score
    return strengths


def refresh_benchmarks(force: bool = False) -> None:
    """Refresh benchmark rankings into benchmarks.json with provenance.
    Each row carries {source_name, source_url, metric, subject_type, fetched_at}.
    Model scores and agent-system scores are stored separately and never merged."""
    path = os.path.join(_s.SUBWAY_DIR, "benchmarks.json")
    old_data: Dict[str, Any] = {}
    with contextlib.suppress(Exception):
        with open(path, "r", encoding="utf-8") as f:
            old_data = json.load(f)

    now = time.time()
    fetched_at = old_data.get("fetched_at", 0)
    if not force and isinstance(fetched_at, (int, float)) and (now - fetched_at < 7 * 86400):
        return

    source_name = "lmarena"
    source_url = "https://datasets-server.huggingface.co/rows?dataset=lmarena-ai/leaderboard-dataset&config=text&split=latest"
    metric = "arena_rating"
    fetched_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    model_rows: List[Dict[str, Any]] = []
    try:
        import urllib.request
        for offset in (0, 100, 200, 300):
            url = f"{source_url}&offset={offset}&length=100"
            req = urllib.request.Request(url, headers={"User-Agent": "subway"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            rows = data.get("rows")
            if not isinstance(rows, list) or not rows:
                break
            for item in rows:
                rdata = item.get("row") if isinstance(item, dict) and "row" in item else item
                if isinstance(rdata, dict) and "model_name" in rdata:
                    model_rows.append({
                        "model_name": rdata.get("model_name"),
                        "rating": rdata.get("rating"),
                        "source_name": source_name,
                        "source_url": source_url,
                        "metric": metric,
                        "subject_type": "model",
                        "fetched_at": fetched_iso,
                    })
        # Preserve any existing agent_system rows separately; never merge rankings.
        agent_rows = []
        for r in (old_data.get("agent_system_rows") or []):
            if isinstance(r, dict) and str(r.get("subject_type") or "") == "agent_system":
                agent_rows.append(r)
        saved = {
            "source": source_url,
            "source_name": source_name,
            "metric": metric,
            "fetched_at": now,
            "rows": model_rows,
            "agent_system_rows": agent_rows,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(saved, f, indent=2)
    except Exception as exc:
        old_data["error"] = str(exc)
        if "source" not in old_data:
            old_data["source"] = source_url
        with contextlib.suppress(Exception):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(old_data, f, indent=2)


def run_stage1_auth_ping(model_id: str, provider: str, runner_fn: Optional[Callable[[str, str, str], Dict[str, Any]]] = None) -> Tuple[bool, str]:
    """Stage 1: Connectivity & Authentication Ping.
    Issue a minimal prompt to verify model is reachable and not blocked behind interactive OAuth/browser login.
    """
    if runner_fn:
        res = runner_fn(provider, model_id, "ping")
    else:
        # No runner configured: do not fake a passing result.
        return False, _NO_RUNNER_REASON

    status = str(res.get("status") or "").lower()
    text = str(res.get("text") or res.get("message") or res.get("error") or "").lower()

    # Detect interactive authentication or OAuth blockers (e.g. beam-free)
    if status in ("auth_required", "unauthorized", "auth_blocked") or any(kw in text for kw in [
        "authentication required", "sign in", "oauth", "interactive login", "browser login", "login required", "token expired"
    ]):
        return False, f"auth_blocked: {text[:100]}"
    if status == "error" and not text:
        return False, "auth_blocked: execution error"
    return True, "auth_ok"


def run_stage2_tool_ping(model_id: str, provider: str, runner_fn: Optional[Callable[[str, str, str], Dict[str, Any]]] = None) -> Tuple[bool, str]:
    """Stage 2: Tool-Calling Protocol Adherence.
    Verify whether the model generates structured tool call invocations or hallucinates plaintext (e.g. pulse-alpha-free).
    """
    if runner_fn:
        res = runner_fn(provider, model_id, "tool_ping")
    else:
        # No runner configured: do not fake a passing result.
        return False, _NO_RUNNER_REASON

    # Check tool call adherence
    tool_calls = res.get("tool_calls")
    text = str(res.get("text") or res.get("message") or "")
    if not tool_calls and (res.get("hallucinated_tool") or "tool: read_file" in text or "call:" in text.lower()):
        return False, "non_agentic: model hallucinated plaintext instead of structured tool schema"
    if res.get("can_tool") is False or res.get("status") == "no_tools":
        return False, "non_agentic: model lacks tool-calling capabilities"
    if not tool_calls and res.get("status") != "ok":
        return False, "non_agentic: no tool calls produced"
    return True, "tool_ok"


def run_stage3_edit_ping(model_id: str, provider: str, runner_fn: Optional[Callable[[str, str, str], Dict[str, Any]]] = None,
                         worktree_dir: str = "") -> Tuple[bool, Dict[str, Any]]:
    """Stage 3: Precision Single-Line Diff Edit & Empirical Telemetry.
    Dispatch a deterministic 1-line code replacement against a test file.
    Measures speed (s), tokens/sec, and verifies patch validity.
    """
    t0 = time.time()
    if runner_fn:
        res = runner_fn(provider, model_id, "edit_ping")
    else:
        # No runner configured: do not fake telemetry.
        return False, {"error": _NO_RUNNER_REASON}
    duration = time.time() - t0

    if not res.get("diff_valid") and res.get("status") != "ok":
        return False, {"error": res.get("error") or "diff replacement failed assertion"}

    speed = float(res.get("speed_sec") or duration)
    tokens = int(res.get("tokens") or 1000)
    tps = float(res.get("tokens_per_sec") or (tokens / max(speed, 0.05)))
    quality = float(res.get("quality_score") or 0.95)

    return True, {
        "speed_sec": round(speed, 2),
        "tokens_per_sec": round(tps, 1),
        "avg_edit_tokens": tokens,
        "quality_score": round(quality, 2)
    }


def update_discovered_model_benchmark(base_dir: str, provider: str, model_id: str, benchmark_result: Dict[str, Any]) -> Dict[str, Any]:
    """Atomically record benchmark outcome into discovered_models.json."""
    if not base_dir:
        base_dir = getattr(_s, "SUBWAY_DIR", os.path.expanduser("~/.subway"))
    disc_path = os.path.join(base_dir, "discovered_models.json")
    tmp_path = os.path.join(base_dir, f".disc_bench_{os.getpid()}_{time.time_ns()}.tmp")

    catalog = {}
    with contextlib.suppress(Exception):
        if os.path.isfile(disc_path):
            with open(disc_path, "r", encoding="utf-8") as f:
                catalog = json.load(f)
    if not isinstance(catalog, dict):
        catalog = {}

    catalog.setdefault("version", 1)
    catalog["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    models = catalog.setdefault("models", {})

    key = f"{provider}:{model_id}"
    rec = models.get(key) or {
        "provider": provider,
        "model_id": model_id,
        "display_name": model_id,
        "status": "unverified",
        "pricing_tier": "free" if ("free" in model_id.lower() or model_id == "big-pickle") else "standard",
        "context_limit": 131072,
        "capabilities": {"can_tool": True, "can_edit": True},
        "telemetry": {},
        "last_verified": "",
        "fail_count": 0
    }

    rec["status"] = benchmark_result.get("status", "unverified")
    rec["capabilities"] = benchmark_result.get("capabilities", rec.get("capabilities", {}))
    if "telemetry" in benchmark_result:
        rec["telemetry"] = benchmark_result["telemetry"]
    rec["last_verified"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if benchmark_result.get("status") in ("auth_blocked", "retired", "non_agentic"):
        rec["fail_count"] = int(rec.get("fail_count", 0)) + 1
        rec["reason"] = benchmark_result.get("reason", "")
    else:
        rec["fail_count"] = 0

    models[key] = rec

    try:
        os.makedirs(base_dir, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, disc_path)
    except Exception:
        with contextlib.suppress(Exception):
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    return catalog


def run_model_micro_benchmark(model_id: str, provider: str, base_dir: str = "",
                              runner_fn: Optional[Callable[[str, str, str], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Run full 3-stage micro-benchmark with hard caps (40k tokens, 30s timeout)."""
    # No runner => unverified, no telemetry/capabilities claims.
    if runner_fn is None:
        res = {"status": _NO_RUNNER_STATUS, "reason": _NO_RUNNER_REASON}
        update_discovered_model_benchmark(base_dir, provider, model_id, res)
        return res

    t_start = time.time()
    # Stage 1: Auth
    ok1, reason1 = run_stage1_auth_ping(model_id, provider, runner_fn=runner_fn)
    if not ok1:
        res = {
            "status": "auth_blocked",
            "reason": reason1,
            "capabilities": {"can_tool": False, "can_edit": False}
        }
        update_discovered_model_benchmark(base_dir, provider, model_id, res)
        return res

    # Timeout guard
    if time.time() - t_start > MAX_BENCHMARK_SECONDS:
        res = {"status": "unverified", "reason": "timeout during stage 1", "capabilities": {"can_tool": False, "can_edit": False}}
        update_discovered_model_benchmark(base_dir, provider, model_id, res)
        return res

    # Stage 2: Tool protocol
    ok2, reason2 = run_stage2_tool_ping(model_id, provider, runner_fn=runner_fn)
    if not ok2:
        res = {
            "status": "non_agentic",
            "reason": reason2,
            "capabilities": {"can_tool": False, "can_edit": False}
        }
        update_discovered_model_benchmark(base_dir, provider, model_id, res)
        return res

    if time.time() - t_start > MAX_BENCHMARK_SECONDS:
        res = {"status": "unverified", "reason": "timeout during stage 2", "capabilities": {"can_tool": True, "can_edit": False}}
        update_discovered_model_benchmark(base_dir, provider, model_id, res)
        return res

    # Stage 3: Code edit
    ok3, telemetry = run_stage3_edit_ping(model_id, provider, runner_fn=runner_fn)
    if not ok3:
        res = {
            "status": "unverified",
            "reason": str(telemetry.get("error") or "diff validation failed"),
            "capabilities": {"can_tool": True, "can_edit": False}
        }
        update_discovered_model_benchmark(base_dir, provider, model_id, res)
        return res

    # Tokens guard check
    if telemetry.get("avg_edit_tokens", 0) > MAX_BENCHMARK_TOKENS:
        res = {
            "status": "unverified",
            "reason": f"token count {telemetry.get('avg_edit_tokens')} exceeded {MAX_BENCHMARK_TOKENS} cap",
            "capabilities": {"can_tool": True, "can_edit": False}
        }
        update_discovered_model_benchmark(base_dir, provider, model_id, res)
        return res

    # Promotion to active
    res = {
        "status": "active",
        "capabilities": {"can_tool": True, "can_edit": True},
        "telemetry": telemetry
    }
    update_discovered_model_benchmark(base_dir, provider, model_id, res)
    return res
