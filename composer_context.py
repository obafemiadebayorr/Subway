"""Composer context endpoints: project_files, context_slice, and thread_export.

Implements Subway Composer §3.1b endpoints.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

import thread_state
import threadlog

_DEFAULT_EXCLUSIONS = {".git", "node_modules", "__pycache__", ".venv", ".worktrees"}


def _load_gitignore_patterns(root: str) -> List[str]:
    """Load gitignore patterns from root/.gitignore if present."""
    patterns = []
    gitignore_path = os.path.join(root, ".gitignore")
    if os.path.isfile(gitignore_path):
        try:
            with open(gitignore_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        patterns.append(stripped)
        except OSError:
            pass
    return patterns


def _matches_gitignore(rel_path: str, patterns: List[str]) -> bool:
    """Check if a relative path matches any gitignore pattern."""
    norm = rel_path.replace(os.sep, "/")
    parts = norm.split("/")
    for pat in patterns:
        pat_clean = pat.rstrip("/")
        if fnmatch.fnmatch(norm, pat_clean) or fnmatch.fnmatch(norm, f"*/{pat_clean}"):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, pat_clean):
                return True
    return False


def project_files(q: str = "", project_dir: str = "", limit: int = 50) -> Dict[str, Any]:
    """Fuzzy searches repository files for the #path autocomplete picker, annotating active lock status.

    - Resolves root = os.path.realpath(project_dir or os.getcwd()).
    - Rejects path traversal outside root with ValueError (maps to 400).
    - Respects .gitignore and project exclusions (.git, node_modules, __pycache__, .venv, .worktrees).
    - Uses git ls-files if in a git worktree, falling back to os.walk.
    - Annotates active lock status from locks.json.
    - Filters by substring/fuzzy query q and caps results at limit (1 to 200).
    """
    root = os.path.realpath(project_dir or os.environ.get("SUBWAY_PROJECT_DIR") or os.getcwd())
    if root in (os.path.realpath(os.path.expanduser("~")), "/"):
        return {"ok": False, "files": [], "error": "No project folder: start the HUD from a project chat"}
    if not os.path.exists(root) or not os.path.isdir(root):
        raise ValueError(f"Project directory does not exist or is not a directory: {project_dir}")

    # Validate that q does not attempt path traversal outside root
    if q:
        q_strip = q.strip().lstrip("#")
        if os.path.isabs(q_strip):
            real_q = os.path.realpath(q_strip)
            if os.path.commonpath([root, real_q]) != root:
                raise ValueError("Path traversal outside project root")
        else:
            # Check for parent dir traversal in path segments
            parts = q_strip.replace("\\", "/").split("/")
            if ".." in parts:
                cand_q = os.path.realpath(os.path.join(root, q_strip))
                if os.path.commonpath([root, cand_q]) != root:
                    raise ValueError("Path traversal outside project root")

    try:
        limit = min(200, max(1, int(limit)))
    except (TypeError, ValueError):
        limit = 50

    # Collect candidate relative file paths
    candidates: List[str] = []
    is_git = False
    try:
        res = subprocess.run(
            ["git", "-C", root, "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=10,
        )
        if res.returncode == 0:
            is_git = True
            for line in res.stdout.splitlines():
                line = line.strip()
                if line:
                    candidates.append(line)
    except (OSError, subprocess.SubprocessError):
        is_git = False

    if not is_git:
        gitignore_patterns = _load_gitignore_patterns(root)
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune excluded directories
            dirnames[:] = [
                d for d in dirnames
                if d not in _DEFAULT_EXCLUSIONS and not _matches_gitignore(
                    os.path.relpath(os.path.join(dirpath, d), root), gitignore_patterns
                )
            ]
            for fname in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fname), root)
                if not _matches_gitignore(rel, gitignore_patterns):
                    candidates.append(rel)

    # Validate every candidate with realpath + commonpath and check exclusions
    valid_candidates: List[str] = []
    for rel in candidates:
        rel_norm = rel.replace("\\", "/")
        parts = rel_norm.split("/")
        if any(p in _DEFAULT_EXCLUSIONS for p in parts):
            continue
        real_cand = os.path.realpath(os.path.join(root, rel))
        try:
            if os.path.commonpath([root, real_cand]) != root:
                continue
        except ValueError:
            continue
        if not os.path.isfile(real_cand):
            continue
        valid_candidates.append(rel_norm)

    # Filter by q if provided
    query = (q or "").strip().lstrip("#").lower()
    if query:
        filtered = [f for f in valid_candidates if query in f.lower()]
    else:
        filtered = valid_candidates

    filtered.sort()
    capped = filtered[:limit]

    # Load lock annotations
    now = time.time()
    try:
        import server as _s
        locks_data = _s.load_locks()
        active_locks = locks_data.get("locks", {})
    except Exception:
        active_locks = {}

    files_result = []
    for rel_path in capped:
        abs_cand = os.path.join(root, rel_path)
        try:
            size_bytes = os.path.getsize(abs_cand)
        except OSError:
            size_bytes = 0

        lines_count = 0
        try:
            with open(abs_cand, "rb") as f:
                lines_count = sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(65536), b""))
        except OSError:
            lines_count = 0

        # Match active lock
        lock_info = active_locks.get(rel_path) or active_locks.get(os.path.normpath(rel_path))
        locked = False
        locked_by = None
        lease_expires_in = 0
        if lock_info:
            expires_at = float(lock_info.get("expires_at") or 0)
            if expires_at > now:
                locked = True
                locked_by = lock_info.get("holder")
                lease_expires_in = max(0, int(expires_at - now))

        files_result.append({
            "path": rel_path,
            "lines": lines_count,
            "size_bytes": size_bytes,
            "locked": locked,
            "locked_by": locked_by,
            "lease_expires_in": lease_expires_in,
        })

    return {"ok": True, "files": files_result}


def context_slice(
    spec: str = "",
    path: str = "",
    start_line: int = 0,
    end_line: int = 0,
    project_dir: str = "",
    item: str = "",
) -> Dict[str, Any]:
    """Extracts exact code line ranges from workspace files using _s._extract_code_item.

    Validates path containment inside os.path.realpath(project_dir or os.getcwd()).
    """
    root = os.path.realpath(project_dir or os.environ.get("SUBWAY_PROJECT_DIR") or os.getcwd())
    if not os.path.exists(root) or not os.path.isdir(root):
        raise ValueError(f"Project directory does not exist or is not a directory: {project_dir}")

    # Resolve target spec/path
    raw_spec = (spec or item or "").strip().lstrip("#")
    raw_path = (path or "").strip().lstrip("#")

    if raw_spec:
        if ":" in raw_spec:
            file_part, line_part = raw_spec.rsplit(":", 1)
        else:
            file_part, line_part = raw_spec, ""
    elif raw_path:
        file_part = raw_path
        if start_line > 0 and end_line >= start_line:
            line_part = f"{start_line}-{end_line}"
        elif start_line > 0:
            line_part = f"{start_line}-{start_line}"
        else:
            line_part = ""
    else:
        raise ValueError("Missing file path or slice specification")

    if not file_part:
        raise ValueError("Invalid line range or path")

    real_cand = os.path.realpath(os.path.join(root, file_part))
    try:
        if os.path.commonpath([root, real_cand]) != root:
            # 422 Unprocessable boundary error
            raise ValueError("Path exceeds safe workspace boundary")
    except ValueError as e:
        if "Path exceeds" in str(e):
            raise
        raise ValueError("Path exceeds safe workspace boundary")

    if not os.path.exists(real_cand):
        raise FileNotFoundError(f"File does not exist: {file_part}")
    if not os.path.isfile(real_cand):
        raise ValueError(f"Target is not a file: {file_part}")

    if line_part:
        item_to_extract = f"{file_part}:{line_part}"
    else:
        # Full file slice
        try:
            with open(real_cand, "rb") as f:
                total_file_lines = max(1, sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(65536), b"")))
        except OSError:
            total_file_lines = 1
        item_to_extract = f"{file_part}:1-{total_file_lines}"

    import server as _s
    resolved = _s._extract_code_item(item_to_extract, root)
    if not resolved:
        raise ValueError("Invalid line range or path")

    rel_path, start_l, end_l, content, abs_path = resolved
    total_lines = max(1, end_l - start_l + 1)
    # Estimate tokens: standard ~3.7 chars per token heuristic
    token_est = max(1, math.ceil(len(content) / 3.7))
    sha256_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    return {
        "ok": True,
        "path": rel_path.replace(os.sep, "/"),
        "start_line": start_l,
        "end_line": end_l,
        "total_lines": total_lines,
        "lines": total_lines,
        "content": content,
        "tokens": token_est,
        "token_estimate": token_est,
        "sha": sha256_hash,
        "sha256": sha256_hash,
        "result": content,
    }


def _thread_workers(thread: str) -> List[Dict[str, Any]]:
    """Retrieve all workers associated with a given thread root."""
    import server as _s
    all_workers = _s._all_workers()
    matching: List[Dict[str, Any]] = []
    for w in all_workers:
        root = str(w.get("thread_root") or w.get("thread") or w.get("launched_by") or "")
        wid = str(w.get("id") or "")
        pid = str(w.get("parent_id") or "")
        if root == thread or wid == thread or pid == thread:
            matching.append(w)
    matching.sort(key=lambda x: str(x.get("created_at") or ""))
    return matching


def _read_worker_patch(wid: str) -> str:
    """Read .patch or .review_diff file for a worker in _s.WORKERS_DIR."""
    import server as _s
    patch_file = os.path.join(_s.WORKERS_DIR, f"{wid}.patch")
    if os.path.isfile(patch_file):
        try:
            with open(patch_file, "r", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError:
            pass
    review_diff_file = os.path.join(_s.WORKERS_DIR, f"{wid}.review_diff")
    if os.path.isfile(review_diff_file):
        try:
            with open(review_diff_file, "r", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError:
            pass
    return ""


def thread_export(thread: str, fmt: str = "md") -> Dict[str, Any]:
    """Exports a clean digest of thread history, decisions, and patches.

    Bundles:
    - Thread ledger (threadlog.thread_log_read(thread))
    - Worker patches (from workers in _s.WORKERS_DIR matching the thread)
    - Usage and cost breakdown (sum of tokens and USD across those workers)
    - Principal history (from thread_state.get_thread_settings(thread))

    Supports format='md', 'json', and 'patch'.
    """
    if not thread:
        raise ValueError("Thread ID is required")

    # 1. Thread ledger
    ledger: List[Dict[str, Any]] = threadlog.thread_log_read(thread)

    # 2. Workers and patches
    workers = _thread_workers(thread)
    worker_summaries: List[Dict[str, Any]] = []
    patches_map: Dict[str, str] = {}
    total_tokens = 0
    total_cost_usd = 0.0
    has_cost = False

    import server as _s
    prices = _s._load_json("config.json").get("model_prices") or {}

    for w in workers:
        wid = str(w.get("id") or "")
        usage = w.get("usage") or {}
        w_tokens = w.get("tokens") or (usage.get("input", 0) + usage.get("output", 0))
        total_tokens += int(w_tokens or 0)

        cost = w.get("cost_usd")
        if isinstance(cost, (int, float)):
            total_cost_usd += float(cost)
            has_cost = True
        else:
            model = str(w.get("model") or w.get("model_used") or "")
            price = prices.get(model)
            if isinstance(price, dict) and isinstance(usage, dict):
                inp = float(usage.get("input") or 0)
                out = float(usage.get("output") or 0)
                total_cost_usd += (inp * float(price.get("input") or 0) + out * float(price.get("output") or 0)) / 1_000_000.0
                has_cost = True

        patch_text = _read_worker_patch(wid)
        if patch_text:
            patches_map[wid] = patch_text

        worker_summaries.append({
            "id": wid,
            "role": w.get("role") or "",
            "provider": w.get("provider") or "",
            "model": w.get("model") or "",
            "status": w.get("status") or "",
            "tokens": w_tokens,
            "verdict": w.get("verdict"),
            "has_patch": bool(patch_text),
            "created_at": w.get("created_at") or "",
        })

    # 3. Principal history and thread settings
    principal_info: Dict[str, Any] = thread_state.get_thread_settings(thread)

    format_norm = (fmt or "md").lower().strip()
    if format_norm not in ("md", "json", "patch"):
        format_norm = "md"

    cost_val = round(total_cost_usd, 4) if has_cost else None

    # Format outputs
    if format_norm == "json":
        data = {
            "ok": True,
            "thread": thread,
            "format": "json",
            "principal": principal_info,
            "usage": {
                "total_tokens": total_tokens,
                "total_cost_usd": cost_val,
            },
            "workers": worker_summaries,
            "ledger": ledger,
            "patches": patches_map,
        }
        content = json.dumps(data, indent=2)
        mime_type = "application/json"
    elif format_norm == "patch":
        patch_pieces = []
        for wid, ptext in patches_map.items():
            patch_pieces.append(f"# Worker: {wid}\n{ptext}")
        content = "\n\n".join(patch_pieces) if patch_pieces else "# No worker patches recorded."
        mime_type = "text/x-diff"
        data = {
            "ok": True,
            "thread": thread,
            "format": "patch",
            "content": content,
            "mime_type": mime_type,
            "patches": patches_map,
        }
    else:  # md
        md_lines = [
            f"# Thread Export: {thread}",
            "",
            "## Summary",
            f"- **Principal**: {principal_info.get('principal_id') or 'none'}",
            f"- **Total Tokens**: {total_tokens:,}",
            f"- **Total Cost (USD)**: {f'${cost_val:.4f}' if cost_val is not None else 'N/A'}",
            f"- **Workers Count**: {len(workers)}",
            "",
            "## Workers",
            "| Worker ID | Role | Provider | Model | Status | Tokens | Verdict |",
            "|---|---|---|---|---|---|---|",
        ]
        if worker_summaries:
            for ws in worker_summaries:
                md_lines.append(
                    f"| {ws['id']} | {ws['role']} | {ws['provider']} | {ws['model']} | {ws['status']} | {ws['tokens']} | {ws['verdict'] or '-'} |"
                )
        else:
            md_lines.append("| (none) | - | - | - | - | - | - |")

        md_lines.extend(["", "## Ledger Timeline"])
        if ledger:
            for rec in ledger[-50:]:  # Last 50 events
                kind = rec.get("kind") or "event"
                node = rec.get("node") or ""
                peer = rec.get("peer") or ""
                at = rec.get("at") or ""
                peer_str = f" -> {peer}" if peer else ""
                md_lines.append(f"- `[{at}]` **{kind}**: `{node}`{peer_str}")
        else:
            md_lines.append("- *(No ledger events)*")

        md_lines.extend(["", "## Patches"])
        if patches_map:
            for wid, ptext in patches_map.items():
                md_lines.extend([
                    f"### Worker `{wid}`",
                    "```diff",
                    ptext,
                    "```",
                    "",
                ])
        else:
            md_lines.append("- *(No patches recorded)*")

        content = "\n".join(md_lines)
        mime_type = "text/markdown"
        data = {
            "ok": True,
            "thread": thread,
            "format": "md",
            "content": content,
            "mime_type": mime_type,
            "principal": principal_info,
            "usage": {
                "total_tokens": total_tokens,
                "total_cost_usd": cost_val,
            },
            "workers": worker_summaries,
            "ledger": ledger,
            "patches": patches_map,
        }

    return data


def t_project_files(q: str = "", project_dir: str = "", limit: int = 50) -> str:
    return json.dumps(project_files(q=q, project_dir=project_dir, limit=limit), indent=2, default=str)


def t_context_slice(
    spec: str = "",
    path: str = "",
    start_line: int = 0,
    end_line: int = 0,
    project_dir: str = "",
    item: str = "",
) -> str:
    return json.dumps(
        context_slice(
            spec=spec,
            path=path,
            start_line=start_line,
            end_line=end_line,
            project_dir=project_dir,
            item=item,
        ),
        indent=2,
        default=str,
    )


def t_thread_export(thread: str, fmt: str = "md") -> str:
    return json.dumps(thread_export(thread=thread, fmt=fmt), indent=2, default=str)

