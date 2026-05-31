"""Receipt field extraction (spec §21.2 level 1).

Pure ``text -> {merchant, date, total, vat, currency}`` — no engine, no I/O — so
it's fully unit-testable independently of the OCR step (``ocr_service``).
Heuristic by design; uncertain results flow to the review queue (spec §21.3).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# A money amount, optionally currency-prefixed: £12.34, 1,234.56, 12.34
_AMOUNT = r"(?:[£$€]\s?)?(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2})"
_AMOUNT_RE = re.compile(_AMOUNT)
_CURRENCY_SYMBOL = {"£": "GBP", "$": "USD", "€": "EUR"}

# Date patterns -> strptime formats.
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"), "%Y-%m-%d"),
    (re.compile(r"\b(\d{2}/\d{2}/\d{4})\b"), "%d/%m/%Y"),
    (re.compile(r"\b(\d{2}-\d{2}-\d{4})\b"), "%d-%m-%Y"),
    (re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b"), "%d.%m.%Y"),
    (re.compile(r"\b(\d{2}/\d{2}/\d{2})\b"), "%d/%m/%y"),
    (re.compile(r"\b(\d{1,2} [A-Za-z]{3,9} \d{4})\b"), "%d %b %Y"),
]


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _amounts_on(line: str) -> list[Decimal]:
    out = []
    for m in _AMOUNT_RE.finditer(line):
        d = _to_decimal(m.group(1))
        if d is not None:
            out.append(d)
    return out


def detect_currency(text: str) -> str | None:
    for sym, code in _CURRENCY_SYMBOL.items():
        if sym in text:
            return code
    m = re.search(r"\b(GBP|USD|EUR|JPY|CHF|AUD|CAD)\b", text, re.IGNORECASE)
    return m.group(1).upper() if m else None


def detect_date(text: str) -> date | None:
    for pattern, fmt in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            try:
                parsed = datetime.strptime(m.group(1), fmt).date()
            except ValueError:
                continue
            # Reject implausible dates (e.g. OCR noise far in the future/past).
            if 2000 <= parsed.year <= 2100:
                return parsed
    return None


def detect_total(text: str) -> Decimal | None:
    lines = text.splitlines()
    # Prefer a line that names the total but isn't a subtotal.
    keyword = re.compile(r"\b(grand\s*total|total\s*to\s*pay|amount\s*due|balance\s*due|total)\b", re.I)
    subtotal = re.compile(r"\bsub[\s-]*total\b", re.I)
    candidates: list[Decimal] = []
    for line in lines:
        if subtotal.search(line):
            continue
        if keyword.search(line):
            amts = _amounts_on(line)
            if amts:
                candidates.append(max(amts))
    if candidates:
        return max(candidates)
    # Fallback: the largest money amount anywhere.
    all_amounts = _amounts_on(text)
    return max(all_amounts) if all_amounts else None


def detect_vat(text: str) -> Decimal | None:
    for line in text.splitlines():
        if re.search(r"\b(vat|tax)\b", line, re.I):
            amts = _amounts_on(line)
            if amts:
                return max(amts)
    return None


def detect_merchant(text: str) -> str | None:
    # The merchant is usually the first meaningful line (not a number/date/total).
    for line in text.splitlines():
        s = line.strip()
        if len(s) < 2:
            continue
        if _AMOUNT_RE.fullmatch(s) or detect_date(s):
            continue
        letters = sum(c.isalpha() for c in s)
        if letters >= 2 and letters >= len(s) * 0.4:
            return s[:300]
    return None


def extract_fields(text: str) -> dict:
    """Best-effort level-1 fields + a 0..1 parse confidence."""
    merchant = detect_merchant(text)
    receipt_date = detect_date(text)
    total = detect_total(text)
    vat = detect_vat(text)
    currency = detect_currency(text)

    found = sum(x is not None for x in (merchant, receipt_date, total))
    parse_confidence = round(found / 3, 2)  # the 3 fields that matter for matching

    return {
        "merchant_raw": merchant,
        "receipt_date": receipt_date,
        "total_amount": total,
        "vat_amount": vat,
        "currency": currency,
        "parse_confidence": parse_confidence,
    }
