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

# Guard against pathological inputs causing catastrophic regex backtracking or
# CPU spikes. Legitimate transaction/description text is short; anything past
# this is truncated before the (possibly expensive) regex passes run.
_MAX_REDACT_LEN = 4096

# --- Patterns ---
# Ordered most-specific/longest first (see ``redact_text``). Patterns are kept
# conservative: we mask *obvious* PII shapes and err toward leaving legitimate
# merchant text (store numbers, dates, short refs) untouched.

# 13-19 digits, optionally grouped by spaces/dashes (card numbers, PANs).
_CARD_RE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
# UK sort code: 12-34-56.
_SORT_CODE_RE = re.compile(r"\b\d{2}-\d{2}-\d{2}\b")
# IBAN: 2 letters, 2 digits, then 10-30 alphanumerics (international bank acct).
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
# Generic long digit run (8-17 digits): UK 8-digit account numbers plus longer
# international/domestic account numbers that are NOT card-length (13-19, caught
# above). Kept to >=8 so short store/reference numbers (3-7 digits) survive.
# This generalises the old UK-only ``\b\d{8}\b`` while staying conservative:
# an 8+ digit standalone run in a finance payload is account-number-ish.
_ACCOUNT_RE = re.compile(r"\b\d{8,17}\b")
# UK postcode, e.g. "SW1A 1AA", "M1 1AE".
_POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b", re.IGNORECASE)
# Email address.
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# International phone numbers. Conservative: requires a leading "+"/"00" country
# prefix OR a "(0…)"-style trunk, then 7-14 digits with common separators. The
# leading marker keeps this from eating bare digit runs already covered by the
# account/card rules or ordinary numbers.
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+|00)\d[\d ().-]{6,16}\d"  # +44 20 7946 0958 / 0044-...
    r"|(?<!\w)\(0\d{1,4}\)[\d ().-]{5,12}\d",  # (020) 7946 0958
)

CARD_MASK = "[card]"
SORT_MASK = "[sort-code]"
IBAN_MASK = "[iban]"
ACCOUNT_MASK = "[account]"
POSTCODE_MASK = "[postcode]"
EMAIL_MASK = "[email]"
PHONE_MASK = "[phone]"


def _mask_card(match: re.Match) -> str:
    """Mask a card number but keep the last 4 digits (useful, non-sensitive)."""
    digits = re.sub(r"\D", "", match.group(0))
    if len(digits) >= 4:
        return f"{CARD_MASK} {digits[-4:]}"
    return CARD_MASK


def _apply_masks(text: str) -> str:
    """Run every masking pass over ``text`` (assumed already length-capped)."""
    out = _CARD_RE.sub(_mask_card, text)
    out = _IBAN_RE.sub(IBAN_MASK, out)
    out = _SORT_CODE_RE.sub(SORT_MASK, out)
    # Phone before the account rule: a "+44…" number contains an 8+ digit run
    # that the account rule would otherwise swallow into "[account]".
    out = _PHONE_RE.sub(PHONE_MASK, out)
    out = _ACCOUNT_RE.sub(ACCOUNT_MASK, out)
    out = _POSTCODE_RE.sub(POSTCODE_MASK, out)
    out = _EMAIL_RE.sub(EMAIL_MASK, out)
    return out


def redact_text(text: str | None) -> str:
    """Return ``text`` with sensitive tokens replaced by neutral placeholders.

    Order matters: the most specific / longest patterns are applied first so a
    card number isn't partly eaten by the account-number rule. Input is capped
    at ``_MAX_REDACT_LEN`` to bound regex work on pathological input.
    """
    if not text:
        return ""
    if len(text) > _MAX_REDACT_LEN:
        text = text[:_MAX_REDACT_LEN]
    return _apply_masks(text)


def contains_sensitive(text: str | None) -> bool:
    """True if ``text`` appears to contain sensitive tokens (for review flags)."""
    if not text:
        return False
    # Cap once, then redact once and compare against the SAME capped input so a
    # pathological (over-length) string isn't reported sensitive purely because
    # it was truncated. Previously this redacted the text twice.
    capped = text[:_MAX_REDACT_LEN] if len(text) > _MAX_REDACT_LEN else text
    return _apply_masks(capped) != capped


def _redact_value(value):
    """Recursively redact strings inside a value (str / list / tuple / dict).

    Every free-text string reachable in an outgoing cloud payload — including
    nested lists (e.g. ``candidate_categories``) and dicts — passes through the
    masker. Non-string scalars (numbers, bools, None) are returned unchanged.
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(v) for v in value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    return value


# Fields a cloud AI request is ever allowed to include (spec §22.4 minimal payload).
_ALLOWED_CLOUD_FIELDS = {"description", "amount", "currency", "candidate_categories"}


def redact_for_cloud(payload: dict) -> dict:
    """Build a minimal, redacted payload safe to send to a cloud AI provider.

    Drops every field not on the allow-list and redacts *every* string value in
    the surviving payload — including strings nested inside lists/dicts such as
    ``candidate_categories`` (a renamed category could otherwise carry PII). This
    is the single choke point the AI gateway uses (Stage 10) so there is one
    obvious place to audit.
    """
    safe: dict = {}
    for key in _ALLOWED_CLOUD_FIELDS:
        if key not in payload:
            continue
        safe[key] = _redact_value(payload[key])
    return safe
