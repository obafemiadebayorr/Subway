#!/usr/bin/env python3
"""Antigravity PreToolUse hook adapter for Subway Governor."""

import json
import os
import subprocess
import sys
import time

_diag_done = set()


def _diag(session_id, payload, tool_name, turn_source):
    key = str(session_id or "")
    try:
        log_dir = os.path.join(os.path.expanduser(os.environ.get("SUBWAY_DIR") or "~/.subway"), "logs")
        os.makedirs(log_dir, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key) or "_"
        marker = os.path.join(log_dir, f".antigravity_governor_{safe}.seen")
        if os.path.exists(marker):
            return
        open(marker, "a").close()
        line = json.dumps({
            "keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
            "toolCall": tool_name,
            "turn_key_source": turn_source,
        })
        with open(os.path.join(log_dir, "antigravity_governor.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def count_user_messages(transcript_path: str) -> int:
    if not transcript_path or not os.path.exists(transcript_path):
        return 0
    count = 0
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "USER_INPUT" or entry.get("source") == "USER_EXPLICIT":
                        count += 1
                except Exception:
                    if '"USER_INPUT"' in line or '"USER_EXPLICIT"' in line:
                        count += 1
    except Exception:
        pass
    return count


def main() -> None:
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            print(json.dumps({"decision": "allow"}))
            return

        payload = json.loads(raw_input)
        session_id = payload.get("conversationId") or ""
        transcript_path = payload.get("transcriptPath") or ""
        tool_call = payload.get("toolCall") or {}
        tool_name = tool_call.get("name") or ""
        tool_args = tool_call.get("args") or {}
        if tool_name == "call_mcp_tool" and isinstance(tool_args, dict):
            server = str(tool_args.get("ServerName") or tool_args.get("serverName") or "")
            inner = ""
            for k in ("ToolName", "toolName", "Name", "name"):
                if tool_args.get(k):
                    inner = str(tool_args.get(k))
                    break
            if inner:
                tool_name = f"{server}/{inner}" if server else inner
                raw = tool_args.get("Arguments")
                try:
                    tool_args = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
                except Exception:
                    tool_args = {}

        turn_source = None
        turn_key = None
        _lui = payload.get("lastUserInput")
        if isinstance(_lui, str) and _lui.strip():
            import hashlib
            turn_key = "u-" + hashlib.sha1(_lui.encode("utf-8", "ignore")).hexdigest()[:12]
            turn_source = "u"
        for k in (() if turn_key is not None else ("turnId", "turn_id", "promptId", "userMessageId", "requestId", "stepId")):
            v = payload.get(k)
            if v:
                turn_key = str(v)
                turn_source = "a"
                break
        if turn_key is None:
            n = count_user_messages(transcript_path)
            if n > 0:
                turn_key = str(n)
                turn_source = "b"
        if turn_key is None:
            turn_key = f"gap-{int(time.time())}"
            turn_source = "c"
            # gap-N semantics handled by idle_reset; use stable-ish marker
            turn_key = "gap"

        _diag(session_id, payload, tool_name, turn_source)

        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cli_path = os.path.join(repo_dir, "bin", "subway.js")

        cmd = [
            "node",
            cli_path,
            "governor",
            "check",
            "--provider",
            "antigravity",
            "--session",
            str(session_id),
            "--turn-key",
            str(turn_key),
            "--idle-reset",
            "90",
        ]

        cli_stdin = json.dumps({"tool": tool_name, "input": tool_args}).encode("utf-8")
        res = subprocess.run(
            cmd,
            input=cli_stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )

        if res.returncode == 2:
            reason = "Blocked by governor"
            try:
                out = json.loads(res.stdout.decode("utf-8", errors="ignore").strip() or "{}")
                if "reason" in out and out["reason"]:
                    reason = out["reason"]
            except Exception:
                pass
            print(json.dumps({"decision": "deny", "reason": reason}))
        else:
            print(json.dumps({"decision": "allow"}))
    except Exception:
        print(json.dumps({"decision": "allow"}))


if __name__ == "__main__":
    main()
