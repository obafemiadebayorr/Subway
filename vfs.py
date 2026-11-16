"""The phone's view of a working tree: a manifest, lazy blobs, and a durable outbox.

The phone never clones a repository. A shallow clone into an iOS sandbox is what breaks:
`isomorphic-git` is single-threaded JS/Wasm, it struggles with packfiles past ~50 MB, and iOS
suspending the app mid-checkout leaves a half-written index. So the Mac stays the source of
truth and the phone keeps three things instead:

    manifest   a small index of the tree: path, sha256, size, top-level symbols
    blob       file text for one sha, fetched only when opened or read
    outbox     edits and deferred tool calls, applied once, in order, idempotently

`POST /api/outbox/sync` is the only writer. It is idempotent by key: replaying the same batch
after a dead zone returns the first run's results instead of acting twice. Conflicts (a stale
`base_sha`, or a file another station has claimed) are reported, never resolved silently.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

MAX_FILE_BYTES = 1_000_000       # larger files are listed with their size but no content
MAX_FILES = 8000                 # the manifest stops here and says it truncated
BINARY_SNIFF = 8192
IDEM_FILE = "outbox_idempotency.json"
IDEM_MAX = 2000                  # remembered keys before the oldest are pruned
BLOB_DIR = "blobs"

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next",
    ".expo", ".worktrees", ".mypy_cache", ".pytest_cache", ".ruff_cache", "coverage",
}


# ---------------------------------------------------------------- helpers

def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_binary(head: bytes) -> bool:
    return b"\x00" in head


def _atomic_write(path: str, data: bytes) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".vfs-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def _save_json(path: str, data: Any) -> None:
    _atomic_write(path, json.dumps(data, indent=2, sort_keys=True).encode("utf-8"))


_PY_DEF = re.compile(r"^(?:async\s+)?(?:function|class|def)\s+([A-Za-z_$][\w$]*)")
_JS_DEF = re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)")
_JS_CONST = re.compile(r"^(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\(|function|class)")


def _symbols_python(text: str) -> List[Dict[str, Any]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            out.append({
                "name": node.name,
                "kind": kind,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
            })
    return out


def _symbols_script(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        m = _JS_DEF.match(line) or _JS_CONST.match(line)
        if m:
            out.append({
                "name": m.group(1),
                "kind": "class" if "class " in line[:m.end()] else "function",
                "line": i,
                "end_line": i,
            })
    return out


def symbols_for(rel_path: str, text: str) -> List[Dict[str, Any]]:
    """Top-level names in a file, so the phone can offer `read_symbol` without the text."""
    ext = os.path.splitext(rel_path)[1].lower()
    if ext == ".py":
        return _symbols_python(text)
    if ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
        return _symbols_script(text)
    return []


# ---------------------------------------------------------------- listing

def _git_files(root: str) -> Optional[List[str]]:
    try:
        res = subprocess.run(
            ["git", "-C", root, "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def _walk_files(root: str) -> List[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            out.append(os.path.relpath(os.path.join(dirpath, name), root).replace("\\", "/"))
    return out


def list_files(root: str) -> List[str]:
    """Every tracked or untracked-but-not-ignored file, skipping the usual build debris."""
    files = _git_files(root)
    if files is None:
        files = _walk_files(root)
    keep = []
    for rel in files:
        parts = rel.replace("\\", "/").split("/")
        if any(p in SKIP_DIRS for p in parts):
            continue
        keep.append("/".join(parts))
    return sorted(set(keep))


def resolve(root: str, rel: str) -> str:
    """Absolute path for a manifest path, refusing anything that escapes the root."""
    clean = str(rel or "").replace("\\", "/")
    if not clean or clean.startswith("/") or clean.startswith("../") or "/../" in clean:
        raise ValueError("path must stay inside the project")
    root = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root, clean))
    root = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root, clean))
    try:
        if os.path.commonpath([root, full]) != root:
            raise ValueError("path must stay inside the project")
    except ValueError:
        raise ValueError("path must stay inside the project")
    return full


# ---------------------------------------------------------------- manifest

def manifest(project_dir: str, limit: int = MAX_FILES) -> Dict[str, Any]:
    """A compact index of the tree. No file content: paths, hashes, sizes, symbol names."""
    root = os.path.realpath(project_dir or os.getcwd())
    if not os.path.isdir(root):
        raise ValueError(f"Project directory does not exist: {project_dir}")
    limit = max(1, min(int(limit or MAX_FILES), MAX_FILES))
    files = list_files(root)
    truncated = len(files) > limit
    entry: List[Dict[str, Any]] = []
    total = 0
    for rel in files[:limit]:
        try:
            # Git can list a symlink whose target escapes the repository. Never include or
            # hash bytes outside the selected project root.
            full = resolve(root, rel)
            size = os.path.getsize(full)
        except (OSError, ValueError):
            # `resolve` raises ValueError for an escaping symlink; exclude it from the manifest.
            continue
        item: Dict[str, Any] = {"path": rel, "size": size}
        if size > MAX_FILE_BYTES:
            item["sha"] = ""
            item["skipped"] = "too large"
            entry.append(item)
            continue
        try:
            with open(full, "rb") as f:
                head = f.read(BINARY_SNIFF)
            if _is_binary(head):
                item["sha"] = _sha_file(full)
                item["binary"] = True
                entry.append(item)
                total += size
                continue
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            continue
        item["sha"] = _sha_bytes(data)
        try:
            symbols = symbols_for(rel, data.decode("utf-8"))
        except UnicodeDecodeError:
            symbols = []
        if symbols:
            item["symbols"] = symbols
        entry.append(item)
        total += size
    return {
        "root": root,
        "project": os.path.basename(root),
        "count": len(entry),
        "total_bytes": total,
        "truncated": truncated,
        "files": entry,
    }


# ---------------------------------------------------------------- blobs

def _blob_path(subway_dir: str, sha: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", str(sha or "")):
        raise ValueError("sha must be a 64-character hex digest")
    return os.path.join(subway_dir, BLOB_DIR, sha[:2], sha)


def blob(project_dir: str, rel: str = "", sha: str = "", subway_dir: str = "",
         max_bytes: int = MAX_FILE_BYTES) -> Dict[str, Any]:
    """File text for one path or one sha, cached by content address under SUBWAY_DIR/blobs."""
    if not rel and not sha:
        raise ValueError("path or sha is required")
    if rel:
        root = os.path.realpath(project_dir or os.getcwd())
        full = resolve(root, rel)
        if not os.path.isfile(full):
            raise FileNotFoundError(rel)
        if os.path.getsize(full) > max_bytes:
            raise ValueError("file is too large to send to a phone")
        with open(full, "rb") as f:
            data = f.read()
        found = _sha_bytes(data)
        if sha and sha != found:
            raise ValueError("sha does not match the file on disk; fetch the manifest again")
        if _is_binary(data[:BINARY_SNIFF]):
            raise ValueError("file is binary")
        if subway_dir:
            try:
                _atomic_write(_blob_path(subway_dir, found), data)
            except (OSError, ValueError):
                pass
        return {"path": rel.replace("\\", "/"), "sha": found, "size": len(data),
                "text": data.decode("utf-8", "replace"), "cached": False}
    cached = _blob_path(subway_dir, sha) if subway_dir else ""
    if cached and os.path.isfile(cached):
        with open(cached, "rb") as f:
            data = f.read()
        return {"path": "", "sha": sha, "size": len(data),
                "text": data.decode("utf-8", "replace"), "cached": True}
    raise FileNotFoundError("no blob for that sha on this station")


# ---------------------------------------------------------------- outbox

def _idem_path(subway_dir: str) -> str:
    return os.path.join(subway_dir, IDEM_FILE)


def _idem_load(subway_dir: str) -> Dict[str, Any]:
    data = _load_json(_idem_path(subway_dir), {})
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def _idem_save(subway_dir: str, entries: Dict[str, Any]) -> None:
    if len(entries) > IDEM_MAX:
        ordered = sorted(entries.items(), key=lambda kv: float(kv[1].get("at", 0)))
        entries = dict(ordered[-IDEM_MAX:])
    _save_json(_idem_path(subway_dir), {"entries": entries})


def _claimed_by_someone_else(root: str, full: str) -> str:
    """A path another station holds a live lease on, or "" when it is free."""
    try:
        from cli_tools.locks import load_locks
    except Exception:
        return ""
    try:
        locks = load_locks()
    except Exception:
        return ""
    rows = locks.get("locks") if isinstance(locks, dict) else None
    if not isinstance(rows, dict):
        return ""
    now = time.time()
    for key, row in rows.items():
        if not isinstance(row, dict):
            continue
        try:
            if float(row.get("expires_at") or 0) < now:
                continue
        except (TypeError, ValueError):
            continue
        try:
            if os.path.realpath(key) == full:
                return str(row.get("holder") or "another station")
        except (OSError, ValueError):
            continue
    return ""


def _run_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    import server as _s

    # An offline queue is not a general-purpose MCP gateway. Only allow dispatching a worker;
    # in particular, never execute arbitrary shell, git, publish, permission, or terminal tools.
    if name != "spin_worker":
        return {"status": "failed", "detail": f"offline outbox does not allow tool {name!r}"}
    entry = getattr(_s, "TOOLS", {}).get(name)
    if entry is None:
        return {"status": "failed", "detail": f"unknown tool {name}"}
    func = entry[0]
    try:
        return {"status": "ok", "detail": str(func(**arguments))[:4000]}
    except TypeError as exc:
        return {"status": "failed", "detail": f"bad arguments: {exc}"}
    except Exception as exc:  # a tool's own failure is the item's failure
        return {"status": "failed", "detail": str(exc)[:2000]}


def _apply(item: Dict[str, Any], root: str) -> Dict[str, Any]:
    op = str(item.get("op") or "").strip().lower()
    rel = str(item.get("path") or "")
    if op in ("write", "delete"):
        try:
            full = resolve(root, rel)
        except ValueError as exc:
            return {"status": "failed", "detail": str(exc)}
        holder = _claimed_by_someone_else(root, full)
        if holder:
            return {"status": "conflict", "detail": f"{rel} is claimed by {holder}"}
        base = item.get("base_sha")
        current = ""
        if os.path.isfile(full):
            current = _sha_file(full)
        if base not in (None, "") and str(base) != current:
            return {"status": "conflict", "detail": f"{rel} changed on the station since it was fetched",
                    "sha": current}
        if op == "delete":
            if not os.path.isfile(full):
                return {"status": "ok", "detail": "already absent", "path": rel}
            try:
                os.unlink(full)
            except OSError as exc:
                return {"status": "failed", "detail": str(exc)}
            return {"status": "ok", "path": rel, "deleted": True}
        content = item.get("content")
        if not isinstance(content, str):
            return {"status": "failed", "detail": "content must be a string"}
        data = content.encode("utf-8")
        try:
            _atomic_write(full, data)
        except OSError as exc:
            return {"status": "failed", "detail": str(exc)}
        return {"status": "ok", "path": rel, "sha": _sha_bytes(data), "size": len(data)}
    if op in ("tool", "call"):
        name = str(item.get("name") or "")
        arguments = item.get("arguments")
        if not name or not isinstance(arguments, dict):
            return {"status": "failed", "detail": "tool items need a name and an arguments object"}
        return _run_tool(name, arguments)
    return {"status": "failed", "detail": f"unknown op {op!r}"}


def sync(items: Any, project_dir: str, subway_dir: str, device: str = "",
         idempotency_key: str = "") -> Dict[str, Any]:
    """Apply a batch once, serialized under a station lock and persisted before side effects.

    A replayed batch key with a saved result returns that result. If the process died while a
    batch was marked in-progress, the retry reports in-progress and does not repeat side effects;
    the user can reconcile it explicitly. This chooses at-most-once over duplicate dispatch.
    """
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    if len(items) > 200:
        raise ValueError("at most 200 items per batch")
    if idempotency_key and (len(idempotency_key) > 200 or not re.fullmatch(r"[A-Za-z0-9._:-]+", idempotency_key)):
        raise ValueError("X-Idempotency-Key must be 1-200 safe ASCII characters")
    root = os.path.realpath(project_dir or os.getcwd())
    if not os.path.isdir(root):
        raise ValueError("project directory does not exist")
    # All HTTP workers share this lock. Keep the idempotency check and side effects in one critical
    # section, so two concurrent retries cannot both launch the same item.
    try:
        import server as _s
        lock = _s._state_lock("outbox_idempotency")
    except Exception:
        lock = contextlib.nullcontext()
    with lock:
        return _sync_locked(items, root, subway_dir, device, idempotency_key)


def _sync_locked(items: List[Any], root: str, subway_dir: str, device: str,
                 idempotency_key: str) -> Dict[str, Any]:
    entries = _idem_load(subway_dir)
    batch_key = f"batch:{device}:{root}:{idempotency_key}" if idempotency_key else ""
    if batch_key and batch_key in entries:
        saved = entries[batch_key]
        if saved.get("state") == "done" and isinstance(saved.get("result"), dict):
            return {**saved["result"], "replayed": True}
        return {"ok": True, "root": root, "replayed": True, "in_progress": True,
                "results": [], "applied": {"in_progress": 1}}
    if batch_key:
        entries[batch_key] = {"at": time.time(), "device": device, "state": "in_progress"}
        _idem_save(subway_dir, entries)
    results: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            results.append({"key": "", "status": "failed", "detail": "item must be an object"})
            continue
        key = str(item.get("key") or item.get("idempotency_key") or "").strip()
        if key and key in entries:
            stored = entries[key].get("result")
            results.append({**stored, "status": stored.get("status", "ok"), "replayed": True}) if isinstance(stored, dict) else results.append({"key": key, "status": "ok", "replayed": True})
            continue
        outcome: Dict[str, Any] = {"key": key, "index": index}
        try:
            outcome.update(_apply(item, root))
        except Exception as exc:
            outcome.update({"status": "failed", "detail": str(exc)[:2000]})
        if key:
            entries[key] = {"at": time.time(), "device": device, "result": outcome}
        results.append(outcome)
    counts: Dict[str, int] = {}
    for row in results:
        counts[str(row.get("status"))] = counts.get(str(row.get("status")), 0) + 1
    response = {"ok": True, "root": root, "applied": counts, "results": results}
    if batch_key:
        entries[batch_key] = {"at": time.time(), "device": device, "state": "done", "result": response}
    if batch_key or any(str(item.get("key") or item.get("idempotency_key") or "")
                        for item in items if isinstance(item, dict)):
        _idem_save(subway_dir, entries)
    return response
