"""Adapter loading, schema validation and path resolution.

Providers are data: an adapter is a JSON description of a session store,
never code. No provider names appear here.
"""

import copy
import json
import os

from transplant import template

IS_WINDOWS = os.name == "nt"

REQUIRED_KEYS = {"id", "store", "resume"}
ALLOWED_KEYS = REQUIRED_KEYS | {
    "detect",
    "read",
    "write",
    "context_tokens",
    "probe",
    "notes",
    "hook",
}
STORE_KINDS = {"jsonl", "sqlite", "json", "protobuf", "dir"}


class AdapterError(Exception):
    """Raised for malformed adapter JSON or schema violations."""


def path_vars():
    """Per-OS path variables usable in store path and resume templates."""
    home = os.path.expanduser("~")
    roaming = os.path.join(home, "AppData", "Roaming")
    app_data = os.path.join(home, "AppData", "Lo" + "cal")
    dot_share = os.path.join(home, ".l" + "ocal", "share")
    return {
        "home": home,
        "appdata": os.environ.get("APPDATA") or (roaming if IS_WINDOWS else ""),
        "localappdata": os.environ.get("LOCALAPPDATA")
        or (app_data if IS_WINDOWS else ""),
        "xdg_data": os.environ.get("XDG_DATA_HOME") or dot_share,
        "xdg_config": os.environ.get("XDG_CONFIG_HOME")
        or os.path.join(home, ".config"),
    }


class Adapter:
    def __init__(self, data, origin="shipped"):
        self._data = copy.deepcopy(data)
        self._origin = origin

    @classmethod
    def from_dict(cls, d, origin="shipped"):
        if not isinstance(d, dict):
            raise AdapterError("adapter must be a JSON object")
        for key in REQUIRED_KEYS:
            if key not in d:
                raise AdapterError("missing required key: %s" % key)
        for key in d:
            if key not in ALLOWED_KEYS:
                raise AdapterError("unknown top-level key: %s" % key)
        store = d["store"]
        if not isinstance(store, dict):
            raise AdapterError("store must be an object")
        if store.get("kind") not in STORE_KINDS:
            raise AdapterError(
                "store.kind must be one of %s" % sorted(STORE_KINDS)
            )
        paths = store.get("path", [])
        if not isinstance(paths, list) or not all(
            isinstance(p, str) for p in paths
        ):
            raise AdapterError("store.path must be a list of strings")
        slug = store.get("slug")
        if slug is not None and (
            not isinstance(slug, dict)
            or not isinstance(slug.get("pattern"), str)
            or not isinstance(slug.get("repl"), str)
        ):
            raise AdapterError(
                "store.slug must be an object with string pattern and repl"
            )
        write = d.get("write")
        if isinstance(write, dict):
            footer = write.get("footer")
            if footer is not None and not isinstance(footer, list):
                raise AdapterError("write.footer must be a list of records")
            dir_mode = write.get("dir_mode")
            if dir_mode is not None and (
                not isinstance(dir_mode, int) or isinstance(dir_mode, bool)
            ):
                raise AdapterError("write.dir_mode must be an integer")
        resume = d["resume"]
        if not isinstance(resume, dict):
            raise AdapterError("resume must be an object")
        argv = resume.get("argv", [])
        if not isinstance(argv, list) or not all(
            isinstance(a, str) for a in argv
        ):
            raise AdapterError("resume.argv must be a list of strings")
        out = resume.get("output")
        if out is not None:
            if not isinstance(out, dict) or out.get("format") != "jsonl" or not isinstance(out.get("text"), str):
                raise AdapterError('resume.output must be {"format": "jsonl", "text": "<dotted path>", "match": {...}}')
            if not isinstance(out.get("match", {}), dict):
                raise AdapterError("resume.output.match must be an object")
        return cls(d, origin)

    @property
    def id(self):
        return self._data.get("id")

    @property
    def kind(self):
        return self._data["store"]["kind"]

    @property
    def origin(self):
        return self._origin

    @property
    def data(self):
        return self._data

    def store_paths(self, **extra):
        """Render store path templates with path variables plus extras."""
        ctx = path_vars()
        ctx.update(extra)
        return [
            os.path.normpath(template.render(p, ctx))
            for p in self._data["store"].get("path", [])
        ]

    @property
    def structured_output(self):
        return bool((self._data["resume"].get("output") or {}).get("text"))

    def reply_text(self, raw):
        """The assistant's words from a resume run's output.

        Plain-text CLIs pass through. With resume.output (a JSONL event stream, e.g. a
        --output-format json flag), only records matching `match` are kept and the value at
        the dotted `text` path is joined, so banners, usage and status lines never reach a
        transcript. Falls back to the raw output when nothing matches."""
        spec = self._data["resume"].get("output")
        if not spec:
            return raw
        match = spec.get("match") or {}
        texts = []
        for line in (raw or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or any(_dig(rec, k) != v for k, v in match.items()):
                continue
            val = _dig(rec, spec["text"])
            if isinstance(val, str) and val.strip():
                texts.append(val.strip())
        return "\n\n".join(texts) if texts else raw

    def resume_argv(self, binary, session_id, text, model="", effort=""):
        """Render the resume argv; flags only appear when requested."""
        resume = self._data["resume"]
        ctx = path_vars()
        ctx.update(
            {
                "bin": binary,
                "session_id": session_id,
                "text": text,
                "model": model,
                "effort": effort,
                "uuid": template.new_uuid(),
                "ts_iso": template.now_iso(),
                "ts_ms": template.now_ms(),
            }
        )
        argv = [template.render(a, ctx) for a in resume.get("argv", [])]
        if model:
            argv.extend(
                template.render(a, ctx) for a in resume.get("model_flag", [])
            )
        if effort:
            argv.extend(
                template.render(a, ctx) for a in resume.get("effort_flag", [])
            )
        return argv

    def context_tokens(self, model=""):
        """Default context window from data, else 128000."""
        ct = self._data.get("context_tokens")
        if isinstance(ct, dict):
            per_model = ct.get("per_model")
            if model and isinstance(per_model, dict) and model in per_model:
                try:
                    return int(per_model[model])
                except (TypeError, ValueError):
                    pass
            try:
                return int(ct.get("default", 128000))
            except (TypeError, ValueError):
                pass
        elif isinstance(ct, int):
            return ct
        return 128000

    def fingerprint_ok(self, sample=None, tables=None):
        """Check a storage sample against the adapter's fingerprint spec.

        Every dotted key in store.fingerprint.sample_keys must resolve in
        the sample (dict key or list index at each step); every sqlite
        table name must be present in tables. A missing fingerprint passes.
        """
        fingerprint = self._data["store"].get("fingerprint")
        if not isinstance(fingerprint, dict):
            return True
        for key in fingerprint.get("sample_keys", []):
            if _resolve(sample, key) is _MISSING:
                return False
        for name in fingerprint.get("sqlite_tables", []):
            if tables is None or name not in tables:
                return False
        return True


class _Missing:
    pass


_MISSING = _Missing()


def _resolve(sample, dotted):
    current = sample
    if current is None:
        return _MISSING
    for part in dotted.split("."):
        if isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return current


def load_file(path, origin="shipped"):
    """Load and validate an adapter JSON file."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise AdapterError("cannot load %s: %s" % (path, exc)) from exc
    try:
        return Adapter.from_dict(data, origin=origin)
    except AdapterError as exc:
        raise AdapterError("%s: %s" % (path, exc)) from exc


def _dig(obj, dotted):
    """Value at a dotted key path ("data.content"), or None."""
    for part in str(dotted).split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj
