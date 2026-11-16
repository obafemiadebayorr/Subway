from __future__ import annotations

import glob
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

LINE_ORDER = ["claude", "agy", "codex", "cursor", "copilot", "opencode", "pi"]
LINE_PRIORITY = {n: i for i, n in enumerate(LINE_ORDER)}
LINE_ALIASES = {".claude": "claude", "Claude": "claude-desktop", ".gemini": "agy", ".copilot": "copilot",
                ".codex": "codex", ".cursor": "cursor", "opencode": "opencode", ".pi": "pi", ".agents": "agents"}
_PRUNE = {".trash", ".Trash", "node_modules", ".git", "sessions", "brain", "logs", "tmp", "__pycache__", "worktrees"}
_SKIP_HOME = {".nvm", ".npm", ".cache", ".cargo", ".rustup", ".gradle", ".m2", ".docker", ".Trash", ".local",
              ".vscode", ".oh-my-zsh", ".pyenv", ".bun", ".deno", ".conda", ".config", ".git"}
_INSTR_DIRS = {"agents": "agent", "agent": "agent", "commands": "command", "command": "command", "rules": "rule"}
_CACHE: Dict[Tuple[str, str], Tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_TTL = 60.0
_BODY_TRUNC_MARKER = "[truncated to 12000 chars]"


def _home() -> str:
    return os.path.expanduser("~")


def _mtime_iso(path: str) -> str:
    try:
        ts = os.path.getmtime(path)
    except OSError:
        ts = 0.0
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    fm: Dict[str, str] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end].strip("\n")
            body = text[end + 4 :]
            for line in block.splitlines():
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                k = k.strip().lower()
                v = v.strip().strip('"').strip("'")
                if k in ("name", "description", "version"):
                    fm[k] = v
    return fm, body


def _first_paragraph(text: str) -> str:
    # text is body after frontmatter
    lines = text.splitlines()
    para: List[str] = []
    started = False
    for line in lines:
        s = line.strip()
        if not started:
            if not s:
                continue
            if s.startswith("#"):
                continue
            started = True
            para.append(s)
            continue
        if not s:
            break
        if s.startswith("#"):
            break
        para.append(s)
    return " ".join(para).strip()


def _nearest_plugin_version(start_dir: str, stop_at: str) -> Optional[str]:
    cur = os.path.abspath(start_dir)
    stop_at = os.path.abspath(stop_at)
    while True:
        cand = os.path.join(cur, "plugin.json")
        if os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    data = json.load(f)
                v = data.get("version")
                if v is not None:
                    return str(v)
            except Exception:
                pass
        if cur == stop_at or cur == os.path.dirname(cur):
            break
        parent = os.path.dirname(cur)
        if not parent.startswith(stop_at) and parent != stop_at:
            # don't go above stop_at
            if cur == stop_at:
                break
        cur = parent
        if stop_at and not cur.startswith(stop_at) and cur != stop_at:
            break
    return None


def _walk_depth_limited(base: str, max_depth: int) -> List[str]:
    """Return directories up to max_depth levels below base (base's children depth=1)."""
    out: List[str] = []
    base = os.path.abspath(base)
    if not os.path.isdir(base):
        return out
    for root, dirs, _files in os.walk(base):
        rel = os.path.relpath(root, base)
        if rel == ".":
            depth = 0
        else:
            depth = len(rel.split(os.sep))
        if depth > max_depth:
            dirs[:] = []
            continue
        if depth >= 1:
            out.append(root)
        if depth >= max_depth:
            dirs[:] = []
    return out


def _collect_scripts(skill_dir: str, limit: int = 20) -> List[str]:
    exts = (".py", ".sh", ".js", ".mjs")
    found: List[str] = []
    for root, _dirs, files in os.walk(skill_dir):
        for fn in sorted(files):
            if fn.endswith(exts):
                found.append(os.path.abspath(os.path.join(root, fn)))
                if len(found) >= limit:
                    return found
    return found


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _make_entry(path: str, line: str, kind: str, skill_dir: str,
                plugin_search_root: Optional[str] = None) -> Dict[str, Any]:
    text = _read_text(path)
    fm, body = _parse_frontmatter(text)
    dir_name = os.path.basename(skill_dir.rstrip(os.sep))
    name = fm.get("name") or dir_name
    desc = fm.get("description") or _first_paragraph(body)
    version = fm.get("version")
    if not version and plugin_search_root:
        version = _nearest_plugin_version(skill_dir, plugin_search_root)
    if not version:
        version = _mtime_iso(path)
    scripts = _collect_scripts(skill_dir) if kind == "skill" else []
    return {
        "name": name,
        "description": desc,
        "line": line,
        "kind": kind,
        "path": os.path.abspath(path),
        "dir": os.path.abspath(skill_dir),
        "version": version,
        "scripts": scripts,
    }


def _discover_static(project_dir: str = "") -> List[Dict[str, Any]]:
    home = _home()
    entries: List[Dict[str, Any]] = []

    # ~/.claude/skills/*/SKILL.md
    base = os.path.join(home, ".claude", "skills")
    for d in _walk_depth_limited(base, 1):  # */SKILL.md => skill dirs at depth 1
        # only direct children
        if os.path.dirname(d) != os.path.abspath(base):
            continue
        p = os.path.join(d, "SKILL.md")
        if os.path.isfile(p):
            entries.append(_make_entry(p, "claude", "skill", d))

    # ~/.claude/plugins/**/skills/*/SKILL.md (depth-limited max 4 overall under plugins)
    base = os.path.join(home, ".claude", "plugins")
    if os.path.isdir(base):
        for root, dirs, files in os.walk(base):
            rel = os.path.relpath(root, base)
            depth = 0 if rel == "." else len(rel.split(os.sep))
            if depth > 4:
                dirs[:] = []
                continue
            # match .../skills/<name>/SKILL.md
            if os.path.basename(root) and "skills" in rel.split(os.sep):
                parts = rel.split(os.sep)
                # want pattern skills/<something> at this root
                try:
                    idx = parts.index("skills")
                except ValueError:
                    idx = -1
                if idx != -1 and len(parts) == idx + 2 and "SKILL.md" in files:
                    d = root
                    p = os.path.join(d, "SKILL.md")
                    entries.append(_make_entry(p, "claude", "skill", d, plugin_search_root=base))
            if depth >= 4:
                dirs[:] = []

    # <project>/.claude/skills/*/SKILL.md
    if project_dir:
        base = os.path.join(project_dir, ".claude", "skills")
        if os.path.isdir(base):
            for name in os.listdir(base):
                d = os.path.join(base, name)
                p = os.path.join(d, "SKILL.md")
                if os.path.isdir(d) and os.path.isfile(p):
                    entries.append(_make_entry(p, "claude", "skill", d))

    # ~/.gemini/config/skills/*/SKILL.md
    base = os.path.join(home, ".gemini", "config", "skills")
    if os.path.isdir(base):
        for name in os.listdir(base):
            d = os.path.join(base, name)
            p = os.path.join(d, "SKILL.md")
            if os.path.isdir(d) and os.path.isfile(p):
                entries.append(_make_entry(p, "agy", "skill", d))

    # ~/.gemini/config/plugins/*/skills/*/SKILL.md
    base = os.path.join(home, ".gemini", "config", "plugins")
    if os.path.isdir(base):
        for root, dirs, files in os.walk(base):
            rel = os.path.relpath(root, base)
            depth = 0 if rel == "." else len(rel.split(os.sep))
            if depth > 4:
                dirs[:] = []
                continue
            parts = rel.split(os.sep) if rel != "." else []
            if "skills" in parts:
                try:
                    idx = parts.index("skills")
                except ValueError:
                    idx = -1
                if idx != -1 and len(parts) == idx + 2 and "SKILL.md" in files:
                    d = root
                    p = os.path.join(d, "SKILL.md")
                    entries.append(_make_entry(p, "agy", "skill", d, plugin_search_root=base))
            if depth >= 4:
                dirs[:] = []

    # ~/.gemini/antigravity/builtin/skills/*/SKILL.md and antigravity-ide (skip antigravity-cli)
    for variant in ("antigravity", "antigravity-ide"):
        base = os.path.join(home, ".gemini", variant, "builtin", "skills")
        if os.path.isdir(base):
            for name in os.listdir(base):
                d = os.path.join(base, name)
                p = os.path.join(d, "SKILL.md")
                if os.path.isdir(d) and os.path.isfile(p):
                    entries.append(_make_entry(p, "agy", "skill", d))

    # ~/.config/opencode/agents/*.md (kind='agent')
    base = os.path.join(home, ".config", "opencode", "agents")
    if os.path.isdir(base):
        for fn in os.listdir(base):
            if not fn.endswith(".md"):
                continue
            p = os.path.join(base, fn)
            if os.path.isfile(p):
                d = os.path.dirname(p)
                # agent: use file stem as dir/name fallback context, scripts empty
                entries.append(_make_entry(p, "opencode", "agent", d))

    return entries


def _line_of(root: str) -> str:
    base = os.path.basename(root.rstrip(os.sep))
    return LINE_ALIASES.get(base) or base.lstrip(".").lower() or "other"


def _config_skill_roots() -> List[str]:
    """Extra roots from Subway's config.json `skill_roots`."""
    for sd in (os.environ.get("SUBWAY_DIR") or "", os.path.join(_home(), ".subway-context"), os.path.join(_home(), ".subway")):
        p = os.path.join(sd, "config.json") if sd else ""
        if p and os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    v = json.load(fh).get("skill_roots") or []
                return [os.path.expanduser(str(x)) for x in v if str(x).strip()]
            except Exception:
                return []
    return []


def _provider_roots() -> List[Tuple[str, int]]:
    """(root, dir budget): every dot-dir in HOME, every dir in ~/.config and ~/Library/Application Support, plus config roots."""
    home = _home()
    out: List[Tuple[str, int]] = []
    for parent, budget, dots_only in ((home, 5000, True), (os.path.join(home, ".config"), 3000, False),
                                      (os.path.join(home, "Library", "Application Support"), 1500, False)):
        try:
            names = sorted(os.listdir(parent))
        except OSError:
            continue
        for n in names:
            if dots_only and (not n.startswith(".") or n in _SKIP_HOME or n in _PRUNE or n.startswith(".subway")):
                continue
            p = os.path.join(parent, n)
            if os.path.isdir(p) and not os.path.islink(p):
                out.append((p, budget))
    out.extend((r, 5000) for r in _config_skill_roots() if os.path.isdir(r))
    return out


def _enabled_plugin_dirs() -> List[str]:
    """Plugin dirs an app's own manifest enables (Claude: settings enabledPlugins + installed_plugins.json)."""
    home = _home()
    base = os.path.join(home, ".claude", "plugins")
    out: List[str] = []
    try:
        with open(os.path.join(home, ".claude", "settings.json"), "r", encoding="utf-8") as fh:
            en = json.load(fh).get("enabledPlugins") or {}
    except Exception:
        en = {}
    for key, on in (en.items() if isinstance(en, dict) else []):
        if not on or "@" not in str(key):
            continue
        name, mkt = str(key).split("@", 1)
        for sub in ("plugins", "external_plugins"):
            p = os.path.join(base, "marketplaces", mkt, sub, name)
            if os.path.isdir(p):
                out.append(p)
    try:
        with open(os.path.join(base, "installed_plugins.json"), "r", encoding="utf-8") as fh:
            inst = json.load(fh).get("plugins") or {}
        for recs in inst.values():
            for r in (recs if isinstance(recs, list) else [recs]):
                ip = (r or {}).get("installPath") if isinstance(r, dict) else None
                if ip:
                    out.append(os.path.expanduser(ip))
    except Exception:
        pass
    return [os.path.abspath(p) for p in out]


def _entry_for(path: str, line: str, kind: str, d: str, root: str, enabled: List[str]) -> Dict[str, Any]:
    import hashlib
    e = _make_entry(path, line, kind, d, plugin_search_root=root)
    ap = e["path"]
    parts = ap.split(os.sep)
    if kind != "skill" and e["name"] == os.path.basename(d.rstrip(os.sep)):
        e["name"] = os.path.splitext(os.path.basename(path))[0]
    in_catalog = any(p in ("marketplaces", "catalog") for p in parts)
    e["installed"] = (not in_catalog) or any(ap.startswith(x + os.sep) for x in enabled)
    if "synced" in parts or "skills-plugin" in parts:
        e["name"] = "anthropic-skills:" + str(e["name"]).split(":")[-1]
    else:
        for marker in ("external_plugins", "plugins"):
            if marker in parts:
                i = len(parts) - 1 - parts[::-1].index(marker)
                plug = parts[i + 3] if i + 3 < len(parts) and parts[i + 1] == "cache" else (parts[i + 1] if i + 1 < len(parts) else "")
                if plug and plug not in ("skills", e["name"]) and ":" not in str(e["name"]) and i + 2 < len(parts) - 1:
                    e["name"] = f"{plug}:{e['name']}"
                break
    e["hash"] = hashlib.sha1(_read_text(path).encode("utf-8", "replace")).hexdigest()[:16]
    return e


def _scan_root(root: str, line: str, enabled: List[str], budget: int = 5000) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    base_depth = root.rstrip(os.sep).count(os.sep)
    seen = 0
    for cur, dirs, files in os.walk(root, followlinks=False):
        seen += 1
        if seen > budget:
            break
        depth = cur.count(os.sep) - base_depth
        dirs[:] = [x for x in dirs if x not in _PRUNE and not os.path.islink(os.path.join(cur, x))]
        if depth >= 12:
            dirs[:] = []
        if "SKILL.md" in files:
            out.append(_entry_for(os.path.join(cur, "SKILL.md"), line, "skill", cur, root, enabled))
        kind = _INSTR_DIRS.get(os.path.basename(cur))
        if kind and depth <= 2:
            for f in sorted(files):
                if f.endswith(".md") and f != "SKILL.md" and not f.upper().startswith("README"):
                    out.append(_entry_for(os.path.join(cur, f), line, kind, cur, root, enabled))
    return out


def _discover(project_dir: str = "") -> List[Dict[str, Any]]:
    """Dynamic: scan every provider root found on this machine (no fixed path list)."""
    enabled = _enabled_plugin_dirs()
    roots = _provider_roots()
    if project_dir and os.path.isdir(os.path.join(project_dir, ".claude")):
        roots.append((os.path.join(project_dir, ".claude"), 2000))
    done = set()
    out: List[Dict[str, Any]] = []
    for r, budget in roots:
        ar = os.path.abspath(r)
        if ar in done:
            continue
        done.add(ar)
        out.extend(_scan_root(ar, _line_of(ar), enabled, budget))
    return out


def _cache_key(project_dir: str) -> Tuple[str, str]:
    return (_home(), os.path.abspath(project_dir) if project_dir else "")


def index_skills(project_dir: str = "", refresh: bool = False, all: bool = False) -> List[Dict[str, Any]]:
    key = _cache_key(project_dir)
    now = time.time()
    if not refresh:
        cached = _CACHE.get(key)
        if cached and (now - cached[0]) < _CACHE_TTL:
            all_entries = cached[1]
        else:
            all_entries = _discover(project_dir)
            _CACHE[key] = (now, all_entries)
    else:
        all_entries = _discover(project_dir)
        _CACHE[key] = (now, all_entries)

    def rank(e: Dict[str, Any]) -> Tuple[int, int]:
        return (0 if e.get("installed", True) else 1, LINE_PRIORITY.get(e["line"], 99))
    by_hash: Dict[str, Dict[str, Any]] = {}
    for e in all_entries:
        if not all and not e.get("installed", True):
            continue
        h = e.get("hash") or e["path"]
        cur = by_hash.get(h)
        if cur is None or rank(e) < rank(cur):
            by_hash[h] = dict(e, also=((cur.get("also") or []) + [cur["path"]]) if cur else [])
        else:
            cur.setdefault("also", []).append(e["path"])
    best: Dict[str, Dict[str, Any]] = {}
    for e in by_hash.values():
        cur = best.get(e["name"])
        if cur is None or rank(e) < rank(cur):
            best[e["name"]] = e
    return [best[k] for k in sorted(best.keys())]


def find_skill(name: str, line: str = "") -> Optional[Dict[str, Any]]:
    # search across all cached discoveries? need to find even non-primary ones
    # try all cache entries; if none, do a default discovery
    candidates: List[Dict[str, Any]] = []
    if _CACHE:
        for _, (_, entries) in _CACHE.items():
            candidates.extend(entries)
    else:
        index_skills()
        for _, (_, entries) in _CACHE.items():
            candidates.extend(entries)
    matches = [e for e in candidates if e["name"] == name] or \
              [e for e in candidates if str(e["name"]).split(":")[-1] == name]
    if not matches:
        # try refreshless index to populate
        candidates = _discover("")
        matches = [e for e in candidates if e["name"] == name]
    if not matches:
        return None
    if line:
        for e in matches:
            if e["line"] == line:
                return e
        return None
    # return best by priority
    matches.sort(key=lambda e: LINE_PRIORITY.get(e["line"], 99))
    return matches[0]


def read_skill_body(entry: Dict[str, Any], cap: int = 12000) -> str:
    path = entry.get("path", "")
    text = _read_text(path)
    if len(text) > cap:
        return text[:cap] + "\n" + _BODY_TRUNC_MARKER
    return text


def _all_entries(project_dir: str = "") -> List[Dict[str, Any]]:
    """Every discovered entry, not deduped (index_skills keeps one per name)."""
    try:
        return list(_discover(project_dir))
    except Exception:
        return []


def t_list_skills(query: str = "", line: str = "") -> str:
    """Skills this station can lend, across every provider line."""
    if str(line or "").startswith("@"):
        return list_remote(str(line)[1:], query)
    q = str(query or "").strip().lower()
    ln = str(line or "").strip().lower()
    rows = _all_entries() if ln else index_skills()
    out = []
    for e in rows:
        if ln and str(e.get("line") or "").lower() != ln:
            continue
        desc = " ".join(str(e.get("description") or "").split())
        if q and q not in str(e.get("name") or "").lower() and q not in desc.lower():
            continue
        out.append(f"- **{e.get('name')}** ({e.get('line')}, {e.get('version') or '?'}): {desc[:120]}")
    if not out:
        return "No skills match." + (f" (line={line})" if ln else "")
    more = f"\n…and {len(out) - 60} more; narrow with query=." if len(out) > 60 else ""
    return f"{len(out)} skills:\n" + "\n".join(out[:60]) + more


def borrow_block(e: Dict[str, Any]) -> str:
    body = read_skill_body(e)
    scripts = e.get("scripts") or []
    head = f"=== BORROWED SKILL: {e.get('name')} (source: {e.get('line')}, path: {e.get('path')}, version: {e.get('version') or '?'}) ==="
    tail = ("\nScripts (local paths):\n" + "\n".join("- " + s for s in scripts)) if scripts else ""
    return f"{head}\n{body}{tail}"


def borrow_remote(name: str, station: str, line: str = "") -> str:
    """Borrow a skill from a linked station: instructions only, scripts stay there."""
    import urllib.parse
    try:
        import peers
    except Exception:
        return "Error: station links are unavailable."
    st = str(station or "").lstrip("@").strip()
    peer = peers.by_slug(st)
    if not peer:
        return f"Error: no linked station called {st}."
    q = "/api/peer/skill?name=" + urllib.parse.quote(name) + ("&line=" + urllib.parse.quote(line) if line else "")
    res = peers.call(peer["peer_id"], "GET", q, timeout=15)
    status = res.get("status")
    if status == 403:
        return f"Error: {st} has not granted skills to this station."
    if status == 404:
        return f"Error: {st} has no skill called {name}."
    if status != 200:
        return f"Error: {st} is offline." if not status else f"Error: {st} answered {status}."
    d = res.get("data") or {}
    head = f"=== BORROWED SKILL: {d.get('name') or name} (source: {d.get('line') or '?'} on {st}, version: {d.get('version') or '?'}) ==="
    return f"{head}\n{d.get('body') or ''}\nScripts were not transferred: instructions only."


def list_remote(station: str, query: str = "", line: str = "") -> str:
    import urllib.parse
    try:
        import peers
    except Exception:
        return "Error: station links are unavailable."
    st = str(station or "").lstrip("@").strip()
    peer = peers.by_slug(st)
    if not peer:
        return f"Error: no linked station called {st}."
    q = "/api/peer/skills?query=" + urllib.parse.quote(query or "") + ("&line=" + urllib.parse.quote(line) if line else "")
    res = peers.call(peer["peer_id"], "GET", q, timeout=15)
    if res.get("status") == 403:
        return f"Error: {st} has not granted skills to this station."
    if res.get("status") != 200:
        return f"Error: {st} is offline." if not res.get("status") else f"Error: {st} answered {res.get('status')}."
    rows = (res.get("data") or {}).get("skills") or []
    if not rows:
        return f"{st} has no matching skills."
    out = [f"- **{r.get('name')}@{st}** ({r.get('line')}, {r.get('version') or '?'}): {' '.join(str(r.get('description') or '').split())[:120]}" for r in rows[:60]]
    return f"{len(rows)} skills on {st}:\n" + "\n".join(out)


def t_borrow_skill(name: str, line: str = "") -> str:
    """Hand a skill's instructions (and its local script paths) to this chat."""
    nm = str(name or "").strip()
    if not nm:
        return "Error: name is required."
    if "@" in nm:
        sk, _, st = nm.rpartition("@")
        return borrow_remote(sk, st, line)
    e = find_skill(nm, line=line)
    if not e:
        import difflib
        names = sorted({str(x.get("name") or "") for x in index_skills()})
        close = difflib.get_close_matches(nm, names, n=5, cutoff=0.5)
        return f"Error: no skill called {nm}." + (f" Close: {', '.join(close)}" if close else "")
    return borrow_block(e)


def skills_rows(entries, needle: str = ""):
    """Trim index entries for the HUD skills list (no paths)."""
    needle = (needle or "").strip().lower()
    rows = []
    for e in entries:
        if needle and needle not in (str(e.get("name", "")) + " " + str(e.get("description", ""))).lower():
            continue
        rows.append({"name": e.get("name"), "line": e.get("line"), "kind": e.get("kind"),
                     "installed": bool(e.get("installed", True)),
                     "description": (e.get("description") or "")[:240],
                     "copies": 1 + len(e.get("also") or [])})
    return rows
