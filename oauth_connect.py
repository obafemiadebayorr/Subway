"""OAuth connect flow for Subway MCP sign-in (step 50c-4)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import html
import http.server
import json
import os
import random
import secrets
import socket
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import oauth_discovery


class OAuthConnectError(Exception):
    """Base error for OAuth connect failures."""


class OAuthConnectTimeout(OAuthConnectError):
    """Raised when the connect flow times out."""


class OAuthStateMismatch(OAuthConnectError):
    """Raised when the callback state does not match."""


def _state_dir() -> str:
    return os.path.expanduser("~/.subway")


def _save_token(server_key: str, record: dict) -> None:
    import oauth_tokens

    oauth_tokens.save(server_key, record)


def _clients_path() -> str:
    return os.path.join(_state_dir(), "mcp_clients.json")


def _load_clients() -> dict:
    path = _clients_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _atomic_write(path: str, data: str, mode: int = 0o600) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _save_clients(clients: dict) -> None:
    _atomic_write(_clients_path(), json.dumps(clients, separators=(",", ":")), 0o600)


def _b64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    if len(verifier) < 43:
        verifier = (verifier + secrets.token_urlsafe(64))[:43]
    if len(verifier) > 128:
        verifier = verifier[:128]
    challenge = _b64url_no_pad(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _is_loopback_host(host: str) -> bool:
    h = (host or "").lower()
    return h in ("localhost", "127.0.0.1", "::1") or h.startswith("127.")


def _validate_token_endpoint(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname or ""
    if scheme == "https":
        return
    if scheme == "http" and _is_loopback_host(host):
        return
    raise OAuthConnectError("token_endpoint must be https unless loopback")


def _read_body_limited(resp, limit: int = 1024 * 1024) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = resp.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise OAuthConnectError("token response too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _post_json(url: str, payload: dict, timeout: float = 10.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _read_body_limited(resp)
    except Exception as e:
        raise OAuthConnectError(f"client registration failed: {e}") from e
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception as e:
        raise OAuthConnectError("client registration returned invalid JSON") from e
    if not isinstance(obj, dict):
        raise OAuthConnectError("client registration returned invalid response")
    return obj


def _post_form_no_redirect(url: str, payload: dict, timeout: float = 10.0) -> dict:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
            raise OAuthConnectError("token endpoint redirect not allowed")

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = _read_body_limited(resp)
    except OAuthConnectError:
        raise
    except urllib.error.HTTPError as e:
        try:
            body = _read_body_limited(e)
            obj = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            obj = {}
        err = obj.get("error") if isinstance(obj, dict) else None
        raise OAuthConnectError(f"token exchange failed: {err or e.code}") from e
    except Exception as e:
        raise OAuthConnectError(f"token exchange failed: {e}") from e
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception as e:
        raise OAuthConnectError("token endpoint returned invalid JSON") from e
    if not isinstance(obj, dict):
        raise OAuthConnectError("token endpoint returned invalid response")
    return obj


_PAGE_TEMPLATE = r'''<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Subway</title>{favicon}
<style>
:root{--ground:#F3F4EE;--surface:#FFFFFF;--ink:#0F2F3A;--muted:#5E7275;--hair:#E1E5DB;--accent:#2C86B0;--ochre:#B8872B;--red:#E0261B;--green:#00C759;--radius:12px}
*{box-sizing:border-box;margin:0}
html,body{background:var(--ground);color:var(--ink);font-family:"Instrument Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
body{min-height:100vh;overflow-x:hidden;position:relative}
.wrap{max-width:1440px;margin:0 auto;padding:20px 16px 160px;display:flex;flex-direction:column;gap:24px;position:relative;z-index:1}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px}
.brand{display:flex;align-items:center;gap:8px;font-size:20px;font-weight:600;letter-spacing:-.02em}
.status{font-size:12px;color:var(--muted);text-align:right}
.scene-win{border-radius:var(--radius);overflow:hidden;background:#07090B;line-height:0}
.scene{width:100%;height:auto;display:block;min-height:180px}
.main{display:flex;flex-direction:column;gap:24px}
.copy{display:flex;flex-direction:column;gap:14px;max-width:640px}
.eyebrow{font-size:12px;font-weight:600;letter-spacing:.14em;color:var(--ochre)}
.err .eyebrow{color:var(--red)}
h1{font-size:40px;line-height:44px;font-weight:600;letter-spacing:-.035em}
.lede{font-size:17px;line-height:26px;color:#3D5559}
.note{font-size:13px;color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--hair);border-radius:var(--radius);box-shadow:0 1px 0 var(--hair),0 18px 40px -24px rgba(15,47,58,.28);padding:22px 22px;display:flex;flex-direction:column;gap:16px;width:100%;max-width:420px}
.card-h{display:flex;align-items:center;justify-content:space-between;gap:8px}
.card-t{font-size:12px;font-weight:600;letter-spacing:.14em;color:var(--muted)}
.card-s{font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;font-size:11px;color:#00A84B}
.err .card-s{color:var(--red)}
.rows{display:flex;flex-direction:column;gap:10px;list-style:none;padding:0}
.rows li{display:flex;align-items:center;gap:12px;font-size:13px;color:var(--muted)}
.sq{width:14px;height:14px;background:var(--green);flex-shrink:0}
.mono{font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;font-size:13px;color:var(--ink)}
.errbox{background:var(--ground);border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;gap:8px}
.errbox p{font-size:13px;line-height:19px;color:var(--muted)}
.rule{height:1px;background:var(--hair)}
.actions{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;gap:8px;padding:11px 18px;background:var(--ink);color:#fff;border-radius:8px;font-size:14px;font-weight:600;text-decoration:none}
.link{font-size:14px;font-weight:600;color:var(--accent);text-decoration:none}
.tracks{position:absolute;left:0;right:0;bottom:0;height:180px;pointer-events:none;z-index:0}
.tracks svg{width:100%;height:100%;display:block}
/* train arrival: plays once */
.train{animation:glide 900ms cubic-bezier(.2,.8,.2,1) both}
.spray{animation:spray 1200ms ease-out both}
.glow{animation:pulse 1400ms ease-out 700ms both}
@keyframes glide{from{transform:translateX(-320px)}to{transform:none}}
@keyframes spray{from{opacity:0}40%{opacity:1}to{opacity:.55}}
@keyframes pulse{from{opacity:.2}50%{opacity:1}to{opacity:.75}}
@media (prefers-reduced-motion:reduce){.train,.spray,.glow{animation:none}}
@media (min-width:768px){
  .wrap{padding:32px 40px 200px;gap:32px}
  h1{font-size:52px;line-height:56px}
  .main{flex-direction:row;align-items:flex-start;justify-content:space-between}
  .copy{padding-left:0}
}
@media (min-width:1180px){
  .wrap{padding:40px 64px 220px}
  .brand{font-size:22px}.status{font-size:13px}
  h1{font-size:64px;line-height:68px}
  .lede{font-size:18px;line-height:28px}
  .copy{padding-left:180px}
  .card{padding:24px 26px}
}
</style></head>
<body class="{state_class}">
<div class="wrap">
  <header class="top">
    <div class="brand">{brand}subway</div>
    <div class="status">{status}</div>
  </header>
  <div class="scene-win">{scene}</div>
  <main class="main">
    <section class="copy">
      <div class="eyebrow">{eyebrow}</div>
      <h1>{title}</h1>
      <p class="lede">{body}</p>
      <p class="note">Tokens stay in your Keychain. Workers never see them.</p>
    </section>
    {card}
  </main>
</div>
<div class="tracks" aria-hidden="true"><svg viewBox="0 0 1440 180" preserveAspectRatio="none" fill="none"><path d="M1228 14 H470 C250 14 132 70 132 180" stroke="#E2453C" stroke-width="3"/><path d="M1228 48 H460 C320 48 256 100 256 180" stroke="#3A8DB8" stroke-width="3"/><path d="M1228 81 H450 C390 81 378 120 378 180" stroke="#F2C531" stroke-width="3"/></svg></div>
</body></html>'''

_CARD_OK = r'''<aside class="card">
  <div class="card-h"><span class="card-t">NOW ON THE LINE</span><span class="card-s">Stations linked</span></div>
  <ul class="rows">
    <li><span class="sq"></span><span class="mono">{server}</span>tools ready in every thread</li>
    <li><span class="sq"></span>Workers borrow it when a brief names it</li>
    <li><span class="sq"></span>Manage it in Settings → Shared MCP</li>
  </ul>
  <div class="rule"></div>
  <div class="actions"><a class="btn" href="{hud}/">Back to Subway<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M3 7h8M8 4l3 3-3 3" stroke="#FFFFFF" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></a><a class="link" href="{hud}/#settings">Open Shared MCP</a></div>
</aside>'''

_CARD_ERR = r'''<aside class="card">
  <div class="card-h"><span class="card-t">WHAT HAPPENED</span><span class="card-s">Red means stop</span></div>
  <div class="errbox"><span class="mono">error: {error_code}</span><p>{error_text}</p></div>
  <div class="rule"></div>
  <div class="actions"><a class="btn" href="{hud}/#settings">Try again<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M11.5 7A4.5 4.5 0 1 1 10 3.6M11.5 1.8v2.9H8.6" stroke="#FFFFFF" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></a><a class="link" href="{hud}/#settings">Open Shared MCP</a></div>
</aside>'''

_CARD_DONE = r'''<aside class="card">
  <div class="card-h"><span class="card-t">ALL SET</span><span class="card-s">Doors closed</span></div>
  <ul class="rows">
    <li><span class="sq"></span>Sign-in finished earlier</li>
    <li><span class="sq"></span><span class="mono">{server}</span>stays connected</li>
    <li><span class="sq"></span>Nothing else to do on this page</li>
  </ul>
  <div class="rule"></div>
  <div class="actions"><a class="btn" href="{hud}/">Back to Subway<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M3 7h8M8 4l3 3-3 3" stroke="#FFFFFF" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></a><a class="link" href="{hud}/#settings">Open Shared MCP</a></div>
</aside>'''

_SCENE_OK = r'''<svg class="scene" viewBox="0 0 1312 300" preserveAspectRatio="xMidYMid slice" fill="none" aria-hidden="true"><defs><linearGradient id="sk1" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#07090B"/><stop offset="1" stop-color="#1A2026"/></linearGradient><radialGradient id="gd1" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#FFD54A" stop-opacity="0.55"/><stop offset="0.5" stop-color="#E0A82E" stop-opacity="0.16"/><stop offset="1" stop-color="#E0A82E" stop-opacity="0"/></radialGradient><linearGradient id="sp1" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="#FFFFFF" stop-opacity="0"/><stop offset="1" stop-color="#FFFFFF" stop-opacity="0.85"/></linearGradient></defs><rect width="1312" height="300" fill="url(#sk1)"/><g fill="#0E1215"><rect x="40" y="70" width="90" height="140"/><rect x="150" y="30" width="70" height="180"/><rect x="236" y="96" width="110" height="114"/><rect x="380" y="50" width="64" height="160"/><rect x="470" y="110" width="120" height="100"/><rect x="620" y="20" width="80" height="190"/><rect x="730" y="80" width="100" height="130"/><rect x="860" y="44" width="70" height="166"/><rect x="960" y="100" width="110" height="110"/><rect x="1100" y="60" width="76" height="150"/><rect x="1196" y="90" width="100" height="120"/></g><g fill="#5E6870"><rect x="128" y="70" width="2" height="140"/><rect x="218" y="30" width="2" height="180"/><rect x="442" y="50" width="2" height="160"/><rect x="698" y="20" width="2" height="190"/><rect x="928" y="44" width="2" height="166"/><rect x="1174" y="60" width="2" height="150"/></g><g fill="#E8EEF2"><rect x="56" y="88" width="3" height="4"/><rect x="72" y="88" width="3" height="4"/><rect x="104" y="104" width="3" height="4"/><rect x="64" y="124" width="3" height="4"/><rect x="96" y="140" width="3" height="4"/><rect x="164" y="48" width="3" height="4"/><rect x="196" y="64" width="3" height="4"/><rect x="180" y="96" width="3" height="4"/><rect x="164" y="128" width="3" height="4"/><rect x="204" y="144" width="3" height="4"/><rect x="252" y="112" width="3" height="4"/><rect x="300" y="112" width="3" height="4"/><rect x="396" y="68" width="3" height="4"/><rect x="420" y="100" width="3" height="4"/><rect x="404" y="132" width="3" height="4"/><rect x="490" y="126" width="3" height="4"/><rect x="540" y="142" width="3" height="4"/><rect x="636" y="40" width="3" height="4"/><rect x="668" y="56" width="3" height="4"/><rect x="652" y="88" width="3" height="4"/><rect x="684" y="120" width="3" height="4"/><rect x="748" y="96" width="3" height="4"/><rect x="796" y="112" width="3" height="4"/><rect x="764" y="144" width="3" height="4"/><rect x="876" y="60" width="3" height="4"/><rect x="908" y="92" width="3" height="4"/><rect x="892" y="124" width="3" height="4"/><rect x="980" y="118" width="3" height="4"/><rect x="1028" y="134" width="3" height="4"/><rect x="1116" y="76" width="3" height="4"/><rect x="1148" y="108" width="3" height="4"/><rect x="1216" y="106" width="3" height="4"/><rect x="1264" y="122" width="3" height="4"/></g><g fill="#FFCC00" opacity="0.9"><rect x="88" y="120" width="3" height="4"/><rect x="668" y="104" width="3" height="4"/><rect x="1132" y="92" width="3" height="4"/></g><circle class="glow" cx="1060" cy="170" r="150" fill="url(#gd1)"/><g fill="#1C2126"><rect x="110" y="214" width="28" height="86"/><rect x="430" y="214" width="28" height="86"/><rect x="750" y="214" width="28" height="86"/><rect x="1070" y="214" width="28" height="86"/></g><g fill="#3A4249"><rect x="110" y="214" width="2" height="86"/><rect x="430" y="214" width="2" height="86"/><rect x="750" y="214" width="2" height="86"/><rect x="1070" y="214" width="2" height="86"/></g><rect x="0" y="194" width="1312" height="22" fill="#2A3036"/><rect x="0" y="194" width="1312" height="3" fill="#FFCC00"/><rect x="0" y="201" width="300" height="2.5" fill="#FF3B30"/><rect x="0" y="204.5" width="300" height="2.5" fill="#0A84FF"/><rect x="0" y="208" width="300" height="2.5" fill="#FF9500"/><rect x="0" y="211.5" width="300" height="2.5" fill="#FFCC00"/><path d="M300 201 L340 205 L340 210 L300 214 Z" fill="#00C759"/><rect x="340" y="205" width="720" height="5" fill="#00C759"/><rect x="0" y="186" width="1312" height="8" fill="#3A4046"/><path d="M0 189 H1312" stroke="#5E666D" stroke-width="5" stroke-dasharray="7 7"/><rect x="0" y="182" width="1312" height="3" fill="#8A949B"/><rect x="0" y="182" width="1312" height="1" fill="#E8EEF2"/><rect x="250" y="168" width="100" height="14" fill="#3A4249"/><path d="M262 168 V120 M338 168 V120" stroke="#5E6870" stroke-width="2"/><rect x="254" y="114" width="92" height="8" fill="#E8EEF2"/><rect x="1010" y="168" width="110" height="14" fill="#3A4249"/><path d="M1022 168 V116 M1108 168 V116" stroke="#C8961E" stroke-width="2"/><rect x="1012" y="108" width="106" height="10" fill="#E0A82E"/><rect x="1012" y="108" width="106" height="2" fill="#FFE58A"/><rect x="1012" y="116" width="106" height="2" fill="#8A5A0B"/><g class="spray"><path d="M470 152 H690 L686 180 H470 Z" fill="url(#sp1)" opacity="0.5"/><g stroke="#E8EEF2"><path d="M540 140 H676" stroke-width="1.4" opacity="0.5"/><path d="M600 132 H684" stroke-width="1" opacity="0.35"/><path d="M500 176 H668" stroke-width="1.4" opacity="0.4"/><path d="M590 170 H672" stroke-width="1" opacity="0.3"/></g></g><g class="train"><g transform="translate(0 15)"><path d="M688 134 H862 L936 157 L922 166 H680 L674 150 Z" fill="#FFFFFF" stroke="#07090B" stroke-width="2" stroke-linejoin="miter"/><path d="M678 158 H902 L926 164.5 L922 166 H680 Z" fill="#00C759"/><path d="M684 154.5 H894 L908 157.5 H683 Z" fill="#FFCC00"/><path d="M698 140.5 H854 L874 146.5 H696 Z" fill="#0F2F3A"/><path d="M868 137.5 L926 155 L902 155 L862 142 Z" fill="#0F2F3A"/><path d="M766 134 V166 M846 134 V166" stroke="#07090B" stroke-width="1.2" opacity="0.45"/><circle cx="928" cy="160.5" r="2" fill="#FFE58A"/></g></g><g font-family="Instrument Sans,-apple-system,system-ui,sans-serif" font-size="13" font-weight="600"><rect x="254" y="86" width="116" height="22" rx="6" fill="#07090B" stroke="#2A3036"/><text x="262" y="101" fill="#E8EEF2">Subway</text><rect x="316" y="92" width="10" height="10" fill="#FF3B30"/><rect x="328" y="92" width="10" height="10" fill="#0A84FF"/><rect x="340" y="92" width="10" height="10" fill="#FF9500"/><rect x="352" y="92" width="10" height="10" fill="#FFCC00"/><rect x="1024" y="80" width="{sign_w}" height="22" rx="6" fill="#07090B" stroke="#8A5A0B"/><text x="1032" y="95" fill="#FFE58A">{server}</text></g></svg>'''

_SCENE_ERR = r'''<svg class="scene" viewBox="0 0 1312 300" preserveAspectRatio="xMidYMid slice" fill="none" aria-hidden="true"><defs><linearGradient id="sk2" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#050607"/><stop offset="1" stop-color="#15191E"/></linearGradient><radialGradient id="rd2" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#FF2D20" stop-opacity="0.6"/><stop offset="0.45" stop-color="#FF2D20" stop-opacity="0.14"/><stop offset="1" stop-color="#FF2D20" stop-opacity="0"/></radialGradient></defs><rect width="1312" height="300" fill="url(#sk2)"/><g fill="#0C0F12"><rect x="40" y="70" width="90" height="140"/><rect x="150" y="30" width="70" height="180"/><rect x="236" y="96" width="110" height="114"/><rect x="380" y="50" width="64" height="160"/><rect x="470" y="110" width="120" height="100"/><rect x="620" y="20" width="80" height="190"/><rect x="730" y="80" width="100" height="130"/><rect x="860" y="44" width="70" height="166"/><rect x="960" y="100" width="110" height="110"/><rect x="1100" y="60" width="76" height="150"/><rect x="1196" y="90" width="100" height="120"/></g><g fill="#4A535A"><rect x="128" y="70" width="2" height="140"/><rect x="218" y="30" width="2" height="180"/><rect x="442" y="50" width="2" height="160"/><rect x="698" y="20" width="2" height="190"/><rect x="928" y="44" width="2" height="166"/><rect x="1174" y="60" width="2" height="150"/></g><g fill="#E8EEF2" opacity="0.8"><rect x="56" y="88" width="3" height="4"/><rect x="104" y="104" width="3" height="4"/><rect x="96" y="140" width="3" height="4"/><rect x="164" y="48" width="3" height="4"/><rect x="180" y="96" width="3" height="4"/><rect x="204" y="144" width="3" height="4"/><rect x="300" y="112" width="3" height="4"/><rect x="396" y="68" width="3" height="4"/><rect x="404" y="132" width="3" height="4"/><rect x="540" y="142" width="3" height="4"/><rect x="636" y="40" width="3" height="4"/><rect x="684" y="120" width="3" height="4"/><rect x="796" y="112" width="3" height="4"/><rect x="876" y="60" width="3" height="4"/><rect x="892" y="124" width="3" height="4"/><rect x="1028" y="134" width="3" height="4"/><rect x="1148" y="108" width="3" height="4"/><rect x="1216" y="106" width="3" height="4"/><rect x="1264" y="122" width="3" height="4"/></g><circle cx="640" cy="120" r="120" fill="url(#rd2)"/><g fill="#1C2126"><rect x="110" y="214" width="28" height="86"/><rect x="430" y="214" width="28" height="86"/><rect x="750" y="226" width="28" height="74"/><rect x="1070" y="214" width="28" height="86"/></g><path d="M750 226 L756 220 L764 228 L772 221 L778 226 Z" fill="#1C2126"/><path d="M0 194 H690 L684 205 L692 216 H0 Z" fill="#2A3036"/><path d="M800 194 H1312 V216 H808 L800 207 L806 200 Z" fill="#2A3036"/><rect x="0" y="194" width="688" height="3" fill="#FFCC00"/><rect x="804" y="194" width="508" height="3" fill="#6E6A55"/><rect x="0" y="201" width="300" height="2.5" fill="#FF3B30"/><rect x="0" y="204.5" width="300" height="2.5" fill="#0A84FF"/><rect x="0" y="208" width="300" height="2.5" fill="#FF9500"/><rect x="0" y="211.5" width="300" height="2.5" fill="#FFCC00"/><path d="M300 201 L340 205 L340 210 L300 214 Z" fill="#00C759"/><rect x="340" y="205" width="346" height="5" fill="#00C759"/><rect x="808" y="205" width="252" height="5" fill="#3A4249"/><rect x="0" y="186" width="690" height="8" fill="#3A4046"/><rect x="802" y="186" width="510" height="8" fill="#3A4046"/><path d="M0 189 H686" stroke="#5E666D" stroke-width="5" stroke-dasharray="7 7"/><path d="M806 189 H1312" stroke="#5E666D" stroke-width="5" stroke-dasharray="7 7"/><rect x="0" y="182" width="700" height="3" fill="#8A949B"/><rect x="0" y="182" width="700" height="1" fill="#E8EEF2"/><path d="M700 182 L728 196" stroke="#8A949B" stroke-width="3"/><path d="M790 186 L800 182 H1312" stroke="#8A949B" stroke-width="3"/><g fill="#5E666D"><rect x="716" y="236" width="12" height="5" transform="rotate(20 722 238)"/><rect x="742" y="262" width="12" height="5" transform="rotate(-30 748 264)"/><rect x="784" y="248" width="10" height="5" transform="rotate(12 789 250)"/></g><rect x="250" y="168" width="100" height="14" fill="#3A4249"/><path d="M262 168 V120 M338 168 V120" stroke="#5E6870" stroke-width="2"/><rect x="254" y="114" width="92" height="8" fill="#E8EEF2"/><rect x="1010" y="168" width="110" height="14" fill="#2A3036"/><path d="M1022 168 V116 M1108 168 V116" stroke="#3A4249" stroke-width="2"/><rect x="1012" y="108" width="106" height="10" fill="#2A3036"/><g transform="translate(-300 15)"><path d="M688 134 H862 L936 157 L922 166 H680 L674 150 Z" fill="#FFFFFF" stroke="#07090B" stroke-width="2" stroke-linejoin="miter"/><path d="M678 158 H902 L926 164.5 L922 166 H680 Z" fill="#00C759"/><path d="M684 154.5 H894 L908 157.5 H683 Z" fill="#FFCC00"/><path d="M698 140.5 H854 L874 146.5 H696 Z" fill="#0F2F3A"/><path d="M868 137.5 L926 155 L902 155 L862 142 Z" fill="#0F2F3A"/><path d="M766 134 V166 M846 134 V166" stroke="#07090B" stroke-width="1.2" opacity="0.45"/><circle cx="928" cy="160.5" r="2" fill="#FF2D20"/></g><path d="M650 182 V112" stroke="#4A535A" stroke-width="3"/><rect x="640" y="78" width="20" height="40" rx="3" fill="#0C0F12" stroke="#3A4249"/><circle cx="650" cy="89" r="10" fill="#FF2D20" opacity="0.3"/><circle cx="650" cy="89" r="5.5" fill="#FF2D20"/><circle cx="650" cy="89" r="2" fill="#FFD2CC"/><circle cx="650" cy="106" r="5" fill="#1C2126"/><g font-family="Instrument Sans,-apple-system,system-ui,sans-serif" font-size="13" font-weight="600"><rect x="254" y="86" width="116" height="22" rx="6" fill="#07090B" stroke="#2A3036"/><text x="262" y="101" fill="#E8EEF2">Subway</text><rect x="316" y="92" width="10" height="10" fill="#FF3B30"/><rect x="328" y="92" width="10" height="10" fill="#0A84FF"/><rect x="340" y="92" width="10" height="10" fill="#FF9500"/><rect x="352" y="92" width="10" height="10" fill="#FFCC00"/><rect x="672" y="80" width="150" height="22" rx="6" fill="#07090B" stroke="#FF2D20"/><text x="680" y="95" fill="#FF3B30" font-family="JetBrains Mono,ui-monospace,Menlo,monospace" font-size="11" font-weight="700">STOP · {error_code_short}</text><rect x="1024" y="80" width="{sign_w}" height="22" rx="6" fill="#07090B" stroke="#2A3036"/><text x="1032" y="95" fill="#5E6870">{server}</text></g></svg>'''

_SCENE_DONE = r'''<svg class="scene" viewBox="0 0 1312 300" preserveAspectRatio="xMidYMid slice" fill="none" aria-hidden="true"><defs><linearGradient id="sk3" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#1F6E86"/><stop offset="0.65" stop-color="#2692A6"/><stop offset="1" stop-color="#8FC6CC"/></linearGradient><radialGradient id="sn3" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#FFF4C2" stop-opacity="0.9"/><stop offset="0.35" stop-color="#FFE58A" stop-opacity="0.35"/><stop offset="1" stop-color="#FFE58A" stop-opacity="0"/></radialGradient></defs><rect width="1312" height="300" fill="url(#sk3)"/><circle cx="1150" cy="54" r="110" fill="url(#sn3)"/><circle cx="1150" cy="54" r="16" fill="#FFF4C2"/><g><rect x="150" y="84" width="118" height="14" rx="7" fill="#74A5A9"/><circle cx="178" cy="78" r="22" fill="#F7F9F3"/><circle cx="210" cy="64" r="30" fill="#F7F9F3"/><circle cx="246" cy="78" r="20" fill="#F7F9F3"/><rect x="156" y="78" width="108" height="14" fill="#F7F9F3"/><rect x="160" y="90" width="100" height="4" fill="#C9DADB"/></g><g><rect x="470" y="50" width="86" height="10" rx="5" fill="#74A5A9"/><circle cx="492" cy="46" r="15" fill="#F7F9F3"/><circle cx="516" cy="36" r="21" fill="#F7F9F3"/><circle cx="542" cy="46" r="14" fill="#F7F9F3"/><rect x="476" y="46" width="76" height="10" fill="#F7F9F3"/><rect x="480" y="55" width="70" height="3" fill="#C9DADB"/></g><g><rect x="820" y="72" width="140" height="16" rx="8" fill="#74A5A9"/><circle cx="852" cy="66" r="24" fill="#F7F9F3"/><circle cx="890" cy="50" r="34" fill="#F7F9F3"/><circle cx="932" cy="66" r="24" fill="#F7F9F3"/><rect x="828" y="66" width="128" height="16" fill="#F7F9F3"/><rect x="832" y="80" width="120" height="4" fill="#C9DADB"/></g><path d="M0 168 C150 122 300 138 420 150 C560 120 700 108 860 138 C1000 122 1150 112 1312 132 V210 H0 Z" fill="#3E8088"/><path d="M0 186 C180 160 340 168 520 176 C700 160 880 156 1040 170 C1160 162 1240 160 1312 164 V210 H0 Z" fill="#2F6A70"/><g fill="#5D8E95"><rect x="40" y="110" width="90" height="100"/><rect x="380" y="96" width="64" height="114"/><rect x="1100" y="104" width="76" height="106"/><rect x="1196" y="124" width="100" height="86"/></g><g fill="#B7D6D9"><rect x="128" y="110" width="2" height="100"/><rect x="442" y="96" width="2" height="114"/><rect x="1174" y="104" width="2" height="106"/></g><g fill="#DCEBEC"><rect x="56" y="124" width="3" height="4"/><rect x="88" y="140" width="3" height="4"/><rect x="104" y="160" width="3" height="4"/><rect x="396" y="112" width="3" height="4"/><rect x="420" y="136" width="3" height="4"/><rect x="1116" y="120" width="3" height="4"/><rect x="1148" y="148" width="3" height="4"/><rect x="1216" y="140" width="3" height="4"/><rect x="1264" y="160" width="3" height="4"/></g><g fill="#9AA3A8"><rect x="110" y="214" width="28" height="86"/><rect x="430" y="214" width="28" height="86"/><rect x="750" y="214" width="28" height="86"/><rect x="1070" y="214" width="28" height="86"/></g><g fill="#D3D9DC"><rect x="136" y="214" width="2" height="86"/><rect x="456" y="214" width="2" height="86"/><rect x="776" y="214" width="2" height="86"/><rect x="1096" y="214" width="2" height="86"/></g><rect x="0" y="194" width="1312" height="22" fill="#B4BBC0"/><rect x="0" y="194" width="1312" height="3" fill="#FFCC00"/><rect x="0" y="201" width="300" height="2.5" fill="#FF3B30"/><rect x="0" y="204.5" width="300" height="2.5" fill="#0A84FF"/><rect x="0" y="208" width="300" height="2.5" fill="#FF9500"/><rect x="0" y="211.5" width="300" height="2.5" fill="#FFCC00"/><path d="M300 201 L340 205 L340 210 L300 214 Z" fill="#00C759"/><rect x="340" y="205" width="972" height="5" fill="#00C759"/><rect x="0" y="186" width="1312" height="8" fill="#7D858B"/><path d="M0 189 H1312" stroke="#A3AAAF" stroke-width="5" stroke-dasharray="7 7"/><rect x="0" y="182" width="1312" height="3" fill="#5E666D"/><rect x="0" y="182" width="1312" height="1" fill="#FFFFFF"/><rect x="560" y="168" width="480" height="14" fill="#C9D0D4"/><rect x="560" y="168" width="480" height="2" fill="#FFCC00"/><path d="M580 168 V106 M800 168 V106 M1020 168 V106" stroke="#5E666D" stroke-width="3"/><rect x="548" y="96" width="504" height="10" fill="#FFFFFF" stroke="#0F2F3A" stroke-width="1.5"/><rect x="548" y="106" width="504" height="3" fill="#74A5A9"/><g transform="translate(0 15)"><path d="M688 134 H862 L936 157 L922 166 H680 L674 150 Z" fill="#FFFFFF" stroke="#07090B" stroke-width="2" stroke-linejoin="miter"/><path d="M678 158 H902 L926 164.5 L922 166 H680 Z" fill="#00C759"/><path d="M684 154.5 H894 L908 157.5 H683 Z" fill="#FFCC00"/><path d="M698 140.5 H854 L874 146.5 H696 Z" fill="#0F2F3A"/><path d="M868 137.5 L926 155 L902 155 L862 142 Z" fill="#0F2F3A"/><path d="M766 134 V166 M846 134 V166" stroke="#07090B" stroke-width="1.2" opacity="0.45"/><circle cx="928" cy="160.5" r="2" fill="#FFE58A"/></g><g font-family="Instrument Sans,-apple-system,system-ui,sans-serif" font-size="13" font-weight="600"><rect x="600" y="116" width="116" height="22" rx="6" fill="#0F2F3A"/><text x="608" y="131" fill="#FFFFFF">Subway</text><rect x="662" y="122" width="10" height="10" fill="#FF3B30"/><rect x="674" y="122" width="10" height="10" fill="#0A84FF"/><rect x="686" y="122" width="10" height="10" fill="#FF9500"/><rect x="698" y="122" width="10" height="10" fill="#FFCC00"/><rect x="840" y="116" width="{sign_w}" height="22" rx="6" fill="#0F2F3A" stroke="#E0A82E"/><text x="848" y="131" fill="#FFE58A">{server}</text></g></svg>'''

_OLD_BRAND = '<svg width="22" height="30" viewBox="20 10 182 248" fill="none" aria-hidden="true"><path d="M190 56 H80 A14 14 0 0 0 66 70 V100 A14 14 0 0 0 80 114 H140 A50 50 0 0 1 190 168 V198 A50 50 0 0 1 140 248 H30" stroke="#F7C51E" stroke-width="13" stroke-linecap="round"/><path d="M190 38 H80 A32 32 0 0 0 48 70 V100 A32 32 0 0 0 80 132 H140 A32 32 0 0 1 172 168 V198 A32 32 0 0 1 140 230 H30" stroke="#2C86B0" stroke-width="13" stroke-linecap="round"/><path d="M190 20 H80 A50 50 0 0 0 30 70 V100 A50 50 0 0 0 80 150 H140 A14 14 0 0 1 154 168 V198 A14 14 0 0 1 140 212 H30" stroke="#E2382B" stroke-width="13" stroke-linecap="round"/></svg>'
_LOGO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hud_next", "logo")

def _brand_mark() -> str:
    """One of the four Subway marks, picked at random, sized for the header."""
    try:
        name = random.choice(("S1s", "S2s", "S3s", "S4s"))
        with open(os.path.join(_LOGO_DIR, name + ".svg"), "r", encoding="utf-8") as f:
            svg = f.read().strip()
        return svg.replace('width="252" height="312"', 'width="22" height="27" aria-hidden="true"', 1)
    except OSError:
        return _OLD_BRAND

def _pretty_server_name(server_key: str) -> str:
    name = str(server_key or "")
    for prefix in ("mcp-", "mcp_"):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
            break
    for suffix in ("-mcp", "_mcp"):
        if name.lower().endswith(suffix):
            name = name[:-len(suffix)]
            break
    parts = [p for p in re.split(r"[-_. ]+", name) if p]
    if not parts:
        return "Your server"
    pretty = " ".join(p[:1].upper() + p[1:] for p in parts)
    return pretty[:24]


def _hud_base() -> str:
    try:
        from hud_server import lifecycle as _lc
        return _lc._hud_url(_lc._hud_port()).rstrip("/")
    except Exception:
        return "http://127.0.0.1:7890"


def _render_page(state, server_key: str, error_code: str = "") -> str:
    pretty = _pretty_server_name(server_key)
    server = html.escape(pretty, quote=True)
    if state is True or state == "ok":
        state_class = "ok"
        status = "Sign-in finished on this Mac"
        eyebrow = "CONNECTED"
        title = f"{server} is on the line."
        body = f"Your agents can use {server} from any thread now. You can close this tab."
        card = _CARD_OK
        scene = _SCENE_OK
        error_text = ""
        error_code_clean = ""
        error_code_short = ""
    elif state is False or state == "err":
        state_class = "err"
        status = "Sign-in stopped on this Mac"
        eyebrow = "SIGN-IN DIDN'T FINISH"
        title = f"{server} isn't connected."
        body = (
            "The train stopped at the signal. Nothing was saved, and your other tools "
            "still work. Go back to Subway and press Connect again."
        )
        card = _CARD_ERR
        scene = _SCENE_ERR
        error_code_clean = "".join(ch for ch in str(error_code or "").lower() if ("a" <= ch <= "z" or "0" <= ch <= "9" or ch == "_"))[:40] or "unknown"
        error_text = f"The sign-in was cancelled or {server} refused it. No token was stored."
        error_code_short = error_code_clean.replace("_", " ")[:18]
    elif state == "done":
        state_class = "done"
        status = "Sign-in finished on this Mac"
        eyebrow = "ALL DONE"
        title = "You can close this tab."
        body = f"Subway already has what it needs from {server}. Head back to your thread."
        card = _CARD_DONE
        scene = _SCENE_DONE
        error_text = ""
        error_code_clean = ""
        error_code_short = ""
    else:
        raise ValueError(f"unknown oauth callback state: {state!r}")
    sign_w = min(200, 16 + 8 * len(pretty))
    page = _PAGE_TEMPLATE
    page = page.replace("{card}", card)
    page = page.replace("{scene}", scene)
    replacements = {
        "{state_class}": html.escape(state_class, quote=True),
        "{status}": html.escape(status, quote=True),
        "{eyebrow}": html.escape(eyebrow, quote=True),
        "{title}": html.escape(title, quote=True),
        "{body}": html.escape(body, quote=True),
        "{server}": server,
        "{error_code}": html.escape(error_code_clean, quote=True),
        "{error_text}": html.escape(error_text, quote=True),
        "{error_code_short}": html.escape(error_code_short, quote=True),
        "{sign_w}": str(sign_w),
        "{hud}": html.escape(_hud_base(), quote=True),
    }
    for k, v in replacements.items():
        page = page.replace(k, v)
    mark = _brand_mark()
    page = page.replace("{brand}", mark)
    page = page.replace("{favicon}", '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,' + urllib.parse.quote(mark) + '">')
    return page



def _own_secret(server_key: str) -> str:
    import oauth_clients
    secret = oauth_clients.client_secret(server_key)
    if not secret:
        raise OAuthConnectError("your OAuth app's client secret is missing from the Keychain")
    return secret


@dataclass
class _FlowState:
    server_key: str
    server_url: str
    token_endpoint: str
    client_id: str
    redirect_uri: str
    verifier: str
    state: str
    scopes: list[str]
    resource: str
    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    result: Optional[dict] = None
    error: Optional[BaseException] = None
    handled: bool = False
    httpd: Any = None
    client_auth: str = ""


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server_version = "SubwayOAuth/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return

    def _send_page(self, state, error_code: str = "") -> None:
        flow: _FlowState = self.server.flow  # type: ignore[attr-defined]
        body = _render_page(state, flow.server_key, error_code).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src data:")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _send_404(self) -> None:
        body = b"Not Found"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self) -> None:  # noqa: N802
        flow: _FlowState = self.server.flow  # type: ignore[attr-defined]
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self._send_404()
            return

        with flow.lock:
            if flow.handled:
                self._send_page("done")
                flow.done.set()
                threading.Thread(target=self.server.server_close, daemon=True).start()
                return
            flow.handled = True

        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        try:
            if "error" in params:
                raise OAuthConnectError("authorization failed")
            state_vals = params.get("state", [])
            code_vals = params.get("code", [])
            state = state_vals[0] if state_vals else ""
            code = code_vals[0] if code_vals else ""
            if not state or not code or not hmac.compare_digest(state, flow.state):
                raise OAuthStateMismatch("state mismatch")
            _validate_token_endpoint(flow.token_endpoint)
            token = _post_form_no_redirect(
                flow.token_endpoint,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": flow.redirect_uri,
                    "client_id": flow.client_id,
                    "code_verifier": flow.verifier,
                    **({"client_secret": _own_secret(flow.server_key)} if flow.client_auth == "secret" else {}),
                    "resource": flow.resource,
                },
                timeout=10.0,
            )
            if "error" in token:
                raise OAuthConnectError("token exchange failed")
            access_token = token.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise OAuthConnectError("missing access_token")
            refresh_token = token.get("refresh_token")
            if refresh_token is not None and not isinstance(refresh_token, str):
                refresh_token = None
            expires_in = token.get("expires_in", 3600)
            try:
                expires_in_val = int(expires_in)
            except Exception:
                expires_in_val = 3600
            if expires_in_val <= 0:
                expires_in_val = 3600
            expires_at = int(time.time()) + expires_in_val
            scopes_val = token.get("scope")
            if isinstance(scopes_val, str) and scopes_val.strip():
                scopes = scopes_val.split()
            else:
                scopes = list(flow.scopes)
            token_ep = token.get("token_endpoint") if isinstance(token.get("token_endpoint"), str) else flow.token_endpoint
            client_id = token.get("client_id") if isinstance(token.get("client_id"), str) else flow.client_id
            record = {
                "server": flow.server_key,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
                "scopes": scopes,
                "token_endpoint": token_ep,
                "client_id": client_id,
                "resource": flow.resource,
                "status": "connected",
                "client_auth": flow.client_auth,
            }
            _save_token(flow.server_key, record)
            public = {
                "server": flow.server_key,
                "status": "connected",
                "scopes": scopes,
                "expires_at": expires_at,
                "resource": flow.resource,
            }
            with flow.lock:
                flow.result = public
        except BaseException as e:  # noqa: BLE001
            with flow.lock:
                flow.error = e
            err_vals = params.get("error", [])
            err_code = err_vals[0] if err_vals else "sign_in_failed"
            self._send_page(False, error_code=err_code)
        else:
            self._send_page(True)
        finally:
            flow.done.set()
            threading.Thread(target=self.server.server_close, daemon=True).start()


class ConnectSession:
    def __init__(self, flow: _FlowState, thread: threading.Thread):
        self._flow = flow
        self._thread = thread
        self.authorize_url = flow.state and ""  # will be set properly
        self.scopes = list(flow.scopes)
        self.redirect_uri = flow.redirect_uri

    def wait(self, timeout: Optional[float] = None) -> dict:
        if timeout is None:
            remaining = self._flow.deadline - time.time()
        else:
            remaining = min(timeout, self._flow.deadline - time.time())
        if remaining <= 0:
            self.cancel()
            raise OAuthConnectTimeout("connect timed out")
        finished = self._flow.done.wait(remaining)
        if not finished:
            self.cancel()
            raise OAuthConnectTimeout("connect timed out")
        err = self._flow.error
        if err is not None:
            if isinstance(err, OAuthConnectError):
                raise err
            raise OAuthConnectError(str(err)) from err
        if self._flow.result is None:
            raise OAuthConnectError("connect failed")
        return dict(self._flow.result)

    def cancel(self) -> None:
        self._flow.done.set()
        try:
            if self._flow.httpd is not None:
                self._flow.httpd.server_close()
        except Exception:
            pass


def _register_client(registration_endpoint: str, redirect_uri: str) -> str:
    payload = {
        "client_name": "Subway",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    obj = _post_json(registration_endpoint, payload, timeout=10.0)
    if "client_secret" in obj and obj.get("client_secret"):
        raise OAuthConnectError(
            "server issued a client secret; confidential clients are not supported yet"
        )
    client_id = obj.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise OAuthConnectError("client registration missing client_id")
    return client_id


def _bind_listener(port: int) -> http.server.HTTPServer:
    httpd = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    # ensure quick shutdown
    httpd.timeout = 0.5
    return httpd


def start_connect(
    server_key: str,
    server_url: str,
    *,
    scopes: Optional[list[str]] = None,
    timeout: float = 120.0,
) -> ConnectSession:
    pr = oauth_discovery.discover_protected_resource(server_url)
    auth_servers = pr.get("authorization_servers") or []
    if not auth_servers:
        raise OAuthConnectError("no authorization_servers discovered")
    issuer = auth_servers[0]
    as_meta = oauth_discovery.discover_authorization_server(issuer)
    registration_endpoint = as_meta.get("registration_endpoint")
    authorization_endpoint = as_meta["authorization_endpoint"]
    token_endpoint = as_meta["token_endpoint"]

    resource = pr.get("resource", server_url)
    pr_scopes = pr.get("scopes_supported") or []
    if scopes is not None:
        use_scopes = list(scopes)
    elif isinstance(pr_scopes, list) and pr_scopes:
        use_scopes = list(pr_scopes)
    else:
        use_scopes = []

    import oauth_clients
    own = oauth_clients.get_client(server_key)
    clients = {} if own else _load_clients()
    issuer_cache = clients.get(issuer) if isinstance(clients.get(issuer), dict) else None
    cached_client_id = issuer_cache.get("client_id") if issuer_cache else None
    cached_redirect = issuer_cache.get("redirect_uri") if issuer_cache else None
    cached_port = None
    if isinstance(cached_redirect, str):
        try:
            pu = urllib.parse.urlparse(cached_redirect)
            if pu.hostname in ("127.0.0.1", "localhost") and pu.port:
                cached_port = int(pu.port)
        except Exception:
            cached_port = None

    httpd = None
    client_id = None
    redirect_uri = None
    if isinstance(cached_client_id, str) and cached_client_id and cached_port:
        try:
            httpd = _bind_listener(cached_port)
            client_id = cached_client_id
            redirect_uri = cached_redirect
        except OSError:
            httpd = None

    if httpd is None:
        # need bind (and possibly register)
        try:
            httpd = _bind_listener(0)
        except OSError as e:
            raise OAuthConnectError(f"failed to bind loopback listener: {e}") from e
        port = httpd.server_address[1]
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        if own:
            client_id = own["client_id"]
        elif isinstance(cached_client_id, str) and cached_client_id and issuer_cache and issuer_cache.get("redirect_uri") == redirect_uri:
            client_id = cached_client_id
        else:
            if not registration_endpoint:
                try:
                    httpd.server_close()
                except Exception:
                    pass
                import oauth_device
                dev = oauth_device.builtin_client(issuer, as_meta)
                if dev:
                    return oauth_device.start_device(server_key, as_meta, dev, scopes=use_scopes,
                                                     resource=resource, timeout=timeout)
                raise OAuthConnectError(
                    f"{server_key} does not let apps register themselves. Add your own OAuth app: subway mcp client {server_key} --client-id ID"
                )
            client_id = _register_client(registration_endpoint, redirect_uri)
            clients[issuer] = {"client_id": client_id, "redirect_uri": redirect_uri}
            _save_clients(clients)

    verifier, challenge = _make_pkce()
    state = secrets.token_urlsafe(32)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": resource,
    }
    if use_scopes:
        params["scope"] = " ".join(use_scopes)
    authorize_url = authorization_endpoint + ("&" if "?" in authorization_endpoint else "?") + urllib.parse.urlencode(params)

    deadline = time.time() + float(timeout)
    flow = _FlowState(
        server_key=server_key,
        server_url=server_url,
        token_endpoint=token_endpoint,
        client_id=client_id,
        redirect_uri=redirect_uri,
        verifier=verifier,
        state=state,
        scopes=list(use_scopes),
        resource=resource,
        deadline=deadline,
    )
    httpd.flow = flow  # type: ignore[attr-defined]
    flow.httpd = httpd
    flow.client_auth = "secret" if own and own.get("has_secret") else ""

    def _serve() -> None:
        try:
            while not flow.done.is_set() and time.time() < flow.deadline:
                httpd.handle_request()
            if not flow.done.is_set():
                with flow.lock:
                    if flow.error is None and not flow.handled:
                        flow.error = OAuthConnectTimeout("connect timed out")
                flow.done.set()
        except Exception as e:
            with flow.lock:
                if flow.error is None:
                    flow.error = e
            flow.done.set()
        finally:
            try:
                httpd.server_close()
            except Exception:
                pass

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()

    session = ConnectSession(flow, thread)
    session.authorize_url = authorize_url
    return session
