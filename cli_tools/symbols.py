import ast
import datetime as _dt
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import server as _s


def _get_extract():
    if hasattr(_s, "_extract_code_item"):
        return _s._extract_code_item
    try:
        from worker_supervision.code_items import _extract_code_item
        return _extract_code_item
    except Exception:
        return None


def t_read_symbol(file: str, symbol: str, include_callers: bool = False, project_dir: str = "") -> str:
    """Read one function or class (and optionally its callers) instead of a whole file."""
    if not file or not symbol:
        return "Error: file and symbol are required."

    root = os.path.realpath(project_dir or os.getcwd())
    extract_fn = _get_extract()
    if extract_fn is None:
        return f"Error: symbol '{symbol}' not found in {file}."

    res = extract_fn(f"{file}:{symbol}", root)
    if not res:
        return f"Error: symbol '{symbol}' not found in {file}."

    display_path, start_l, end_l, content, abs_path = res
    out = f"--- {display_path}:{start_l}-{end_l} ---\n{content}"
    if not out.endswith("\n"):
        out += "\n"

    if include_callers:
        callers: List[Tuple[str, int, str]] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "scratchpad") and "worktrees" not in d]
            if any(skip in dirpath.split(os.sep) for skip in (".git", "node_modules", "scratchpad")) or "worktrees" in dirpath:
                continue

            for fname in sorted(filenames):
                fpath = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(fpath, root)
                if rel_path.startswith("..") or "worktrees" in rel_path:
                    continue

                if fname.endswith(".py"):
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            src = f.read()
                        tree = ast.parse(src, filename=fpath)
                        src_lines = src.splitlines(keepends=True)
                        call_lines = set()
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Call):
                                is_match = False
                                if isinstance(node.func, ast.Name) and node.func.id == symbol:
                                    is_match = True
                                elif isinstance(node.func, ast.Attribute) and node.func.attr == symbol:
                                    is_match = True
                                if is_match and hasattr(node, "lineno"):
                                    call_lines.add(node.lineno)
                        for lno in sorted(call_lines):
                            c_start = max(1, lno - 1)
                            c_end = min(len(src_lines), lno + 1)
                            ctx = "".join(src_lines[c_start - 1:c_end])
                            callers.append((rel_path, lno, ctx))
                            if len(callers) >= 10:
                                break
                    except Exception:
                        continue
                elif fname.endswith((".js", ".ts", ".mjs", ".jsx", ".tsx")):
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            lines = f.readlines()
                        pattern = re.compile(r'\b' + re.escape(symbol) + r'\s*\(')
                        def_pattern = re.compile(r'\bfunction\s+' + re.escape(symbol) + r'\b')
                        for i, line in enumerate(lines):
                            if def_pattern.search(line):
                                continue
                            if pattern.search(line):
                                lno = i + 1
                                c_start = max(1, lno - 1)
                                c_end = min(len(lines), lno + 1)
                                ctx = "".join(lines[c_start - 1:c_end])
                                callers.append((rel_path, lno, ctx))
                                if len(callers) >= 10:
                                    break
                    except Exception:
                        continue
                if len(callers) >= 10:
                    break
            if len(callers) >= 10:
                break

        out += "\nCallers:\n"
        for c_path, c_line, c_ctx in callers[:10]:
            out += f"{c_path}:{c_line}\n{c_ctx}"
            if not out.endswith("\n"):
                out += "\n"

    if len(out) > 20000:
        out = out[:20000]
    return out


def t_read_outline(file: str, project_dir: str = "") -> str:
    """List a file's classes and functions with line ranges, not the bodies."""
    if not file:
        return "Error: file is required."
    root = os.path.realpath(project_dir or os.getcwd())
    abs_path = os.path.realpath(os.path.join(root, file))
    if not (abs_path == root or abs_path.startswith(root + os.sep)):
        return f"Error: file '{file}' outside project directory."
    if not os.path.isfile(abs_path):
        return f"Error: file '{file}' not found."
    try:
        from worker_supervision.code_items import outline
    except ImportError:
        outline = getattr(_s, "outline", None)
    if outline is None:
        return "Error: outline helper not available."
    return outline(abs_path)


def t_claim_symbol(filepath: str, symbol: str, holder: str = "", duration_seconds: int = 300,
                   purpose: str = "", project_dir: str = "", baseline_commit: str = "",
                   role: str = "implementer") -> str:
    """Acquire a temporary workspace lease on a specific symbol within a file."""
    if not filepath or not symbol:
        return "Error: filepath and symbol are required."

    root = project_dir or os.getcwd()
    canon_path = _s._canon_path(filepath, project_dir)

    extract_fn = _get_extract()
    if extract_fn is None:
        return f"Error: symbol '{symbol}' not found in {filepath}."

    res = extract_fn(f"{filepath}:{symbol}", root)
    if not res:
        res = extract_fn(f"{canon_path}:{symbol}", root)
    if not res:
        return f"Error: symbol '{symbol}' not found in {filepath}."

    display_path, start_l, end_l, content, abs_path = res

    explicit_holder = bool(holder)
    det = _s._detected_chat()
    is_unverified = False
    if det is not None:
        det_id = det["id"]
        if holder:
            if not _s._id_matches(holder, det_id):
                return f"Error: caller is '{det_id}', cannot claim symbol as '{holder}'."
            else:
                me_id = det_id
        else:
            holder = _s._short_id(det_id)
            me_id = det_id
    else:
        me_id = _s._self_id()
        holder = holder or _s._short_id(me_id)
        is_unverified = True

    role = (role or "implementer").lower()
    try:
        duration = max(10, min(int(duration_seconds), 3600))
    except (TypeError, ValueError):
        duration = 300

    head = _s._git_head(canon_path)
    worker = _s._load_worker(me_id)
    parent = worker.get("launched_by") if worker else None

    key = f"{canon_path}::{symbol}"
    with _s._state_lock("locks"):
        now = time.time()
        data = _s.load_locks()
        locks = data["locks"]

        # Check (c): another holder has a whole-file lease on that file
        file_lease = locks.get(canon_path)
        if file_lease and file_lease.get("holder") != holder and not (
            (not explicit_holder or file_lease.get("holder") == holder)
            and file_lease.get("holder_id")
            and _s._id_matches(file_lease.get("holder_id"), me_id)
        ):
            rem = max(0, int(file_lease.get("expires_at", 0) - now))
            purp_str = f" for '{file_lease.get('purpose')}'" if file_lease.get("purpose") else ""
            return (f"[COLLISION WARNING] File '{canon_path}' is already leased to "
                    f"{file_lease.get('holder')}{purp_str}. Lease expires in {rem}s.")

        # Check (b): another holder has the same key
        existing = locks.get(key)
        if existing and existing.get("holder") != holder and not (
            (not explicit_holder or existing.get("holder") == holder)
            and existing.get("holder_id")
            and _s._id_matches(existing.get("holder_id"), me_id)
        ):
            rem = max(0, int(existing.get("expires_at", 0) - now))
            purp_str = f" for '{existing.get('purpose')}'" if existing.get("purpose") else ""
            return (f"[COLLISION WARNING] Symbol '{symbol}' in '{canon_path}' is already leased to "
                    f"{existing.get('holder')}{purp_str}. Lease expires in {rem}s.")

        expires_at = now + duration
        baseline = baseline_commit or head
        locks[key] = {
            "holder": holder,
            "holder_id": me_id,
            "acquired_at": now,
            "expires_at": expires_at,
            "duration": duration,
            "purpose": purpose or "Active modification",
            "baseline_commit": baseline,
            "role": role,
            "status": "LEASED",
            "symbol": symbol,
            "file": canon_path,
            "start": start_l,
            "end": end_l,
        }
        if parent:
            locks[key]["parent"] = parent
        if is_unverified:
            locks[key]["unverified"] = True
        data["authors"][key] = {"holder": holder, "holder_id": me_id}
        if parent:
            data["authors"][key]["parent"] = parent
        if is_unverified:
            data["authors"][key]["unverified"] = True
        _s.save_locks(data)

    exp_iso = _dt.datetime.fromtimestamp(expires_at).astimezone().strftime("%H:%M:%S")
    unv_note = " [unverified]" if is_unverified else ""
    return (f"[LEASE ACQUIRED] Symbol '{symbol}' in '{canon_path}' claimed by {holder}{unv_note} "
            f"until {exp_iso} ({duration}s). Purpose: {purpose or 'Active modification'}.")
