"""Deterministic privacy assessment for worker briefs."""

import re
from typing import Dict, Optional, Tuple

from threadlog import PRIVATE_WORDS


_SECRET_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("private-key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("API token", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("GitHub token", re.compile(r"\bghp_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[bpas]-")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
    ("password assignment", re.compile(r"\bpassword\s*[:=]\s*\S{6,}", re.IGNORECASE)),
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_SSN = re.compile(r"(?<!\d)(?!000|666|9\d\d)\d{3}[- ]?(?!00)\d{2}[- ]?(?!0000)\d{4}(?!\d)")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", re.IGNORECASE)
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_SENSITIVE_RECORD = re.compile(
    r"\b(?:patients?|diagnoses|salary|payroll|medical\s+records?)\b.{0,80}"
    r"\b(?:data|records?|lists?|csv|exports?)\b"
    r"|\b(?:data|records?|lists?|csv|exports?)\b.{0,80}"
    r"\b(?:patients?|diagnoses|salary|payroll|medical\s+records?)\b",
    re.IGNORECASE | re.DOTALL,
)


def _phrase_pattern(phrase: str) -> Optional[re.Pattern]:
    words = phrase.strip().split()
    if not words or (len(words) == 1 and words[0].lower() == "private"):
        return None
    body = r"\s+".join(re.escape(word) for word in words)
    if phrase.lower() == PRIVATE_WORDS[0].lower():
        return re.compile(r"\b" + body + r"\b(?!\s+clients?\b)", re.IGNORECASE)
    return re.compile(r"\b" + body + r"\b", re.IGNORECASE)


_EXPLICIT_PHRASES = tuple(
    pattern
    for pattern in (
        _phrase_pattern(phrase)
        for phrase in list(PRIVATE_WORDS) + [
            "nda", "under nda", "internal only", "do not share", "proprietary",
        ]
    )
    if pattern is not None
)


def _luhn_valid(number: str) -> bool:
    digits = [int(char) for char in number if char.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _personal_data_match(text: str) -> str:
    if len(_EMAIL.findall(text)) >= 3:
        return "multiple email addresses"
    if len(_PHONE.findall(text)) >= 3:
        return "multiple phone numbers"
    if _SSN.search(text):
        return "SSN"
    if _IBAN.search(text):
        return "IBAN"
    if any(_luhn_valid(candidate.group()) for candidate in _CARD_CANDIDATE.finditer(text)):
        return "payment card"
    if _SENSITIVE_RECORD.search(text):
        return "sensitive records"
    return ""


def assess(text: str) -> Dict[str, object]:
    """Return whether text appears private without exposing matched secret values."""
    content = text if isinstance(text, str) else str(text or "")
    for pattern in _EXPLICIT_PHRASES:
        if pattern.search(content):
            return {
                "private": True,
                "reason": "explicit privacy phrase",
                "kind": "explicit_phrase",
                "match": "privacy phrase",
            }
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(content):
            return {
                "private": True,
                "reason": "credential-like content",
                "kind": "secret",
                "match": label,
            }
    personal_match = _personal_data_match(content)
    if personal_match:
        return {
            "private": True,
            "reason": "personal data pattern",
            "kind": "personal_data",
            "match": personal_match,
        }
    return {"private": False, "reason": "", "kind": "none", "match": ""}


def provider_policy(private: bool, reason: str, mode: str, explicit: bool,
                    provider: str, local_model_available: bool) -> Dict[str, str]:
    """Return a user-facing block or warning for a flagged task's provider."""
    privacy_mode = mode if mode in ("local_only", "auto", "off") else "auto"
    if not private:
        return {"error": "", "warning": ""}
    if privacy_mode in ("local_only", "off") and not local_model_available:
        return {
            "error": "Error: This task is private but no local model is installed. "
                     "Install one, or set Settings → Privacy routing to auto.",
            "warning": "",
        }
    if explicit and provider not in ("auto", "local"):
        return {
            "error": f"Error: this task was explicitly marked private ({reason}), "
                     f"so it can't go to {provider}. Use provider='local'.",
            "warning": "",
        }
    if provider not in ("auto", "local") and (
        privacy_mode in ("local_only", "off") or local_model_available
    ):
        return {
            "error": f"Error: this task is private ({reason}), so it can't go to {provider}. "
                     "Change Settings → Privacy routing to allow cloud providers.",
            "warning": "",
        }
    if provider == "auto" and explicit and not local_model_available:
        return {
            "error": f"Error: this task was explicitly marked private ({reason}), "
                     "but no local model is installed.",
            "warning": "",
        }
    if privacy_mode == "auto" and not local_model_available and not explicit and provider != "local":
        return {
            "error": "",
            "warning": f"Privacy: flagged ({reason}); no local model, so it goes to {provider}. "
                       "Reject it if it has to remain on this machine.",
        }
    return {"error": "", "warning": ""}
