"""Start-on-login service definitions for a headless Subway station.

The builders in this module are *pure*: they return text and argv lists and never
touch the filesystem, the network, or an OS service manager. Nothing is written
or executed until :func:`install` is called with ``apply=True``. A plain
``install(...)`` (or ``--apply`` omitted on the CLI) is a dry run.

No key material goes into a definition file: the station reads its own identity
from SUBWAY_DIR when the service starts it.

Usage (CLI)::

    python3 service_install.py                       # dry run, current OS
    python3 service_install.py --platform linux      # dry run, chosen OS
    python3 service_install.py --apply               # write + register
    python3 service_install.py --apply --uninstall   # unregister + remove
"""
from __future__ import annotations

import argparse
import json
import os
import platform as _platform
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape as _xml_escape

LABEL = "com.subway.station"
TASK_NAME = "Subway Station"
UNIT_NAME = "subway.service"
DEFAULT_COMMAND: Tuple[str, ...] = ("subway", "tunnel")

Runner = Callable[[List[str]], object]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def normalize_platform(system: Optional[str] = None) -> str:
    """Return 'darwin', 'windows' or 'linux'. Unknown values fail loudly."""
    value = (system if system is not None else _platform.system()).strip().lower()
    if value in ("darwin", "macos", "mac", "osx"):
        return "darwin"
    if value in ("windows", "win32", "win"):
        return "windows"
    if value == "linux":
        return "linux"
    raise ValueError("Unsupported platform for Subway service install: %r" % (system,))


def station_argv(command=None) -> List[str]:
    """The station command as an argv list. Strings are split on whitespace; no shell is used."""
    if command is None:
        return list(DEFAULT_COMMAND)
    if isinstance(command, str):
        parts = command.split()
    else:
        parts = [str(part) for part in command]
    if not parts:
        raise ValueError("station command must not be empty")
    return parts


def log_paths(subway_dir: Optional[str] = None) -> Tuple[str, str]:
    """Stable log file locations, also printed by setup and by the CLI."""
    base = subway_dir or os.path.join(os.path.expanduser("~"), ".subway")
    return (
        os.path.join(base, "logs", "station.out.log"),
        os.path.join(base, "logs", "station.err.log"),
    )


def _unit_arg(arg: str) -> str:
    """Quote a systemd ExecStart argument when it contains whitespace or quotes."""
    if not any(ch.isspace() for ch in arg) and '"' not in arg:
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _windows_command_line(argv: Sequence[str]) -> str:
    """Quote a schtasks /TR command line (Windows-style double quotes)."""
    parts = []
    for arg in argv:
        if any(ch.isspace() for ch in arg):
            parts.append('"' + arg.replace('"', '\\"') + '"')
        else:
            parts.append(arg)
    return " ".join(parts)


def service_paths(platform: Optional[str] = None, home: Optional[str] = None,
                  subway_dir: Optional[str] = None) -> dict:
    """Return the OS-specific definition path, auxiliary file path and log paths."""
    system = normalize_platform(platform)
    home = home or os.path.expanduser("~")
    if not os.path.isabs(home):
        raise ValueError("home must be an absolute path: %r" % (home,))
    subway_dir = subway_dir or os.path.join(home, ".subway")
    if not os.path.isabs(subway_dir):
        raise ValueError("subway_dir must be an absolute path: %r" % (subway_dir,))
    paths = {"definition": None, "xml": None, "logs": log_paths(subway_dir)}
    if system == "darwin":
        paths["definition"] = os.path.join(home, "Library", "LaunchAgents", LABEL + ".plist")
    elif system == "linux":
        paths["definition"] = os.path.join(home, ".config", "systemd", "user", UNIT_NAME)
    else:
        paths["xml"] = os.path.join(subway_dir, "windows-task.xml")
    return paths


# ---------------------------------------------------------------------------
# Definition text builders (pure)
# ---------------------------------------------------------------------------

def macos_plist(command=None, stdout=None, stderr=None, label=LABEL) -> str:
    """A LaunchAgent plist with RunAtLoad + KeepAlive (launchd restarts the station)."""
    argv = station_argv(command)
    out = stdout or log_paths()[0]
    err = stderr or log_paths()[1]
    args = "\n".join("    <string>%s</string>" % _xml_escape(a) for a in argv)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '  <key>Label</key>\n'
        '  <string>%s</string>\n'
        '  <key>ProgramArguments</key>\n'
        '  <array>\n%s\n  </array>\n'
        '  <key>RunAtLoad</key>\n  <true/>\n'
        '  <key>KeepAlive</key>\n  <true/>\n'
        '  <key>StandardOutPath</key>\n  <string>%s</string>\n'
        '  <key>StandardErrorPath</key>\n  <string>%s</string>\n'
        '</dict>\n'
        '</plist>\n'
    ) % (_xml_escape(label), args, _xml_escape(out), _xml_escape(err))


def linux_unit(command=None, stdout=None, stderr=None) -> str:
    """A systemd --user unit. Restart=always is the equivalent of KeepAlive."""
    argv = station_argv(command)
    out = stdout or log_paths()[0]
    err = stderr or log_paths()[1]
    return (
        "[Unit]\n"
        "Description=Subway station\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "ExecStart=%s\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "StandardOutput=append:%s\n"
        "StandardError=append:%s\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    ) % (" ".join(_unit_arg(a) for a in argv), out, err)


def windows_task_xml(command=None, task_name=TASK_NAME, user=None,
                     interval="PT1M", count=3) -> str:
    """Task Scheduler XML carrying the ONLOGON trigger and RestartOnFailure settings."""
    argv = station_argv(command)
    user_line = "      <UserId>%s</UserId>\n" % _xml_escape(str(user)) if user else ""
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        '  <RegistrationInfo>\n'
        '    <Description>Subway station (logon trigger, restart on failure)</Description>\n'
        '  </RegistrationInfo>\n'
        '  <Triggers>\n    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>\n  </Triggers>\n'
        '  <Principals>\n    <Principal id="Author">\n'
        '%s'
        '      <LogonType>InteractiveToken</LogonType>\n'
        '      <RunLevel>Limited</RunLevel>\n'
        '    </Principal>\n  </Principals>\n'
        '  <Settings>\n'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
        '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
        '    <StartWhenAvailable>true</StartWhenAvailable>\n'
        '    <RestartOnFailure>\n'
        '      <Interval>%s</Interval>\n      <Count>%d</Count>\n'
        '    </RestartOnFailure>\n'
        '    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n'
        '  </Settings>\n'
        '  <Actions Context="Author">\n    <Exec>\n'
        '      <Command>%s</Command>\n'
        '      <Arguments>%s</Arguments>\n'
        '    </Exec>\n  </Actions>\n'
        '</Task>\n'
    ) % (user_line, _xml_escape(interval), int(count), _xml_escape(argv[0]),
         _xml_escape(" ".join(argv[1:])))


# ---------------------------------------------------------------------------
# Command builders (pure) — argv lists, never executed here
# ---------------------------------------------------------------------------

def macos_install_commands(definition_path, uid="$UID") -> List[List[str]]:
    return [["launchctl", "bootstrap", "gui/%s" % uid, definition_path]]


def macos_uninstall_commands(definition_path, uid="$UID") -> List[List[str]]:
    return [["launchctl", "bootout", "gui/%s" % uid, definition_path]]


def linux_install_commands(user="$USER") -> List[List[str]]:
    return [["loginctl", "enable-linger", user]]


def linux_uninstall_commands(user="$USER") -> List[List[str]]:
    return [["loginctl", "disable-linger", user]]


def linux_enable_commands() -> List[List[str]]:
    return [["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", UNIT_NAME]]


def linux_disable_commands() -> List[List[str]]:
    return [["systemctl", "--user", "disable", UNIT_NAME],
            ["systemctl", "--user", "daemon-reload"]]


def windows_install_commands(command=None, task_name=TASK_NAME) -> List[List[str]]:
    # /RL LIMITED runs as the logged-in user (never SYSTEM), so the screen is reachable.
    # /F makes a re-run replace the existing task instead of prompting: idempotent.
    return [["schtasks", "/Create", "/SC", "ONLOGON", "/RL", "LIMITED",
             "/TN", task_name, "/TR", _windows_command_line(station_argv(command)), "/F"]]


def windows_apply_settings_commands(xml_path, task_name=TASK_NAME) -> List[List[str]]:
    # Applies the <RestartOnFailure> settings from windows_task_xml().
    return [["schtasks", "/Create", "/TN", task_name, "/XML", xml_path, "/F"]]


def windows_uninstall_commands(task_name=TASK_NAME) -> List[List[str]]:
    return [["schtasks", "/Delete", "/TN", task_name, "/F"]]


# ---------------------------------------------------------------------------
# Aggregation + side-effecting install (only with apply=True)
# ---------------------------------------------------------------------------

@dataclass
class ServiceSpec:
    """Everything needed to install (or describe) a station service on one OS."""
    platform: str
    definition_path: Optional[str] = None
    definition_content: Optional[str] = None
    aux_files: List[Tuple[str, str]] = field(default_factory=list)
    install_commands: List[List[str]] = field(default_factory=list)
    settings_commands: List[List[str]] = field(default_factory=list)
    uninstall_commands: List[List[str]] = field(default_factory=list)
    log_paths: Tuple[str, str] = ()
    notes: List[str] = field(default_factory=list)

    def files(self) -> List[Tuple[str, str]]:
        out = []
        if self.definition_path and self.definition_content is not None:
            out.append((self.definition_path, self.definition_content))
        out.extend(self.aux_files)
        return out


@dataclass
class InstallResult:
    platform: str
    apply: bool = False
    uninstall: bool = False
    wrote: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    commands: List[List[str]] = field(default_factory=list)
    already_present: bool = False
    notes: List[str] = field(default_factory=list)
    spec: Optional[ServiceSpec] = None

    def as_dict(self) -> dict:
        return {
            "platform": self.platform,
            "apply": self.apply,
            "uninstall": self.uninstall,
            "wrote": self.wrote,
            "removed": self.removed,
            "commands": [" ".join(shlex.quote(a) for a in c) for c in self.commands],
            "already_present": self.already_present,
            "notes": self.notes,
            "log_paths": list(self.spec.log_paths) if self.spec else [],
        }


def generate(platform=None, *, home=None, subway_dir=None, command=None,
             uid="$UID", user="$USER", task_name=TASK_NAME, label=LABEL) -> ServiceSpec:
    """Build the full service definition for one OS. Pure: no I/O, no execution."""
    system = normalize_platform(platform)
    paths = service_paths(system, home=home, subway_dir=subway_dir)
    out, err = paths["logs"]
    spec = ServiceSpec(platform=system, log_paths=(out, err))

    if system == "darwin":
        spec.definition_path = paths["definition"]
        spec.definition_content = macos_plist(command, out, err, label=label)
        spec.install_commands = macos_install_commands(spec.definition_path, uid)
        spec.uninstall_commands = macos_uninstall_commands(spec.definition_path, uid)
        spec.notes.append("Needs a GUI (gui/$UID) login session; FileVault blocks pre-login start.")
    elif system == "linux":
        spec.definition_path = paths["definition"]
        spec.definition_content = linux_unit(command, out, err)
        spec.install_commands = linux_install_commands(user) + linux_enable_commands()
        spec.uninstall_commands = linux_disable_commands() + linux_uninstall_commands(user)
        spec.notes.append("linger keeps the user bus alive after logout.")
    else:
        spec.aux_files = [(paths["xml"], windows_task_xml(command, task_name=task_name))]
        spec.install_commands = windows_install_commands(command, task_name=task_name)
        spec.settings_commands = windows_apply_settings_commands(paths["xml"], task_name)
        spec.uninstall_commands = windows_uninstall_commands(task_name)
        spec.notes.append("Runs as the logged-in user, not SYSTEM, so the screen is reachable.")
    return spec


def _default_runner(argv: List[str]) -> None:
    """Best-effort OS call. 'Already loaded/registered' is fine: re-runs are idempotent."""
    try:
        subprocess.run(argv, check=False, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def _write_atomic(path: str, content: str) -> bool:
    """Write a definition, replacing a corrupt file. Returns False if already identical."""
    parent = os.path.dirname(path)
    if not parent or not os.path.isabs(parent):
        raise ValueError("definition path must be absolute: %r" % (path,))
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                if handle.read() == content:
                    return False
        except (OSError, UnicodeError):
            pass  # corrupt or unreadable: replace it
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".subway-service-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


def _remove(path: str) -> bool:
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


def install(platform=None, *, apply=False, uninstall=False, runner: Optional[Runner] = None,
            **kwargs) -> InstallResult:
    """Install/remove a station service.

    With ``apply=False`` (the default) this is a pure dry run: no file is written
    and no OS command is executed. With ``apply=True`` definition files are
    written (or removed on ``uninstall``) and the generated commands are run via
    *runner*, or via a best-effort subprocess runner when none is supplied.
    """
    spec = generate(platform, **kwargs)
    result = InstallResult(platform=spec.platform, apply=bool(apply),
                           uninstall=bool(uninstall), spec=spec, notes=list(spec.notes))
    if not apply:
        result.notes.append("dry run: nothing written or executed; pass --apply to install")
        return result

    def run(argv: List[str]) -> None:
        result.commands.append(list(argv))
        (runner or _default_runner)(list(argv))

    if uninstall:
        for argv in spec.uninstall_commands:
            run(argv)
        for path, _ in spec.files():
            if _remove(path):
                result.removed.append(path)
        result.already_present = not result.removed
    else:
        for path, content in spec.files():
            if _write_atomic(path, content):
                result.wrote.append(path)
            else:
                result.already_present = True
        for argv in spec.install_commands + spec.settings_commands:
            run(argv)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_human(result: InstallResult) -> None:
    spec = result.spec
    verb = "Uninstall" if result.uninstall else "Install"
    print("[SUBWAY] %s %s service (platform=%s, %s)" % (
        verb, LABEL, result.platform, "applied" if result.apply else "DRY RUN"))
    for path, content in spec.files():
        print("\n--- %s ---" % path)
        print(content, end="")
    groups = (("uninstall", spec.uninstall_commands),) if result.uninstall else (
        ("install", spec.install_commands + spec.settings_commands),)
    for label, commands in groups:
        for argv in commands:
            print("[SUBWAY] %s: %s" % (label, " ".join(shlex.quote(a) for a in argv)))
    print("[SUBWAY] logs: %s (stdout), %s (stderr)" % tuple(spec.log_paths))
    for note in result.notes:
        print("[SUBWAY] note: %s" % note)
    if result.uninstall:
        print("[SUBWAY] removal: re-run with --apply --uninstall to unregister and delete the definition.")
    elif not result.apply:
        print("[SUBWAY] to apply: re-run with --apply")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="subway service", description=__doc__.splitlines()[0])
    parser.add_argument("--platform", default=None, help="darwin|windows|linux (default: auto)")
    parser.add_argument("--apply", action="store_true", help="write files and run OS commands")
    parser.add_argument("--uninstall", action="store_true", help="unregister and remove the service")
    parser.add_argument("--home", default=None, help="home directory (default: ~)")
    parser.add_argument("--subway-dir", default=None, help="SUBWAY_DIR (default: <home>/.subway)")
    parser.add_argument("--command", nargs="*", default=None, help="station command (default: subway tunnel)")
    parser.add_argument("--uid", default="$UID", help="macOS GUI uid (default: $UID)")
    parser.add_argument("--user", default="$USER", help="Linux user for linger (default: $USER)")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result")
    args = parser.parse_args(argv)
    try:
        result = install(args.platform, apply=args.apply, uninstall=args.uninstall,
                         home=args.home, subway_dir=args.subway_dir,
                         command=args.command, uid=args.uid, user=args.user)
    except ValueError as exc:
        parser.error(str(exc))
        return 2
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
