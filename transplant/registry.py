"""Registry of session-store adapters.

Provider ids come from the live model catalog plus adapter ids, never a
fixed list. Providers are data.
"""

import glob
import json
import os

from transplant.adapter import Adapter, AdapterError

SHIPPED_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "adapters"
)

_errors = []


def learned_dir():
    """Directory holding user instance learned adapters."""
    base = os.environ.get("SUBWAY_DIR") or os.path.join(
        os.path.expanduser("~"), ".subway-context"
    )
    return os.path.join(base, "adapters")


def load_errors():
    return list(_errors)


def _load_dir(path, origin, out):
    if not path or not os.path.isdir(path):
        return
    for filepath in sorted(glob.glob(os.path.join(path, "*.json"))):
        try:
            with open(filepath, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            adapter = Adapter.from_dict(data, origin=origin)
        except (OSError, ValueError, AdapterError) as exc:
            _errors.append("%s: %s" % (filepath, exc))
            continue
        out[adapter.id] = adapter


def adapters():
    """dict of adapter id -> Adapter; learned wins over shipped."""
    del _errors[:]
    out = {}
    _load_dir(SHIPPED_DIR, "shipped", out)
    _load_dir(learned_dir(), "learned", out)
    return out


def for_provider(pid):
    return adapters().get(pid)


def known_providers():
    """Live catalog provider ids plus adapter ids, in first-seen order."""
    seen = []
    try:
        import server as _server

        for item in _server.model_catalog() or []:
            pid = item.get("provider")
            if pid and pid not in seen:
                seen.append(pid)
    except Exception:
        pass
    for aid in adapters():
        if aid not in seen:
            seen.append(aid)
    return seen


def unmapped():
    known = adapters()
    return [pid for pid in known_providers() if pid not in known]
