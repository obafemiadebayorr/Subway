#!/usr/bin/env python3
"""Read-only ship preflight for release lanes."""

import argparse
import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile


def _parse_version(v_str):
    if not v_str:
        return ()
    v_str = str(v_str).strip().lstrip("v")
    nums = re.findall(r"\d+", v_str)
    return tuple(int(x) for x in nums) if nums else ()


def _run_git(args, cwd):
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def _read_version(worktree):
    version = ""
    pkg_path = os.path.join(worktree, "package.json")
    if os.path.exists(pkg_path):
        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                pkg_data = json.load(f)
                version = str(pkg_data.get("version", "")).strip()
        except Exception:
            version = ""
    return version


def _read_pkg(worktree):
    try:
        with open(os.path.join(worktree, "package.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _check_tests(worktree, run_tests, timeout):
    try:
        if run_tests:
            base = ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
            nice = shutil.which("nice")
            # Low-end laptops: keep the test run below interactive priority.
            cmd = ([nice, "-n", "10"] + base) if nice else base
            proc = subprocess.run(
                cmd,
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            ok = proc.returncode == 0
            lines = [ln.strip() for ln in (proc.stdout + "\n" + proc.stderr).splitlines() if ln.strip()]
            last_line = lines[-1] if lines else "no output"
            failed = [ln for ln in lines if ln.startswith("FAILED ")][:10]
            if failed:
                detail = f"{last_line} ({', '.join(failed)})"
            else:
                detail = last_line
        else:
            ok = True
            detail = "skipped"
        return {"id": "tests", "label": "tests", "ok": ok, "detail": detail}
    except Exception as e:
        return {"id": "tests", "label": "tests", "ok": False, "detail": repr(e)}


def _check_version(worktree, version):
    try:
        if not version:
            return {"id": "version", "label": "version", "ok": False, "detail": "package.json missing or invalid"}
        curr_v = _parse_version(version)
        ok = True
        reasons = []

        # Check main:package.json
        rc_main, out_main, _ = _run_git(["show", "main:package.json"], cwd=worktree)
        if rc_main == 0 and out_main:
            try:
                main_pkg = json.loads(out_main)
                main_v = _parse_version(main_pkg.get("version", ""))
                if main_v and curr_v <= main_v:
                    ok = False
                    reasons.append(f"<= main ({main_pkg.get('version')})")
            except Exception:
                pass

        # Check newest tag
        rc_tag, out_tag, _ = _run_git(["tag", "--list", "v*", "--sort=-v:refname"], cwd=worktree)
        if rc_tag == 0 and out_tag:
            tags = [t.strip() for t in out_tag.splitlines() if t.strip()]
            if tags:
                newest_tag = tags[0]
                tag_v = _parse_version(newest_tag)
                if tag_v and curr_v <= tag_v:
                    ok = False
                    reasons.append(f"<= tag ({newest_tag})")

        if ok:
            detail = f"v{version} (strictly newer)"
        else:
            detail = f"v{version} not newer: {', '.join(reasons)}"
        return {"id": "version", "label": "version", "ok": ok, "detail": detail}
    except Exception as e:
        return {"id": "version", "label": "version", "ok": False, "detail": repr(e)}


def _check_changelog(worktree, version):
    try:
        changelog_path = os.path.join(worktree, "CHANGELOG.md")
        if not os.path.exists(changelog_path):
            return {"id": "changelog", "label": "changelog", "ok": False, "detail": "CHANGELOG.md not found"}
        if not version:
            return {"id": "changelog", "label": "changelog", "ok": False, "detail": "no version to check"}
        with open(changelog_path, "r", encoding="utf-8") as f:
            cl_text = f.read()
        pattern = rf"^#+\s*\[?v?{re.escape(version)}\b"
        if re.search(pattern, cl_text, re.M):
            return {"id": "changelog", "label": "changelog", "ok": True, "detail": f"found v{version}"}
        return {"id": "changelog", "label": "changelog", "ok": False, "detail": f"missing entry for v{version}"}
    except Exception as e:
        return {"id": "changelog", "label": "changelog", "ok": False, "detail": repr(e)}


def _check_docs(worktree):
    try:
        # MCP tool names in server.py
        server_path = os.path.join(worktree, "server.py")
        curr_tools = set()
        if os.path.exists(server_path):
            with open(server_path, "r", encoding="utf-8") as f:
                curr_tools = set(re.findall(r'"name":\s*"([a-z_]+)"', f.read()))

        rc_main_srv, out_main_srv, _ = _run_git(["show", "main:server.py"], cwd=worktree)
        main_tools = set(re.findall(r'"name":\s*"([a-z_]+)"', out_main_srv)) if rc_main_srv == 0 else set()
        new_tools = curr_tools - main_tools

        # settings keys in hud_server/settings_schema.py
        settings_path = os.path.join(worktree, "hud_server", "settings_schema.py")
        curr_settings = set()
        if os.path.exists(settings_path):
            with open(settings_path, "r", encoding="utf-8") as f:
                curr_settings = set(re.findall(r'_e\("([a-z0-9_]+)"', f.read()))

        rc_main_set, out_main_set, _ = _run_git(["show", "main:hud_server/settings_schema.py"], cwd=worktree)
        main_settings = set(re.findall(r'_e\("([a-z0-9_]+)"', out_main_set)) if rc_main_set == 0 else set()
        new_settings = curr_settings - main_settings

        added_names = sorted(new_tools | new_settings)

        # Collect docs content
        docs_content = []
        readme_path = os.path.join(worktree, "README.md")
        if os.path.exists(readme_path):
            with open(readme_path, "r", encoding="utf-8") as f:
                docs_content.append(f.read())
        for doc_file in glob.glob(os.path.join(worktree, "docs", "*.md")):
            try:
                with open(doc_file, "r", encoding="utf-8") as f:
                    docs_content.append(f.read())
            except Exception:
                pass
        combined_docs = "\n".join(docs_content)

        missing = [name for name in added_names if name not in combined_docs]
        if not missing:
            detail = f"all {len(added_names)} added names documented" if added_names else "no added names"
            return {"id": "docs", "label": "docs", "ok": True, "detail": detail}
        return {"id": "docs", "label": "docs", "ok": False, "detail": f"missing: {', '.join(missing)}"}
    except Exception as e:
        return {"id": "docs", "label": "docs", "ok": False, "detail": repr(e)}


def _check_mirror(worktree):
    try:
        mirror_script = os.path.join(worktree, "scripts", "publish_mirror.py")
        if not os.path.exists(mirror_script):
            return {"id": "mirror", "label": "mirror", "ok": False, "detail": "scripts/publish_mirror.py not found"}
        spec = importlib.util.spec_from_file_location("publish_mirror", mirror_script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        files = mod.public_files(worktree)

        bad = []
        for p in files:
            p_norm = p.replace("\\", "/")
            filename = p_norm.split("/")[-1]
            is_test_file = (filename.startswith("test_") and filename.endswith(".py")) or filename == "test_*.py"
            if p_norm.startswith(("tests/", "docs/", "plan/", ".subway-")) or "/test_" in p_norm or is_test_file:
                bad.append(p)

        if not bad:
            return {"id": "mirror", "label": "mirror", "ok": True, "detail": f"{len(files)} files"}
        detail = f"{len(files)} files, bad: {', '.join(bad[:20])}"
        return {"id": "mirror", "label": "mirror", "ok": False, "detail": detail}
    except Exception as e:
        return {"id": "mirror", "label": "mirror", "ok": False, "detail": repr(e)}


def _check_clean(worktree):
    try:
        rc_clean, out_clean, _ = _run_git(["status", "--porcelain"], cwd=worktree)
        if rc_clean != 0:
            return {"id": "clean", "label": "clean", "ok": False, "detail": f"git status failed: {out_clean}"}
        dirty = [ln for ln in out_clean.splitlines() if ln.strip() and not ln.startswith("??")]
        if not dirty:
            return {"id": "clean", "label": "clean", "ok": True, "detail": "clean"}
        return {"id": "clean", "label": "clean", "ok": False, "detail": f"{len(dirty)} uncommitted: {', '.join(dirty[:10])}"}
    except Exception as e:
        return {"id": "clean", "label": "clean", "ok": False, "detail": repr(e)}


def _ruff_pin(worktree):
    """Parse the pinned ruff version (`ruff==X.Y.Z`) from .github/workflows/pr.yml."""
    wf = os.path.join(worktree, ".github", "workflows", "pr.yml")
    try:
        with open(wf, "r", encoding="utf-8") as f:
            m = re.search(r"ruff==(\d+\.\d+\.\d+)", f.read())
        return m.group(1) if m else None
    except OSError:
        return None


def _check_ruff(worktree):
    try:
        pin = _ruff_pin(worktree)
        if not pin:
            return {"id": "ruff", "label": "ruff", "ok": False,
                    "detail": "no ruff==X.Y.Z pin found in .github/workflows/pr.yml"}
        if shutil.which("uvx"):
            cmd = ["uvx", f"ruff@{pin}", "check", "."]
        else:
            cmd = ["python3", "-m", "ruff", "check", "."]
        try:
            proc = subprocess.run(cmd, cwd=worktree, capture_output=True, text=True, timeout=600)
        except FileNotFoundError:
            return {"id": "ruff", "label": "ruff", "ok": False,
                    "detail": f"ruff not found; install with: pip install ruff=={pin}"}
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        if proc.returncode == 0:
            return {"id": "ruff", "label": "ruff", "ok": True, "detail": "clean"}
        if "No module named ruff" in out or "No module named 'ruff'" in out:
            return {"id": "ruff", "label": "ruff", "ok": False,
                    "detail": f"ruff not found; install with: pip install ruff=={pin}"}
        lines = [ln.rstrip() for ln in out.splitlines() if ln.strip()]
        detail = "\n".join(lines[:5]) if lines else f"ruff exited {proc.returncode}"
        return {"id": "ruff", "label": "ruff", "ok": False, "detail": detail}
    except Exception as e:
        return {"id": "ruff", "label": "ruff", "ok": False, "detail": repr(e)}


def _missing_packed_files(worktree):
    """Top-level tracked *.py (and dirs) imported anywhere but missing from package.json 'files'."""
    pkg = _read_pkg(worktree)
    files = pkg.get("files")
    if not isinstance(files, list):
        return []
    files = set(files)
    rc, out, _ = _run_git(["ls-files"], cwd=worktree)
    if rc != 0:
        return []
    tracked = [ln.strip() for ln in out.splitlines() if ln.strip()]
    top_py = {t for t in tracked if "/" not in t and t.endswith(".py")}
    top_dirs = {t.split("/")[0] for t in tracked if "/" in t}
    imported = set()
    pat = re.compile(r"^(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
    for t in tracked:
        if not t.endswith(".py"):
            continue
        p = os.path.join(worktree, t)
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                imported |= set(pat.findall(f.read()))
        except OSError:
            continue
    missing = []
    for name in sorted(imported):
        mod = name + ".py"
        if mod in top_py and mod not in files:
            missing.append(mod)
        elif name in top_dirs and name not in files:
            missing.append(name + "/")
    return missing


def _check_packed_smoke(worktree):
    tmp = tempfile.mkdtemp(prefix="subway_pack_smoke_")
    try:
        proc = subprocess.run(["npm", "pack", "--pack-destination", tmp],
                              cwd=worktree, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:300]
            return {"id": "packed_smoke", "label": "packed_smoke", "ok": False,
                    "detail": f"npm pack failed: {err}"}
        tgz = None
        for ln in proc.stdout.splitlines():
            ln = ln.strip()
            if ln.endswith(".tgz"):
                tgz = ln
        if not tgz or not os.path.exists(os.path.join(tmp, tgz)):
            cands = sorted(glob.glob(os.path.join(tmp, "*.tgz")))
            tgz = os.path.basename(cands[0]) if cands else None
        if not tgz:
            return {"id": "packed_smoke", "label": "packed_smoke", "ok": False,
                    "detail": "npm pack produced no .tgz"}
        subprocess.run(["tar", "-xzf", os.path.join(tmp, tgz), "-C", tmp],
                       check=True, capture_output=True, timeout=120)
        imp = subprocess.run(
            ["python3", "-c", "import sys; sys.path.insert(0,'package'); import server, hud_server.handlers"],
            cwd=tmp, capture_output=True, text=True, timeout=300)
        smoke_ok = imp.returncode == 0
        html_ok = os.path.isfile(os.path.join(tmp, "package", "hud_next", "index.html"))
        missing = _missing_packed_files(worktree)
        parts = []
        if not smoke_ok:
            err_tail = (imp.stderr or imp.stdout or "").strip().splitlines()
            parts.append("import failed: " + (err_tail[-1] if err_tail else f"exit {imp.returncode}"))
        if not html_ok:
            parts.append("package/hud_next/index.html missing")
        if missing:
            parts.append("missing from files: " + ", ".join(missing))
        ok = smoke_ok and html_ok and not missing
        detail = "; ".join(parts) if parts else "packed install imports cleanly"
        return {"id": "packed_smoke", "label": "packed_smoke", "ok": ok, "detail": detail}
    except Exception as e:
        return {"id": "packed_smoke", "label": "packed_smoke", "ok": False, "detail": repr(e)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _check_npm_auth_hint(worktree):
    # Read-only hint; never makes a network call. ok is always True.
    try:
        name = str(_read_pkg(worktree).get("name", "")).strip()
    except Exception:
        name = ""
    try:
        if name.startswith("@"):
            detail = (f"scoped package {name}: a trusted publisher must exist on npmjs.com "
                      f"for this repo before publishing (no network check performed)")
        else:
            detail = f"package {name or 'unknown'} is unscoped; no trusted-publisher reminder"
        return {"id": "npm_auth_hint", "label": "npm_auth_hint", "ok": True, "detail": detail}
    except Exception as e:
        return {"id": "npm_auth_hint", "label": "npm_auth_hint", "ok": True, "detail": f"unreadable package.json ({e!r})"}


def run(worktree, run_tests=True, timeout=900, only=None):
    worktree = os.path.abspath(worktree)
    version = _read_version(worktree)

    builders = [
        ("tests", lambda: _check_tests(worktree, run_tests, timeout)),
        ("version", lambda: _check_version(worktree, version)),
        ("changelog", lambda: _check_changelog(worktree, version)),
        ("docs", lambda: _check_docs(worktree)),
        ("mirror", lambda: _check_mirror(worktree)),
        ("clean", lambda: _check_clean(worktree)),
        ("ruff", lambda: _check_ruff(worktree)),
        ("packed_smoke", lambda: _check_packed_smoke(worktree)),
        ("npm_auth_hint", lambda: _check_npm_auth_hint(worktree)),
    ]

    checks = []
    for check_id, build in builders:
        if only is not None and check_id not in only:
            continue
        checks.append(build())

    overall_ok = all(c["ok"] for c in checks)
    return {
        "ok": overall_ok,
        "version": version,
        "checks": checks,
    }


def main():
    parser = argparse.ArgumentParser(description="Read-only ship preflight for release lanes.")
    parser.add_argument("worktree", nargs="?", default=".", help="Worktree directory (default: .)")
    parser.add_argument("--no-tests", action="store_true", help="Skip running unit tests")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    result = run(args.worktree, run_tests=not args.no_tests)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for c in result["checks"]:
            sym = "✓" if c["ok"] else "✗"
            print(f"{sym} {c['label']} — {c['detail']}")

    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
