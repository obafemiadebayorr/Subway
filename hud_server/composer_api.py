"""PKG-B1: dispatcher for the composer POST routes.

Routes wired in ``hud_server.handlers.do_POST``:

- ``/api/composer/principal``  -> ``composer_actions.principal``   (PKG-B2)
- ``/api/composer/rewind``     -> ``composer_actions.rewind``      (PKG-B2)
- ``/api/composer/compact``    -> ``composer_actions.compact``     (PKG-B2)
- ``/api/composer/mail``       -> ``composer_mail.queue_mail``     (PKG-B3)

Implementations are imported lazily inside :func:`dispatch` so this package
compiles and tests green before B2/B3 land; a missing module surfaces as
``(500, {"error": <import error>})`` which is the only acceptable
not-yet-built state and disappears once B2/B3 land.

Each implementation returns ``(status, obj)``. A bare ``dict`` return is
accepted as ``(200, obj)``. ``ValueError`` from an implementation maps to
400. No token is ever logged or echoed here.
"""

from __future__ import annotations

import importlib

from typing import Any, Dict, Tuple

_ACTIONS = ("principal", "rewind", "compact", "mail", "initiator")


def dispatch(action: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Route one composer action to its lazily-imported implementation."""
    if action not in _ACTIONS:
        return 404, {"error": f"Unknown composer action: {action}"}

    payload = payload if isinstance(payload, dict) else {}

    try:
        if action == "mail":
            composer_mail = importlib.import_module("composer_mail")

            def impl(p: Dict[str, Any]) -> Any:
                return composer_mail.queue_mail(p.get("steps"), p.get("clock"), p.get("thread") or "")
        else:
            # PKG-B2 module holds principal / rewind / compact / initiator.
            composer_actions = importlib.import_module("hud_server.composer_actions")

            impl = getattr(composer_actions, action)
    except ImportError as e:  # B2/B3 not landed yet — the only acceptable 500.
        return 500, {"error": f"composer backend unavailable: {e}"}

    try:
        res = impl(payload)
    except ValueError as e:
        return 400, {"error": str(e)}
    except KeyError as e:
        return 400, {"error": f"missing required field: {e}"}

    if isinstance(res, tuple) and len(res) == 2:
        status, obj = res
    else:
        status, obj = 200, res

    if not isinstance(obj, dict):
        obj = {"result": obj}
    if 200 <= int(status) < 300 and "error" not in obj:
        return 200, obj
    return int(status), obj if "error" in obj else {"error": str(obj)}
