"""Redaction utility (spec §22.4, §28.3).

Pure functions that strip personally/financially sensitive tokens from text
before it ever leaves the device (e.g. to a cloud AI provider). This is
defense-in-depth: the primary privacy guarantee is architectural (strict-local
default, no external calls). Redaction only matters once AI is enabled.

Nothing here touches the database or network — it is deliberately easy to test
and reason about.

Backlog: requested in things-to-add-change-consider.md (#7 anonymise bank
details, #13 trim secrets from OCR text before AI).
"""

from __future__ import annotations

import re

# --- Patterns (UK-centric, matching the spec's target banks) ---

# 13-19 digits, optionally grouped by spaces/dashes (card numbers, PANs).
_CARD_RE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
# UK sort code: 12-34-56
_SORT_CODE_RE = re.compile(r"\b\d{2}-\d{2}-\d{2}\b")
# IBAN: 2 letters, 2 digits, then 10-30 alphanumerics.
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
# Standalone 8-digit run (UK account number) — checked after card/IBAN.
_ACCOUNT_RE = re.compile(r"\b\d{8}\b")
# UK postcode, e.g. "SW1A 1AA", "M1 1AE".
_POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b", re.IGNORECASE)
# Email address.
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

CARD_MASK = "[card]"
SORT_MASK = "[sort-code]"
IBAN_MASK = "[iban]"
ACCOUNT_MASK = "[account]"
POSTCODE_MASK = "[postcode]"
EMAIL_MASK = "[email]"


def _mask_card(match: re.Match) -> str:
    """Mask a card number but keep the last 4 digits (useful, non-sensitive)."""
    digits = re.sub(r"\D", "", match.group(0))
    if len(digits) >= 4:
        return f"{CARD_MASK} {digits[-4:]}"
    return CARD_MASK


def redact_text(text: str | None) -> str:
    """Return ``text`` with sensitive tokens replaced by neutral placeholders.

    Order matters: the most specific / longest patterns are applied first so a
    card number isn't partly eaten by the account-number rule.
    """
    if not text:
        return ""
    out = _CARD_RE.sub(_mask_card, text)
    out = _IBAN_RE.sub(IBAN_MASK, out)
    out = _SORT_CODE_RE.sub(SORT_MASK, out)
    out = _ACCOUNT_RE.sub(ACCOUNT_MASK, out)
    out = _POSTCODE_RE.sub(POSTCODE_MASK, out)
    out = _EMAIL_RE.sub(EMAIL_MASK, out)
    return out


def contains_sensitive(text: str | None) -> bool:
    """True if ``text`` appears to contain sensitive tokens (for review flags)."""
    if not text:
        return False
    return text != redact_text(text)


# Fields a cloud AI request is ever allowed to include (spec §22.4 minimal payload).
_ALLOWED_CLOUD_FIELDS = {"description", "amount", "currency", "candidate_categories"}


def redact_for_cloud(payload: dict) -> dict:
    """Build a minimal, redacted payload safe to send to a cloud AI provider.

    Drops every field not on the allow-list and redacts free-text fields. This
    is the single choke point the AI gateway will use (Stage 10) so there is one
    obvious place to audit.
    """
    safe: dict = {}
    for key in _ALLOWED_CLOUD_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, str):
            safe[key] = redact_text(value)
        else:
            safe[key] = value
    return safe
