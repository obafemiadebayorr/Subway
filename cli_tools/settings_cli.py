"""subway settings list|get|set — the CLI face of the settings contract."""
from __future__ import annotations

import json
import sys
from typing import List

USAGE = "Usage: subway settings [list [--group G]] | get <key> | set <key> <value>"


def _fmt(val) -> str:
    return json.dumps(val)


def run(argv: List[str]) -> int:
    from hud_server import settings_api, settings_schema as schema

    verb = argv[0].lower() if argv else "list"
    if verb in ("list", "ls"):
        group = ""
        if "--group" in argv:
            idx = argv.index("--group")
            group = argv[idx + 1] if idx + 1 < len(argv) else ""
        cfg = settings_api.load_config()
        shown = 0
        for spec in schema.SCHEMA:
            if spec["scope"] != "global" or (group and spec["group"] != group):
                continue
            val = settings_api.value(spec["key"], cfg)
            mark = "*" if val != spec["default"] else " "
            edit = "" if spec.get("managed") else "  (HUD only)"
            print(f"{mark} {spec['group']:<13} {spec['key']:<24} {_fmt(val)}  [default {_fmt(spec['default'])}]{edit}")
            shown += 1
        if not shown:
            print(f"No settings in group '{group}'. Groups: {', '.join(schema.GROUPS)}", file=sys.stderr)
            return 2
        return 0
    if verb == "get":
        spec = schema.resolve(argv[1]) if len(argv) > 1 else None
        if spec is None:
            print(f"Unknown setting. {USAGE}", file=sys.stderr)
            return 2
        if spec["scope"] != "global":
            print(f"{spec['key']} is a per-thread setting; change it in the thread's settings.", file=sys.stderr)
            return 2
        print(_fmt(settings_api.value(spec["key"])))
        return 0
    if verb == "set":
        spec = schema.resolve(argv[1]) if len(argv) > 1 else None
        if spec is None or len(argv) < 3:
            print(f"Unknown setting or missing value. {USAGE}", file=sys.stderr)
            return 2
        if not spec.get("managed") or spec["scope"] != "global":
            print(f"{spec['key']} is changed from the HUD Settings page (it has side effects or lives per thread).", file=sys.stderr)
            return 2
        status, body, _rest = settings_api.handle_post({spec["key"]: argv[2]})
        if status != 200:
            print(body.get("error", "invalid value"), file=sys.stderr)
            return 1
        print(f"{spec['key']} = {_fmt(body['applied'][spec['key']])}")
        return 0
    print(USAGE, file=sys.stderr)
    return 2
