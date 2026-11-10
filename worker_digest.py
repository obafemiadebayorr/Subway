"""Worker failure digest: aggregates repeat failures from past workers."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

try:
    from workers.outcomes import _all_workers
except ImportError:
    try:
        from workers import _all_workers
    except ImportError:
        _all_workers = None

DIGEST_CACHE_FILE = os.path.expanduser("~/.subway-context/worker_digest.json")
CACHE_TTL_SECONDS = 600

ADVICE = {
    "stalled": "give exact code and an edit by call 3",
    "quota": "check quota_status first",
    "permission": "pre-approve or run directly",
    "bad_path": "verify paths in the brief",
    "syntax": "verify with node --input-type=module --check / py_compile",
}

STALLED_RE = re.compile(r"budget_stopped|stalled|no edit", re.IGNORECASE)
QUOTA_RE = re.compile(r"quota|rate.?limit|429|exhausted", re.IGNORECASE)
PERM_RE = re.compile(r"permission denied|denied|not allowed|blocked by guard", re.IGNORECASE)
BAD_PATH_RE = re.compile(r"No such file|ENOENT|could not resolve|not found", re.IGNORECASE)
SYNTAX_RE = re.compile(r"SyntaxError|IndentationError", re.IGNORECASE)

FILE_RE = re.compile(r"[\w./-]+\.(?:py|js|mjs|css|md|json|html)")


def _get_cache_path() -> str:
    default_path = os.path.expanduser("~/.subway-context/worker_digest.json")
    if DIGEST_CACHE_FILE != default_path:
        return DIGEST_CACHE_FILE
    subway_dir = os.environ.get("SUBWAY_DIR")
    if subway_dir:
        return os.path.join(subway_dir, "worker_digest.json")
    return DIGEST_CACHE_FILE


def _load_workers() -> List[Dict[str, Any]]:
    if _all_workers is not None and callable(_all_workers):
        try:
            return list(_all_workers(include_archive=True))
        except TypeError:
            return list(_all_workers())
        except Exception:
            return []
    return []


def _classify_record(rec: Dict[str, Any]) -> Optional[str]:
    try:
        status_note = f"{rec.get('status') or ''} {rec.get('note') or ''} {rec.get('reason') or ''}"

        log_path = rec.get("log_file") or rec.get("log_path") or rec.get("log") or ""
        log_tail = ""
        if log_path and os.path.isfile(str(log_path)):
            try:
                with open(str(log_path), "rb") as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    f.seek(max(0, size - 4096), os.SEEK_SET)
                    log_tail = f.read().decode("utf-8", errors="replace")
            except Exception:
                log_tail = ""

        # First match wins
        # stalled = status/note mentions "budget_stopped", "stalled", "no edit"
        if STALLED_RE.search(status_note):
            return "stalled"

        combined = f"{log_tail}\n{status_note}"
        if QUOTA_RE.search(combined):
            return "quota"
        if PERM_RE.search(combined):
            return "permission"
        if BAD_PATH_RE.search(combined):
            return "bad_path"
        if SYNTAX_RE.search(combined):
            return "syntax"
    except Exception:
        pass
    return None


def digest(limit: int = 300) -> List[Dict[str, Any]]:
    cache_path = _get_cache_path()
    now = time.time()
    try:
        if os.path.isfile(cache_path):
            mtime = os.path.getmtime(cache_path)
            if (now - mtime) < CACHE_TTL_SECONDS:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                    if isinstance(cached_data, list):
                        return cached_data
    except Exception:
        pass

    try:
        loader = globals().get("_load_workers")
        if callable(loader):
            records = list(loader())
        else:
            records = []
    except Exception:
        records = []

    # Finished records (not running/pending, not old w_*.json)
    finished: List[Dict[str, Any]] = []
    for r in records:
        try:
            if not isinstance(r, dict):
                continue
            wid = str(r.get("id") or r.get("worker_id") or "")
            if wid.startswith("w_"):
                continue
            status = str(r.get("status") or "").lower()
            if status in ("running", "pending", ""):
                continue
            finished.append(r)
        except Exception:
            continue

    # Chronologically sort to take the last `limit`
    try:
        finished.sort(key=lambda x: str(x.get("created_at") or ""))
    except Exception:
        pass

    if limit > 0 and len(finished) > limit:
        finished = finished[-limit:]

    groups: Dict[tuple, Dict[str, Any]] = {}
    for rec in finished:
        try:
            pattern = _classify_record(rec)
            if not pattern:
                continue

            provider = str(rec.get("provider") or "unknown")
            model = str(rec.get("model") or "unknown")
            prompt = str(rec.get("prompt") or "")

            files: List[str] = []
            for m in FILE_RE.finditer(prompt):
                clean_path = m.group(0).lstrip("./")
                if clean_path and clean_path not in files:
                    files.append(clean_path)
                if len(files) == 3:
                    break

            wid = str(rec.get("id") or rec.get("worker_id") or "")
            key = (pattern, provider, model)
            if key not in groups:
                groups[key] = {
                    "pattern": pattern,
                    "provider": provider,
                    "model": model,
                    "count": 0,
                    "files": files,
                    "example_id": wid,
                }
            g = groups[key]
            g["count"] += 1
            if wid:
                g["example_id"] = wid
            for f in files:
                if f not in g["files"] and len(g["files"]) < 3:
                    g["files"].append(f)
        except Exception:
            continue

    items: List[Dict[str, Any]] = []
    for g in groups.values():
        if g["count"] < 2:
            continue
        pattern = g["pattern"]
        advice = ADVICE.get(pattern, "")
        files = g["files"]
        on_clause = f" on {', '.join(files)}" if files else ""
        line = f"{g['provider']}/{g['model']} {pattern} ×{g['count']}{on_clause} — {advice}"
        items.append({
            "pattern": g["pattern"],
            "provider": g["provider"],
            "model": g["model"],
            "count": g["count"],
            "files": g["files"],
            "example_id": g["example_id"],
            "line": line,
        })

    items.sort(key=lambda x: x["count"], reverse=True)
    items = items[:8]

    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp_cache = f"{cache_path}.tmp.{os.getpid()}"
        with open(tmp_cache, "w", encoding="utf-8") as f:
            json.dump(items, f)
        os.replace(tmp_cache, cache_path)
    except Exception:
        pass

    return items


def lines_for(files: List[str], provider: str, max_lines: int = 2, model: str = "") -> List[str]:
    try:
        items = digest()
    except Exception:
        return []

    clean_files = set(f.lstrip("./") for f in files if f)
    base_files = set(os.path.basename(f.lstrip("./")) for f in files if f and os.path.basename(f.lstrip("./")))

    matched: List[str] = []
    for it in items:
        it_files = it.get("files") or []
        f_overlap = False
        for f in it_files:
            cf = f.lstrip("./")
            if not cf:
                continue
            if cf in clean_files or os.path.basename(cf) in base_files:
                f_overlap = True
                break
        if f_overlap:
            line = it.get("line")
            if line and line not in matched:
                matched.append(line)
            if len(matched) >= max_lines:
                break

    if not matched and provider and model:
        candidates = [
            it for it in items
            if (it.get("provider") == provider or str(it.get("provider", "")).lower() == provider.lower())
            and (it.get("model") == model or str(it.get("model", "")).lower() == model.lower())
            and int(it.get("count") or 0) >= 10
        ]
        if candidates:
            best = max(candidates, key=lambda x: int(x.get("count") or 0))
            line = best.get("line")
            if line:
                matched.append(line)

    return matched[:max_lines]


def text(items: List[Dict[str, Any]]) -> str:
    try:
        lines = [it.get("line", "") for it in items if it.get("line")]
        return "\n".join(lines)
    except Exception:
        return ""
