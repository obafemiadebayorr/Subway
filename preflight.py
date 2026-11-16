import os
import re
import shutil
from typing import List, Optional

# Match path-like tokens: relative paths, paths with ~, /, or http/https
# e.g., foo.py, src/app.ts:func, ~/file.py, /path/to/file.py, http://example.com/foo.js
PATH_TOKEN_RE = re.compile(
    r"(?<![\w/])(?P<token>(?:https?://\S+|~[^\s]*|/\S*)?(?:[\w.-]+/)*[\w.-]+\.(?:jsonl|json|py|mjs|js|tsx|ts|css|md|html)(?![\w])(?::[\w.]+)?)"
)


def extract_brief_paths(prompt: str) -> List[str]:
    paths = []
    if not prompt:
        return paths
    for m in PATH_TOKEN_RE.finditer(prompt):
        tok = m.group("token")
        p = tok.split(":", 1)[0]
        if p and p not in paths:
            paths.append(p)
    return paths


def check_brief(prompt: str, project_dir: str) -> List[str]:
    warnings: List[str] = []
    if not prompt or not project_dir:
        return warnings

    proj_dir_abs = os.path.realpath(os.path.abspath(project_dir))

    for m in PATH_TOKEN_RE.finditer(prompt):
        if len(warnings) >= 5:
            break

        token = m.group("token")
        # skip ones starting with "~", "/" outside project_dir, or "http"
        if token.startswith("~") or token.startswith("http"):
            continue

        if ":" in token:
            file_part, sym_part = token.split(":", 1)
        else:
            file_part, sym_part = token, None

        if file_part.startswith("/"):
            file_abs = os.path.realpath(os.path.abspath(file_part))
            try:
                common = os.path.commonpath([proj_dir_abs, file_abs])
            except Exception:
                continue
            if common != proj_dir_abs:
                continue  # outside project_dir, skip
            # inside project_dir
            rel_path = os.path.relpath(file_abs, proj_dir_abs)
            target_path = file_abs
        else:
            rel_path = file_part
            target_path = os.path.join(proj_dir_abs, rel_path)

        # For each relative path that doesn't exist under project_dir:
        if not os.path.exists(target_path):
            # A quoted bare name like "config.json" is a string literal in quoted code, not a repo path.
            q_before = prompt[m.start() - 1:m.start()] if m.start() else ''
            if q_before in ('"', "'") and prompt[m.end():m.end() + 1] == q_before and '/' not in file_part:
                continue
            start_pos = m.start()
            before_40 = prompt[max(0, start_pos - 40):start_pos].lower()
            after = prompt[m.end():m.end()+20].lower()
            if ("new" in before_40 or "create" in before_40 or "add " in before_40
                    or "write " in before_40 or "new file" in before_40
                    or "(new" in after or "new file" in after or "to create" in after):
                continue
            warn = f"missing path: {rel_path}"
            if warn not in warnings:
                warnings.append(warn)
        else:
            # File exists; resolve path tokens (path:LINE, path:START-END,
            # path:symbol, bare path) via the code-item extractor first.
            sym_part_clean = sym_part.strip(".") if sym_part else sym_part
            try:
                from worker_supervision.code_items import _extract_code_item
                probe = f"{file_part}:{sym_part_clean}" if sym_part_clean else file_part
                if _extract_code_item(probe, proj_dir_abs) is not None:
                    continue
            except Exception:
                pass
            # check path:symbol
            if sym_part_clean and not re.fullmatch(r"\d+(-\d+)?", sym_part_clean.strip()):
                sym_name = sym_part_clean.split(".")[-1]
                try:
                    with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                        file_text = f.read()
                    if sym_name not in file_text:
                        warn = f"symbol not found: {token}"
                        if warn not in warnings:
                            warnings.append(warn)
                except Exception:
                    pass

    return warnings[:5]


def _get_provider_binary(provider: str) -> Optional[str]:
    # Reuse whatever mapping spawn.py already uses to build worker commands
    try:
        import subway as _s
        opencode_bin = _s.find_opencode_binary()
        copilot_bin = _s.find_copilot_binary()
        cursor_bin = getattr(_s, "find_cursor_binary", lambda: None)()
        devin_bin = getattr(_s, "find_devin_binary", lambda: None)()
        binaries = {
            "claude": _s.find_claude_binary(),
            "opencode": opencode_bin,
            "antigravity": _s.find_agy_binary(),
            "local": opencode_bin,
            "copilot": copilot_bin,
            "pi": getattr(_s, "find_pi_binary", lambda: None)(),
            "cursor": cursor_bin,
            "devin": devin_bin,
        }
        try:
            custom_map = {p["id"]: p for p in _s.load_custom_providers()}
            for cid, cp in custom_map.items():
                binaries[cid] = shutil.which(cp.get("bin", ""))
        except Exception:
            pass

        norm_p = {"claude-code": "claude", "opencode2": "opencode", "agy": "antigravity",
                  "ollama": "local", "github-copilot": "copilot"}.get(provider, provider)
        if norm_p in binaries:
            return binaries[norm_p]
    except Exception:
        pass

    # Fallback directly via shutil.which for standard names
    mapping = {
        "claude": "claude",
        "opencode": "opencode",
        "antigravity": "agy",
        "local": "opencode",
        "copilot": "copilot",
        "pi": "pi",
        "cursor": "cursor",
        "devin": "devin",
    }
    bin_name = mapping.get(provider, provider)
    return shutil.which(bin_name)


def check_provider(provider: str, model: str) -> List[str]:
    warnings: List[str] = []
    if not provider:
        return warnings

    prov = provider.lower()
    known_binaries = {
        "opencode": "opencode",
        "pi": "pi",
        "copilot": "copilot",
        "claude": "claude",
        "gemini": "gemini",
    }
    if prov != "antigravity" and prov in known_binaries:
        bin_name = known_binaries[prov]
        if prov == "pi":
            repo_root = os.path.dirname(os.path.abspath(__file__))
            pi_candidates = [
                os.path.join(repo_root, "bin", "pi-deep"),
                os.path.expanduser(os.path.join("~", ".deepai-code", "bin", "deepai")),
            ]
            pi_present = any(os.path.exists(p) for p in pi_candidates) or bool(shutil.which("pi"))
            if not pi_present:
                warnings.append(f"provider CLI not found: {provider}")
        elif not shutil.which(bin_name):
            warnings.append(f"provider CLI not found: {provider}")

    # (b) model currently marked bad (honours reset_time, the 24h window and stall cooldowns)
    if model:
        try:
            import local_models
            if local_models._model_is_bad(model):
                warnings.append(f"model marked bad: {model}")
        except Exception:
            pass

    return warnings


PROVIDER_OVERHEAD_TOKENS = {"claude": 25000, "opencode": 12000, "pi": 8000, "antigravity": 15000}
DEFAULT_OVERHEAD_TOKENS = 12000
DEFAULT_CONTEXT_WINDOW = 128000
SOLID_BLOCK_FRACTION = 0.5
_PROVIDER_ALIASES = {"claude-code": "claude", "opencode2": "opencode", "agy": "antigravity",
                     "ollama": "opencode", "local": "opencode", "github-copilot": "copilot"}
_PACKET_HDR_RE = re.compile(r"^--- (?P<path>[^\n]+?):\d+-\d+ ---$", re.M)


def _parse_ctx(value) -> int:
    """Context size from an int or a string like 128000, '200K' or '1M'. 0 when unusable."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else 0
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([km]?)", str(value or "").strip().lower().replace(",", ""))
    if not m:
        return 0
    return int(float(m.group(1)) * {"": 1, "k": 1000, "m": 1000000}[m.group(2)])


def _context_sources() -> List[dict]:
    """Known model context sizes from cached files only (no network): [{'names': [...], 'ctx': value}]."""
    rows: List[dict] = []
    try:
        import server as _s
        pi = _s._load_json("pi_models.json")
        for m in (pi.get("models") or []) if isinstance(pi, dict) else []:
            if isinstance(m, dict):
                rows.append({"names": [str(m.get("id") or "")], "ctx": m.get("contextWindow") or m.get("context")})
        import local_models
        disc = local_models.load_discovered_models()
        for key, rec in ((disc.get("models") or {}) if isinstance(disc, dict) else {}).items():
            if isinstance(rec, dict):
                rows.append({"names": [str(rec.get("model_id") or ""), str(key)], "ctx": rec.get("context_limit")})
    except Exception:
        pass
    return rows


def context_window(provider: str, model: str) -> int:
    want = str(model or "").strip().lower()
    if want:
        try:
            for row in _context_sources():
                ctx = _parse_ctx(row.get("ctx"))
                if not ctx:
                    continue
                for name in row.get("names") or []:
                    n = str(name).strip().lower()
                    if n and (n == want or n.endswith("/" + want) or n.endswith(":" + want) or want.endswith("/" + n)):
                        return ctx
        except Exception:
            pass
    return DEFAULT_CONTEXT_WINDOW


def largest_block(prompt: str, project_dir: str) -> tuple:
    """(chars, label) of the largest single code packet or un-packeted named file in the brief."""
    best = (0, "")
    if not prompt:
        return best
    packeted = set()
    heads = list(_PACKET_HDR_RE.finditer(prompt))
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(prompt)
        packeted.add(h.group("path"))
        size = end - h.end()
        if size > best[0]:
            best = (size, h.group("path"))
    root = os.path.realpath(os.path.abspath(project_dir)) if project_dir else ""
    if root:
        for p in extract_brief_paths(prompt):
            if p.startswith(("~", "http")) or p in packeted:
                continue
            target = os.path.realpath(p if os.path.isabs(p) else os.path.join(root, p))
            if not (target == root or target.startswith(root + os.sep)) or not os.path.isfile(target):
                continue
            try:
                size = os.path.getsize(target)
            except OSError:
                continue
            if size > best[0]:
                best = (size, p)
    return best


def check_solid_block(prompt: str, project_dir: str, provider: str, model: str) -> List[str]:
    """Warn (never block) when one big item plus the provider's fixed overhead takes over half the context."""
    chars, label = largest_block(prompt, project_dir)
    if chars <= 0:
        return []
    prov = str(provider or "").lower()
    prov = _PROVIDER_ALIASES.get(prov, prov)
    overhead = PROVIDER_OVERHEAD_TOKENS.get(prov, DEFAULT_OVERHEAD_TOKENS)
    item_tokens = chars // 4
    est = item_tokens + overhead
    window = context_window(prov, model)
    if est > window * SOLID_BLOCK_FRACTION:
        return [f"solid block: {label} is ~{item_tokens} tokens; with ~{overhead} {prov or 'provider'} overhead "
                f"that is ~{est}, over half of the {window}-token context window. "
                f"Use path:symbol~skel or split the work with units=[...]."]
    return []


def run(prompt: str, project_dir: str, provider: str, model: str) -> List[str]:
    results: List[str] = []

    try:
        results.extend(check_brief(prompt, project_dir))
    except Exception:
        pass

    try:
        results.extend(check_provider(provider, model))
    except Exception:
        pass

    try:
        results.extend(check_solid_block(prompt, project_dir, provider, model))
    except Exception:
        pass

    try:
        from worker_digest import lines_for
        brief_paths = extract_brief_paths(prompt)
        digest = lines_for(brief_paths, provider, model=model)
        if digest:
            results.extend(digest)
    except Exception:
        pass

    return results
