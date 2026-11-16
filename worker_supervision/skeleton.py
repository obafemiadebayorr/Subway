"""Skeleton ("~skel") code packets: a whole file folded down to signatures, one symbol expanded.

fold_python uses ast (end_lineno); fold_js uses a brace-depth scanner. No dependencies.
Both return (text, folded_count) or None when the file cannot be parsed or the symbol is absent.
"""
import ast
import os
import re
from typing import List, Optional, Tuple

PY_EXTS = (".py",)
JS_EXTS = (".js", ".mjs", ".cjs")


def _top_name(symbol: str) -> str:
    return str(symbol or "").split(".")[0]


def _sub_name(symbol: str) -> Optional[str]:
    parts = str(symbol or "").split(".", 1)
    return parts[1] if len(parts) > 1 else None


def _sig_text(lines: List[str], node) -> str:
    body0 = node.body[0]
    end_idx = body0.lineno - 1  # lines up to the line before body starts
    if (isinstance(body0, ast.Expr) and isinstance(body0.value, ast.Constant)
            and isinstance(body0.value.value, str)):
        sig_lines = list(lines[node.lineno - 1:body0.lineno])
        if sig_lines:
            sig_lines[-1] = sig_lines[-1].rstrip("\n")[:100] + "\n"
        return "".join(sig_lines)
    return "".join(lines[node.lineno - 1:end_idx])


def _assemble(lines: List[str], folds: List[Tuple[int, int, str]], comment: str) -> str:
    """folds: (start_line, end_line, signature_text), 1-based inclusive, non-overlapping."""
    out: List[str] = []
    i = 1
    by_start = {f[0]: f for f in folds}
    while i <= len(lines):
        fold = by_start.get(i)
        if fold:
            start, end, sig = fold
            out.append(f"{sig.rstrip()}  {comment} … folded (lines {start}-{end})\n")
            i = end + 1
        else:
            out.append(lines[i - 1] if lines[i - 1].endswith("\n") else lines[i - 1] + "\n")
            i += 1
    return "".join(out)


def _fold_def(lines: List[str], node) -> Optional[Tuple[int, int, str]]:
    end = getattr(node, "end_lineno", None)
    if not end:
        return None
    start = min([d.lineno for d in node.decorator_list] + [node.lineno])
    if end <= node.lineno:  # one-line def: nothing to fold
        return None
    return (start, end, _sig_text(lines, node))


def _assign_name(node) -> Optional[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for t in targets:
        if isinstance(t, ast.Name):
            return t.id
    return None


def fold_python(text: str, symbol: str) -> Optional[Tuple[str, int]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    lines = text.splitlines(keepends=True)
    symbol = str(symbol or "")
    top = _top_name(symbol)
    sub = _sub_name(symbol)
    found = False
    folds: List[Tuple[int, int, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", None)
            if not end:
                return None
            if node.name == top:
                found = True
                if isinstance(node, ast.ClassDef) and sub:
                    method = next(
                        (m for m in node.body
                         if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name == sub),
                        None)
                    if method is None:
                        return None
                    for m in node.body:
                        if not isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            continue
                        if m.name == sub:
                            continue
                        f = _fold_def(lines, m)
                        if f:
                            folds.append(f)
                continue
            f = _fold_def(lines, node)
            if f:
                folds.append(f)
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            end = getattr(node, "end_lineno", None)
            if not end:
                return None
            if end - node.lineno + 1 > 15 and _assign_name(node) != symbol:
                folds.append((node.lineno, end, lines[node.lineno - 1]))
    if not found:
        return None
    folds.sort(key=lambda f: f[0])
    return _assemble(lines, folds, "#"), len(folds)


_REGEX_KEYWORDS = ("return", "typeof", "case", "do", "else", "in", "of", "yield", "await", "void", "delete", "throw", "new")


def _ends_with_keyword(text: str, i: int) -> bool:
    m = re.search(r"([A-Za-z_$][\w$]*)\s*$", text[max(0, i - 12):i])
    return bool(m) and m.group(1) in _REGEX_KEYWORDS


def _js_mask(text: str) -> str:
    """Same length as text; strings, comments, template text and regex literals become spaces (newlines kept)."""
    out = list(text)
    n = len(text)
    i = 0
    prev = ""  # last significant char, to tell a regex from a division

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
        elif c in ("'", '"'):
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            blank(i, j + 1)
            i = j + 1
            prev = "a"
        elif c == "`":
            j = i + 1
            depth = 0
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if depth == 0 and text[j] == "`":
                    break
                if text[j] == "$" and j + 1 < n and text[j + 1] == "{":
                    depth += 1
                    j += 2
                    continue
                if depth and text[j] == "}":
                    depth -= 1
                j += 1
            blank(i, j + 1)
            i = j + 1
            prev = "a"
        elif c == "/" and (prev in ("", "(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "+", "-", "*", "%", "<", ">", "~", "^")
                           or _ends_with_keyword(text, i)):
            j = i + 1
            in_class = False
            while j < n and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    in_class = True
                elif text[j] == "]":
                    in_class = False
                elif text[j] == "/" and not in_class:
                    break
                j += 1
            blank(i, j + 1)
            i = j + 1
            prev = "a"
        else:
            if not c.isspace():
                prev = c
            i += 1
    return "".join(out)


_JS_DECL = re.compile(
    r"(?:export\s+(?:default\s+)?)?(?:async\s+)?"
    r"(?:function\s*\*?\s*(?P<fn>[A-Za-z_$][\w$]*)"
    r"|class\s+(?P<cls>[A-Za-z_$][\w$]*)"
    r"|(?:const|let|var)\s+(?P<var>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
    r"(?:function\b|class\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>))"
)


def _match_brace(masked: str, open_idx: int) -> int:
    depth = 0
    for k in range(open_idx, len(masked)):
        ch = masked[k]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return k
    return -1


def _stmt_end(masked: str, start: int) -> int:
    """End index of an expression statement: a `;` at nesting 0, else the end of the line that closes it."""
    depth = 0
    for k in range(start, len(masked)):
        ch = masked[k]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ";" and depth <= 0:
            return k
        elif ch == "\n" and depth <= 0:
            return k
    return len(masked) - 1


def fold_js(text: str, symbol: str) -> Optional[Tuple[str, int]]:
    masked = _js_mask(text)
    lines = text.splitlines(keepends=True)
    line_starts = [0]
    for ln in lines:
        line_starts.append(line_starts[-1] + len(ln))

    def line_of(idx: int) -> int:
        lo, hi = 0, len(lines)
        while lo < hi:
            mid = (lo + hi) // 2
            if line_starts[mid + 1] <= idx:
                lo = mid + 1
            else:
                hi = mid
        return lo + 1

    top = _top_name(symbol)
    folds: List[Tuple[int, int, str]] = []
    found = False
    depth = 0
    pos = 0
    n = len(masked)
    while pos < n:
        line_end = masked.find("\n", pos)
        line_end = n if line_end < 0 else line_end
        m = _JS_DECL.match(masked, pos) if depth == 0 else None
        if m:
            name = m.group("fn") or m.group("cls") or m.group("var")
            decl_start = pos
            arrow = masked.find("=>", m.start(), m.end() + 1) if m.group("var") else -1
            if m.group("var") and arrow >= 0:
                k = arrow + 2
                while k < n and masked[k] in " \t\r\n":
                    k += 1
                end_idx = _match_brace(masked, k) if k < n and masked[k] == "{" else _stmt_end(masked, k)
            else:
                brace = masked.find("{", m.end())
                end_idx = _match_brace(masked, brace) if brace >= 0 else -1
            if end_idx >= 0:
                a, b = line_of(decl_start), line_of(end_idx)
                if name == top:
                    found = True
                elif b > a:
                    folds.append((a, b, lines[a - 1]))
                pos = end_idx + 1
                nl = masked.find("\n", pos)
                pos = n if nl < 0 else nl + 1
                continue
        for ch in masked[pos:line_end]:
            if ch in "{([":
                depth += 1
            elif ch in "})]":
                depth = max(0, depth - 1)
        pos = line_end + 1
    if not found:
        return None
    return _assemble(lines, folds, "//"), len(folds)


def fold_file(path: str, text: str, symbol: str) -> Optional[Tuple[str, int]]:
    low = path.lower()
    base = os.path.basename(path)
    if low.endswith(PY_EXTS):
        res = fold_python(text, symbol)
        comment = "#"
    elif low.endswith(JS_EXTS):
        res = fold_js(text, symbol)
        comment = "//"
    else:
        return None
    if res is None:
        return None
    out, count = res
    header = (f"{comment} Focus skeleton of {base}: `{symbol}` is complete and exact, "
              f"edit it directly. Folded parts are unchanged; read one with sed -n A,Bp only if you need it.\n")
    return header + out, count
