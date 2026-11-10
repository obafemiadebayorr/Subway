import os
import re
import json
import shutil
from typing import List, Optional

# Match path-like tokens: relative paths, paths with ~, /, or http/https
# e.g., foo.py, src/app.ts:func, ~/file.py, /path/to/file.py, http://example.com/foo.js
PATH_TOKEN_RE = re.compile(
    r"(?<![\w/])(?P<token>(?:https?://\S+|~[^\s]*|/\S*)?(?:[\w.-]+/)*[\w.-]+\.(?:py|js|mjs|ts|css|md|json|html)(?::[\w.]+)?)"
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
            start_pos = m.start()
            before_40 = prompt[max(0, start_pos - 40):start_pos].lower()
            if "new" in before_40 or "create" in before_40:
                continue
            warn = f"missing path: {rel_path}"
            if warn not in warnings:
                warnings.append(warn)
        else:
            # File exists; check path:symbol
            if sym_part:
                sym_name = sym_part.split(".")[-1]
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
        if not shutil.which(bin_name):
            warnings.append(f"provider CLI not found: {provider}")

    # (b) model listed in ~/.subway-context/bad_models.json (read it; it's JSON — handle dict or list)
    if model:
        subway_dir = os.environ.get("SUBWAY_DIR") or os.path.expanduser("~/.subway-context")
        bad_models_path = os.path.join(subway_dir, "bad_models.json")
        if not os.path.exists(bad_models_path):
            bad_models_path = os.path.expanduser("~/.subway-context/bad_models.json")

        if os.path.exists(bad_models_path):
            try:
                with open(bad_models_path, "r", encoding="utf-8") as f:
                    bad_data = json.load(f)
                is_bad = False
                if isinstance(bad_data, list):
                    is_bad = model in bad_data
                elif isinstance(bad_data, dict):
                    is_bad = (model in bad_data or
                              model in bad_data.get("models", []) or
                              model in bad_data.get("bad_models", []))
                if is_bad:
                    warnings.append(f"model marked bad: {model}")
            except Exception:
                pass

    return warnings


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
        from worker_digest import lines_for
        brief_paths = extract_brief_paths(prompt)
        digest = lines_for(brief_paths, provider, model=model)
        if digest:
            results.extend(digest)
    except Exception:
        pass

    return results
