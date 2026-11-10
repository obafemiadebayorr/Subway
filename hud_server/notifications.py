from __future__ import annotations

from __future__ import annotations

import base64

import concurrent.futures

import contextlib

import contextvars

import datetime as _dt

import difflib

import glob

import hashlib

import hmac

import http.server

import json

import math

import os

import queue

import re

import secrets

import select

import shutil

import signal

import sqlite3

import subprocess

import sys

import tempfile

import threading

try:
    import ctypes
except ImportError:
    ctypes = None  # type: ignore[assignment]

import time

import traceback

import urllib.error

import urllib.parse

import urllib.request

import uuid

from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

import terminals

import native_terminals

import sse_stream

import threads

try:
    import fcntl
except ImportError:  # Windows: state stays atomic per write, but not serialized across processes.
    fcntl = None  # type: ignore[assignment]

import server as _s


def _log_notification_failure(mechanism: str, error: Any) -> None:
    """Keep the notification failure log bounded without making alerts fatal."""
    path = os.path.join(_s.SUBWAY_DIR, "notify.log")  # follows SUBWAY_DIR so tests and sandboxes never touch the real log
    entry = f"{_dt.datetime.now().astimezone().isoformat(timespec='seconds')} [{mechanism}] {error}\n"
    try:
        with _s._notification_log_lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except FileNotFoundError:
                lines = []
            with open(path, "w", encoding="utf-8") as f:
                f.writelines((lines + [entry])[-200:])
    except (OSError, UnicodeError):
        pass

def _run_notification_command(mechanism: str, command: List[str], timeout: Optional[float] = None,
                              **kwargs: Any) -> Optional[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, **kwargs)
        return_code = getattr(result, "returncode", 0)
        if isinstance(return_code, int) and return_code != 0:
            error = (result.stderr or result.stdout or f"exited with status {return_code}").strip()
            _s._log_notification_failure(mechanism, error)
            return error
    except Exception as exc:
        error = str(exc)
        _s._log_notification_failure(mechanism, error)
        return error
    return None

def _send_desktop_notification(title: str, message: str, route: str = "workers") -> Tuple[str, Optional[str]]:
    """Send a desktop notification and report the mechanism and any non-fatal error."""
    if os.environ.get("SUBWAY_NO_NOTIFY"):  # Tests and sandboxes must never reach the real desktop.
        return "disabled", "SUBWAY_NO_NOTIFY is set"
    route = route if route in ("overview", "usage", "workers") else "workers"
    url = f"http://127.0.0.1:{_s._hud_port()}/#{route}"
    if sys.platform == "darwin":
        notifier = _s._find_executable(
            "terminal-notifier",
            ("/opt/homebrew/bin/terminal-notifier", "/usr/local/bin/terminal-notifier"),
        )
        if notifier:
            error = _s._run_notification_command(
                "terminal-notifier",
                [notifier, "-title", title, "-message", message, "-sound", "default", "-group", "subway", "-open", url],
                timeout=3,
            )
            return "terminal-notifier", error
        safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
        safe_msg = message.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{safe_msg}" with title "{safe_title}" sound name "default"'
        error = _s._run_notification_command("osascript", ["osascript", "-e", script], timeout=2)
        return "osascript", error
    if sys.platform.startswith("linux"):
        notifier = _s._find_executable("notify-send", ("/usr/bin/notify-send",))
        if not notifier:
            error = "notify-send was not found"
            _s._log_notification_failure("notify-send", error)
            return "notify-send", error
        try:
            proc = subprocess.run([notifier, "--help"], capture_output=True, text=True, timeout=2)
            has_action = "--action" in ((proc.stdout or "") + (proc.stderr or ""))
        except Exception as exc:
            _s._log_notification_failure("notify-send capability check", exc)
            has_action = False
        if has_action:
            def _linux_wait() -> None:
                try:
                    res = subprocess.run(
                        [notifier, "--app-name", "Subway", "--action=open=Open", "--wait", title, message],
                        capture_output=True, text=True,
                    )
                    return_code = getattr(res, "returncode", 0)
                    if isinstance(return_code, int) and return_code != 0:
                        _s._log_notification_failure("notify-send", res.stderr or f"exited with status {return_code}")
                    elif res.stdout and res.stdout.strip() == "open":
                        _s._run_notification_command("xdg-open", ["xdg-open", url])
                except Exception as exc:
                    _s._log_notification_failure("notify-send", exc)

            threading.Thread(target=_linux_wait, daemon=True).start()
            return "notify-send", None
        error = _s._run_notification_command("notify-send", [notifier, title, message], timeout=2)
        return "notify-send", error
    if sys.platform.startswith("win"):
        xml_url = _s._escape_xml(url)
        safe_title = _s._escape_xml(title).replace("'", "''")
        safe_msg = _s._escape_xml(message).replace("'", "''")
        xml = (
            f'<toast activationType="protocol" launch="{xml_url}">'
            f'<visual><binding template="ToastGeneric">'
            f'<text>{safe_title}</text><text>{safe_msg}</text>'
            f'</binding></visual></toast>'
        )
        app_id = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
        cmd = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null; "
            f"$xml = [Windows.Data.Xml.Dom.XmlDocument]::new(); $xml.LoadXml('{xml}'); "
            f"$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
            f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}').Show($toast)"
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        error = _s._run_notification_command(
            "Windows toast",
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd],
            timeout=5,
            creationflags=creationflags,
        )
        return "Windows toast", error
    error = f"unsupported platform: {sys.platform}"
    _s._log_notification_failure("desktop notification", error)
    return "none", error

def notify_desktop_event(key: str, title: str, message: str, route: str = "workers") -> bool:
    """Send one desktop notification per event, keyed in limit_pings.json so each event notifies once."""
    now = time.time()
    try:
        with _s._state_lock("pings"):
            sent = _s._load_json("limit_pings.json")
            if key in sent:
                return False
            sent[key] = now
            _s._save_json("limit_pings.json", {k: v for k, v in sent.items() if now - v < 8 * 86400})
    except Exception as exc:
        _s._log_notification_failure("notification dedupe", exc)
    try:
        from notify_driver import dispatch_notification
        with _s._state_lock("config"):
            cfg = _s._load_json("config.json")
        dispatch_notification(title, message, kind="info", data={"key": key, "route": route}, config=cfg)
    except Exception as exc:
        _s._log_notification_failure("universal dispatch", exc)
    return True
