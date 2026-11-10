"""Show the pairing dialog until someone answers it, then apply the answer.

Run as its own process (`python3 -m pairing_prompt <request_id> <code>`) so the phone's
HTTP request returns immediately while the Mac waits for a human.
"""
from __future__ import annotations

import subprocess
import sys

import pairing


def ask(request_id: str, code: str, give_up_s: int = 600) -> str:
    req = pairing.get_request(request_id) or {"request_id": request_id, "code": code, "device": "A device"}
    script = pairing.dialog_script(req, give_up_s=give_up_s)
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=give_up_s + 30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "timeout"
    out = (proc.stdout or "") + (proc.stderr or "")
    if "button returned:Approve" in out:
        return "approved"
    if "button returned:Deny" in out or "User canceled" in out:
        return "denied"
    return "timeout"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: python3 -m pairing_prompt <request_id> <code>", file=sys.stderr)
        return 2
    request_id, code = argv[1], argv[2]
    req = pairing.get_request(request_id)
    if not req or req.get("status") != "pending":
        return 0  # answered elsewhere (HUD or CLI) while the dialog was opening
    answer = ask(request_id, code)
    if answer == "approved":
        pairing.approve(request_id)
    elif answer == "denied":
        pairing.deny(request_id, "Denied in the dialog")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
