import os
import re
import secrets
from pathlib import Path


def _get_subway_dir() -> Path:
    subway_dir = os.environ.get("SUBWAY_DIR")
    if subway_dir:
        return Path(subway_dir)
    return Path.home() / ".subway-context"


def _parse_pass_summary(cmd: str, out: str) -> str:
    # 1. pytest: "N passed[, M skipped] in Xs"
    # Matches: "5 passed in 0.12s", "5 passed, 2 skipped in 1.23s", "1 passed in 10.5s"
    m_pytest = re.search(r'(\d+\s+passed(?:,\s+\d+\s+skipped)?\s+in\s+[\d\.]+s)', out)
    if m_pytest:
        return m_pytest.group(1)

    # 2. vitest/jest: "Tests N passed" or "Tests: N passed" -> "Tests N passed"
    m_jest = re.search(r'Tests:\s+(\d+\s+passed)', out)
    if m_jest:
        return f"Tests {m_jest.group(1)}"
    m_jest2 = re.search(r'Tests\s+(\d+\s+passed)', out)
    if m_jest2:
        return f"Tests {m_jest2.group(1)}"

    # 3. cargo test: "test result: ok. N passed"
    m_cargo = re.search(r'test result:\s*ok\.\s*(\d+\s+passed)', out)
    if m_cargo:
        return f"test result: ok. {m_cargo.group(1)}"

    # 4. npm/node/other: "exit 0, <n> lines"
    lines = out.splitlines()
    return f"exit 0, {len(lines)} lines"


def _is_external_frame(line: str) -> bool:
    external_markers = (
        "site-packages/",
        "dist-packages/",
        "node_modules/",
        "/lib/python",
        "<frozen",
    )
    return any(marker in line for marker in external_markers)


def _is_passing_or_progress(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    # Progress dots e.g. "....F...", "...", ".."
    if re.fullmatch(r'[\.sFeExX\+]+', stripped) and len(stripped) > 2 and stripped.count('.') > 0:
        return True
    if stripped in ('.', '..', '...'):
        return True
    # Passing test lines: "PASS ...", "test_foo ... ok", "test_bar ... PASSED"
    if stripped.startswith("PASS ") or re.search(r'\.\.\.\s*(?:ok|PASSED)$', stripped):
        return True
    return False


def _filter_fail_output(cmd: str, out: str) -> list[str]:
    raw_lines = out.splitlines()
    filtered: list[str] = []

    # Check if this looks like pytest or jest/vitest output
    is_pytest = "pytest" in cmd or any(l.startswith("FAILED ") or "short test summary info" in l for l in raw_lines)
    is_jest = any(l.startswith("●") for l in raw_lines) or "jest" in cmd or "vitest" in cmd

    if is_jest:
        in_bullet_block = False
        for line in raw_lines:
            if _is_passing_or_progress(line):
                continue
            if line.startswith("●"):
                in_bullet_block = True
                filtered.append(line)
                continue
            if in_bullet_block:
                if line.startswith("Summary") or line.startswith("Test Suites:"):
                    in_bullet_block = False
                    filtered.append(line)
                elif _is_external_frame(line):
                    continue
                else:
                    filtered.append(line)
            elif any(marker in line for marker in ("FAIL ", "Tests:", "Snapshots:", "Test Suites:")):
                filtered.append(line)
        if filtered:
            return filtered

    if is_pytest:
        in_short_summary = False
        in_assertion = False
        for line in raw_lines:
            if _is_passing_or_progress(line):
                continue
            if _is_external_frame(line):
                continue
            if line.startswith("FAILED "):
                filtered.append(line)
            elif line.startswith("E   "):
                filtered.append(line)
            elif "short test summary info" in line:
                in_short_summary = True
                filtered.append(line)
            elif in_short_summary:
                if line.startswith("=") and ("failed" in line or "passed" in line or "error" in line):
                    filtered.append(line)
                    in_short_summary = False
                else:
                    filtered.append(line)
            elif line.startswith("="):
                if any(k in line for k in ("FAILURES", "ERRORS", "failed", "error")):
                    filtered.append(line)
            elif line.strip().startswith("assert ") or line.strip().startswith("AssertionError"):
                filtered.append(line)
        if filtered:
            return filtered

    # General / fallback filtering:
    for line in raw_lines:
        if _is_passing_or_progress(line):
            continue
        if _is_external_frame(line):
            continue
        filtered.append(line)

    return filtered


def project(cmd: str, rc: int, out: str, log_id: str = "") -> str:
    if not log_id:
        log_id = f"term_{secrets.token_hex(3)}"

    # Save the full output to ~/.subway-context/logs/<log_id>.log (create the dir)
    logs_dir = _get_subway_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"{log_id}.log"
    log_file.write_text(out, encoding="utf-8")

    if rc == 0:
        summary = _parse_pass_summary(cmd, out)
        return f"PASS ({summary}) [full log: {log_id}]"

    # rc != 0
    lines = _filter_fail_output(cmd, out)
    if len(lines) > 40:
        lines = lines[:40]

    trailer = f"[full log: {log_id}; read_terminal(id=\"{log_id}\", mode=\"full\")]"
    if lines:
        return "\n".join(lines) + "\n" + trailer
    return trailer
