from __future__ import annotations
import sys
sys.modules.setdefault("server", sys.modules[__name__])
import base64
import concurrent.futures
import contextlib
import contextvars
import datetime as _dt
import difflib
import glob
import hashlib
import hmac
import http.server
import json
import math
import os
import re
import secrets
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
try:
    import ctypes
except ImportError:
    ctypes = None
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple
import terminals
import native_terminals
import threads
try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None
import server as _s


def _py_range_to_defs(source: str, start_l: int, end_l: int) -> Optional[Tuple[int, int, str]]:
    """Grow a line range to the smallest enclosing top-level def/class.

    Returns (start, end, label) or None when the range should be kept as-is
    (no enclosing top-level definition, or the file does not parse).
    """
    import ast
    try:
        tree = ast.parse(source)
    except Exception:
        return None  # syntax error / unparsable: caller keeps the raw range
    spans = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            n_start = min([d.lineno for d in getattr(node, "decorator_list", [])] + [node.lineno])
            n_end = getattr(node, "end_lineno", None) or n_start
            spans.append((n_start, n_end, node.name))
    hits = [s for s in spans if s[0] <= end_l and s[1] >= start_l]
    if not hits:
        return None
    new_start = min(s[0] for s in hits)
    new_end = max(s[1] for s in hits)
    label = hits[0][2] if len(hits) == 1 else "+".join(s[2] for s in hits[:4]) + ("+%d more" % (len(hits) - 4) if len(hits) > 4 else "")
    return (new_start, new_end, label)


_JS_SYMBOL_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "js-symbol.mjs")

def _js_ast_span(abs_path: str, target: str) -> Optional[Tuple[int, int, str]]:
    """Exact (start, end, kind) of a JS symbol via acorn (bin/js-symbol.mjs), or None if not found or unparsable."""
    node = shutil.which("node")
    if not node or not os.path.isfile(_JS_SYMBOL_SCRIPT):
        return None
    try:
        out = subprocess.run([node, _JS_SYMBOL_SCRIPT, abs_path, target], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        d = json.loads(out.stdout)
        return int(d["start"]), int(d["end"]), str(d.get("kind") or "")
    except (ValueError, KeyError, TypeError):
        return None

def _extract_code_item(item: str, cwd: str) -> Optional[Tuple[str, int, int, str, str]]:
    """Resolve an item specification to (display_path, start_line, end_line, content, abs_path)."""
    import ast
    root = os.path.realpath(cwd)
    if ":" in item:
        file_part, target = item.rsplit(":", 1)
        abs_path = os.path.realpath(os.path.join(root, file_part))
        if not (abs_path == root or abs_path.startswith(root + os.sep)):
            return None
        if not os.path.isfile(abs_path):
            return None
        display_path = os.path.relpath(abs_path, root)

        range_match = re.match(r"^(\d+)-(\d+)$", target)
        if range_match:
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                start_l, end_l = int(range_match.group(1)), int(range_match.group(2))
                if start_l < 1 or end_l < start_l:
                    return None
                if not lines:
                    return None
                start_l = min(start_l, len(lines))
                end_l = min(end_l, len(lines))
                if file_part.endswith(".py"):
                    # AST-first: a Python text range becomes the enclosing def/class span.
                    expanded = _py_range_to_defs("".join(lines), start_l, end_l)
                    if expanded is not None:
                        new_start, new_end, label = expanded
                        new_start = max(1, min(new_start, len(lines)))
                        new_end = max(new_start, min(new_end, len(lines)))
                        body = "".join(lines[new_start - 1 : new_end])
                        content = f"# {display_path}:{new_start}-{new_end} ({label})\n{body}"
                        return (display_path, new_start, new_end, content, abs_path)
                # Every other file (and .py where ast fails / no enclosing def)
                # returns the plain line range, as before.
                content = "".join(lines[start_l - 1 : end_l])
                return (display_path, start_l, end_l, content, abs_path)
            except Exception:
                return None

        if file_part.endswith((".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")):
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                if file_part.endswith((".js", ".mjs", ".cjs")):
                    ast_span = _js_ast_span(abs_path, target)
                    if ast_span is not None:
                        a_start, a_end, _kind = ast_span
                        a_start = max(1, min(a_start, len(lines)))
                        a_end = max(a_start, min(a_end, len(lines)))
                        return (display_path, a_start, a_end, "".join(lines[a_start - 1 : a_end]), abs_path)
                pattern = re.compile(r'\bfunction\s+' + re.escape(target) + r'\b')
                for i, line in enumerate(lines):
                    if pattern.search(line):
                        start_l = i + 1
                        indent_len = len(line) - len(line.lstrip())
                        end_l = None
                        if line.rstrip().endswith("}") and "{" in line and line.count("{") == line.count("}"):
                            end_l = start_l
                        else:
                            for j in range(i + 1, len(lines)):
                                cand = lines[j]
                                cand_indent = len(cand) - len(cand.lstrip())
                                if cand_indent == indent_len and cand.strip().startswith("}"):
                                    end_l = j + 1
                                    break
                        if end_l is not None:
                            content = "".join(lines[start_l - 1 : end_l])
                            return (display_path, start_l, end_l, content, abs_path)
                return None
            except Exception:
                return None

        if file_part.endswith(".py"):
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    source = f.read()
                    lines = source.splitlines(keepends=True)
                tree = ast.parse(source, filename=abs_path)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == target:
                        start_l = min([d.lineno for d in getattr(node, "decorator_list", [])] + [node.lineno])
                        end_l = getattr(node, "end_lineno", start_l)
                        content = "".join(lines[start_l - 1 : end_l])
                        return (display_path, start_l, end_l, content, abs_path)
                return None
            except Exception:
                return None
        return None
    else:
        target = item
        candidates = []
        server_py = os.path.join(root, "server.py")
        if os.path.isfile(server_py):
            candidates.append(os.path.realpath(server_py))
        try:
            for entry in sorted(os.listdir(root)):
                if entry.endswith(".py") and entry != "server.py":
                    p = os.path.realpath(os.path.join(root, entry))
                    if not (p == root or p.startswith(root + os.sep)):
                        continue
                    if os.path.isfile(p):
                        candidates.append(p)
        except Exception:
            pass
        this_file = os.path.realpath(_s.__file__)
        if this_file == root or this_file.startswith(root + os.sep):
            if this_file not in candidates and os.path.isfile(this_file):
                candidates.append(this_file)

        for abs_path in candidates:
            abs_path = os.path.realpath(abs_path)
            if not (abs_path == root or abs_path.startswith(root + os.sep)):
                continue
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    source = f.read()
                    lines = source.splitlines(keepends=True)
                tree = ast.parse(source, filename=abs_path)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == target:
                        start_l = min([d.lineno for d in getattr(node, "decorator_list", [])] + [node.lineno])
                        end_l = getattr(node, "end_lineno", start_l)
                        display_path = os.path.relpath(abs_path, root)
                        content = "".join(lines[start_l - 1 : end_l])
                        return (display_path, start_l, end_l, content, abs_path)
            except Exception:
                continue
        return None


def _reviewer_task_text(prompt: str, limit: int = 6000) -> str:
    """The previous worker's own task, so a reviewer judges the patch against what was actually asked."""
    text = str(prompt or "").strip()
    if text.startswith(_s._TASK_RULES_OPENERS):
        parts = text.split("\n\n", 1)
        if len(parts) == 2 and parts[1].strip():
            text = parts[1].strip()
    return text[:limit].rstrip()


def _capture_in_place_review_diff(meta: Dict[str, Any]) -> None:
    """Save an in-place worker's git changes as reviewer context, not as an applicable patch."""
    project_dir = str(meta.get("project_dir") or "")
    try:
        root_result = subprocess.run(
            ["git", "-C", project_dir, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        diff_text = f"No diff is available: could not inspect the project git repository: {_s.one_line(str(exc), 200)}"
    else:
        if root_result.returncode:
            diff_text = "No diff is available: the project folder is not a git repository."
        else:
            root = root_result.stdout.strip()
            try:
                status_result = subprocess.run(
                    ["git", "-C", root, "status", "--porcelain=1", "-z", "--untracked-files=all"],
                    capture_output=True, timeout=60,
                )
                if status_result.returncode:
                    diff_text = ("No diff is available: could not capture git changes. "
                                 + _s.one_line(status_result.stderr, 200))
                else:
                    status_entries = _s._git_status_entries(status_result.stdout)
                    dirty_before = set(meta.get("git_dirty_before") or [])
                    changed = [(code, path) for code, path in status_entries if path not in dirty_before]
                    preexisting = [(code, path) for code, path in status_entries if path in dirty_before]
                    pieces = []
                    for index, (title, entries) in enumerate((
                            ("Changed by this worker", changed),
                            ("Already modified before this worker started (may include this worker's edits)", preexisting))):
                        if index:
                            pieces.append("")
                        pieces.append(title)
                        if index:
                            pieces.append("The second group may contain other people's work.")
                        paths = list(dict.fromkeys(path for _, path in entries))
                        if not paths:
                            pieces.append("(none)")
                            continue
                        pieces.extend(f"{code} {path}" for code, path in entries)
                        tracked_paths = [path for code, path in entries if not code.startswith("??")]
                        pathspecs = [f":(literal){path}" for path in dict.fromkeys(tracked_paths)]
                        if pathspecs:
                            for label, args in (
                                ("Git diff", ["git", "-C", root, "diff", "--no-ext-diff", "--"] + pathspecs),
                                ("Git staged diff", ["git", "-C", root, "diff", "--cached", "--no-ext-diff", "--"] + pathspecs),
                            ):
                                result = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=60)
                                if result.returncode:
                                    raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
                                if result.stdout.strip():
                                    pieces.append(f"{label}:\n{result.stdout.rstrip()}")
                        for code, rel_path in entries:
                            if not code.startswith("??"):
                                continue
                            file_path = os.path.realpath(os.path.join(root, rel_path))
                            if os.path.commonpath((root, file_path)) != root or not os.path.isfile(file_path):
                                continue
                            with open(file_path, "rb") as f:
                                content = f.read(_s._REVIEW_DIFF_LIMIT + 1).decode("utf-8", "replace")
                            pieces.append(f"Untracked file {rel_path}:\n{content}")
                    diff_text = "\n\n".join(piece for piece in pieces if piece.strip())
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                diff_text = f"No diff is available: could not capture git changes: {_s.one_line(str(exc), 200)}"

    trimmed_note = "\n\n[Review diff trimmed to 60000 characters.]"
    if len(diff_text) > _s._REVIEW_DIFF_LIMIT:
        diff_text = diff_text[:_s._REVIEW_DIFF_LIMIT - len(trimmed_note)] + trimmed_note
    review_diff_path = os.path.join(_s.WORKERS_DIR, f"{meta['id']}.review_diff")
    try:
        with open(review_diff_path, "w", encoding="utf-8") as f:
            f.write(diff_text)
        meta["review_diff_path"] = review_diff_path
    except OSError as exc:
        meta["review_diff_note"] = f"No diff is available: could not save reviewer diff: {_s.one_line(str(exc), 200)}"


def _format_ast_args(args_node) -> str:
    parts = []
    for a in getattr(args_node, "posonlyargs", []):
        parts.append(a.arg)
    for a in args_node.args:
        parts.append(a.arg)
    if args_node.vararg:
        parts.append(f"*{args_node.vararg.arg}")
    for a in getattr(args_node, "kwonlyargs", []):
        parts.append(a.arg)
    if args_node.kwarg:
        parts.append(f"**{args_node.kwarg.arg}")
    return ", ".join(parts)


def outline(abs_path: str) -> str:
    """List a file's classes and functions with line ranges, not the bodies."""
    if not os.path.isfile(abs_path):
        return ""
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return ""

    if abs_path.endswith(".py"):
        try:
            import ast
            tree = ast.parse("".join(lines), filename=abs_path)
            out_lines: List[str] = []
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    end_l = getattr(node, "end_lineno", node.lineno)
                    bases = [ast.unparse(b) for b in node.bases] if hasattr(ast, "unparse") else [getattr(b, "id", "") for b in node.bases]
                    bases_str = f"({', '.join(bases)})" if bases else ""
                    out_lines.append(f"L{node.lineno}-{end_l} class {node.name}{bases_str}")
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            m_end = getattr(item, "end_lineno", item.lineno)
                            m_args = _format_ast_args(item.args)
                            out_lines.append(f"  L{item.lineno}-{m_end} def {item.name}({m_args})")
                        elif isinstance(item, ast.ClassDef):
                            c_end = getattr(item, "end_lineno", item.lineno)
                            c_bases = [ast.unparse(b) for b in item.bases] if hasattr(ast, "unparse") else [getattr(b, "id", "") for b in item.bases]
                            c_bases_str = f"({', '.join(c_bases)})" if c_bases else ""
                            out_lines.append(f"  L{item.lineno}-{c_end} class {item.name}{c_bases_str}")
                            for sub in item.body:
                                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                    s_end = getattr(sub, "end_lineno", sub.lineno)
                                    s_args = _format_ast_args(sub.args)
                                    out_lines.append(f"    L{sub.lineno}-{s_end} def {sub.name}({s_args})")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    end_l = getattr(node, "end_lineno", node.lineno)
                    args_str = _format_ast_args(node.args)
                    out_lines.append(f"L{node.lineno}-{end_l} def {node.name}({args_str})")
            return "\n".join(out_lines) + ("\n" if out_lines else "")
        except Exception:
            pass

    if abs_path.endswith((".js", ".mjs", ".cjs")):
        node = shutil.which("node")
        if node and os.path.isfile(_JS_SYMBOL_SCRIPT):
            try:
                out = subprocess.run([node, _JS_SYMBOL_SCRIPT, abs_path, "--outline"], capture_output=True, text=True, timeout=5)
                if out.returncode == 0:
                    res = out.stdout
                    if res and not res.endswith("\n"):
                        res += "\n"
                    return res
            except (OSError, subprocess.SubprocessError):
                pass

    if not lines:
        return ""
    if len(lines) <= 40:
        res = "".join(lines)
        if not res.endswith("\n"):
            res += "\n"
        return res
    res = "".join(lines[:40])
    if not res.endswith("\n"):
        res += "\n"
    res += f"[... {len(lines) - 40} more lines]\n"
    return res


