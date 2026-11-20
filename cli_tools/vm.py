"""`subway vm` CLI: list/start/stop/exec/link for lima, utm, qemu, devcontainer VMs."""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

import vm_station


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="subway vm")
    sub = p.add_subparsers(dest="action", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--backend", choices=vm_station.BACKENDS)
    for a in ("start", "stop"):
        s = sub.add_parser(a)
        s.add_argument("name")
        s.add_argument("--backend", default="lima", choices=vm_station.BACKENDS)
    ex = sub.add_parser("exec")
    ex.add_argument("name")
    ex.add_argument("--backend", default="lima", choices=vm_station.BACKENDS)
    ex.add_argument("cmd", nargs=argparse.REMAINDER)
    ln = sub.add_parser("link")
    ln.add_argument("name")
    ln.add_argument("--backend", default="lima", choices=vm_station.BACKENDS)
    ln.add_argument("--url", required=True)
    ln.add_argument("--token", required=True)
    ln.add_argument("--relation", default="own")
    return p


def t_vm(argv: List[str], runner=None) -> str:
    """Run a vm subcommand; returns text, raises SystemExit(2) on bad input."""
    a = _parser().parse_args(argv)
    try:
        if a.action == "list":
            rows = vm_station.list_vms(a.backend, runner=runner)
            if not rows:
                return "No VMs found."
            return "\n".join(f"{r['name']}\t{r['backend']}\t{r['status']}" for r in rows)
        if a.action in ("start", "stop"):
            fn = vm_station.start_vm if a.action == "start" else vm_station.stop_vm
            fn(a.name, a.backend, runner=runner)
            return f"{a.action} ok: {a.name}"
        if a.action == "exec":
            cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
            r = vm_station.exec_vm(a.name, cmd, a.backend, runner=runner)
            return (r["stdout"] + r["stderr"]).rstrip("\n") if r["ok"] else \
                f"exit {r['code']}: {(r['stderr'] or r['stdout']).strip()}"
        cmd = vm_station.build_link_cmd(a.name, a.backend, a.url, a.token, a.relation)
        r = vm_station._run(cmd, runner)
        if not r["ok"]:
            raise vm_station.VMError(f"link failed: {r['stderr'].strip() or r['code']}")
        return f"linked {a.name} as {a.relation}"
    except vm_station.VMError as e:
        raise SystemExit(f"vm: {e}")


def main(argv: Optional[List[str]] = None) -> int:
    try:
        print(t_vm(list(sys.argv[1:] if argv is None else argv)))
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 1
        return int(e.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
