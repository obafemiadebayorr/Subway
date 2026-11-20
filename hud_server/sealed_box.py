"""Sealed box helpers for collaboration sessions.

Stdlib only. No AEAD library is available in this environment (no
`cryptography`, no `nacl`), so this module implements an HMAC-verified
stream construction:

  key   = HKDF-SHA256(PBKDF2-HMAC-SHA256(join_code, salt=session_id, ...))
  ct    = plaintext XOR keystream(HMAC-SHA256(enc_key, nonce || counter))
  tag   = HMAC-SHA256(mac_key, nonce || ct)   (encrypt-then-MAC)

This is authenticated encryption in the Encrypt-then-MAC sense, not AES-GCM.
If an AEAD lib is added later, swap seal/open_box internals; the wire format
(dict with nonce/ct/tag, base64) can stay.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Any, Dict

_PBKDF2_ROUNDS = 100_000
_NONCE_LEN = 16


def _hkdf(ikm: bytes, info: bytes, length: int = 32) -> bytes:
    # HKDF-Extract with a fixed salt, then Expand (single block is enough for 32B).
    prk = hmac.new(b"subway-sealed-box-v1", ikm, hashlib.sha256).digest()
    okm = b""
    t = b""
    i = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t
        i += 1
    return okm[:length]


def derive_key(join_code: str, session_id: str, rotation_salt: str = "") -> bytes:
    """Derive a 32-byte session key from the join code and session id."""
    if not isinstance(join_code, str) or not join_code:
        raise ValueError("join_code required")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id required")
    salt = (session_id + ":" + (rotation_salt or "")).encode("utf-8")
    ikm = hashlib.pbkdf2_hmac("sha256", join_code.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return _hkdf(ikm, b"subway-session-key")


def _subkeys(key: bytes):
    return _hkdf(key, b"enc"), _hkdf(key, b"mac")


def _keystream(enc_key: bytes, nonce: bytes, n: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < n:
        out += hmac.new(enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return out[:n]


def seal(key: bytes, plaintext: bytes) -> Dict[str, str]:
    if not isinstance(plaintext, (bytes, bytearray)):
        raise TypeError("plaintext must be bytes")
    enc_key, mac_key = _subkeys(key)
    nonce = secrets.token_bytes(_NONCE_LEN)
    ct = bytes(p ^ k for p, k in zip(plaintext, _keystream(enc_key, nonce, len(plaintext))))
    tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"nonce": b64(nonce), "ct": b64(ct), "tag": b64(tag)}


def open_box(key: bytes, blob: Dict[str, Any]) -> bytes:
    try:
        nonce = base64.b64decode(blob["nonce"])
        ct = base64.b64decode(blob["ct"])
        tag = base64.b64decode(blob["tag"])
    except Exception as exc:
        raise ValueError("malformed sealed blob") from exc
    enc_key, mac_key = _subkeys(key)
    expect = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expect, tag):
        raise ValueError("sealed box authentication failed")
    return bytes(c ^ k for c, k in zip(ct, _keystream(enc_key, nonce, len(ct))))
