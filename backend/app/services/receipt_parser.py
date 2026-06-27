"""Receipt field extraction (spec §21.2 level 1).

Pure ``text -> {merchant, date, total, vat, currency}`` — no engine, no I/O — so
it's fully unit-testable independently of the OCR step (``ocr_service``).
Heuristic by design; uncertain results flow to the review queue (spec §21.3).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# A money amount, optionally currency-prefixed. Captures thousands-grouped numbers
# (1,234 / 1,234.56 / 1.234,56) and plain decimals using EITHER separator with 1–2
# decimal places (12.34, 12,50, 45.5) — so EU comma-decimals and short decimals are
# no longer dropped. Bare integers are deliberately not matched (avoids treating
# quantities / years as money). _to_decimal works out which separator is the point.
_AMOUNT = r"(?:[£$€]\s?)?(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+[.,]\d{1,2})"
_AMOUNT_RE = re.compile(_AMOUNT)
_CURRENCY_SYMBOL = {"£": "GBP", "$": "USD", "€": "EUR"}

# Date patterns -> the strptime formats to try in order. The numeric d/m/y forms are
# ambiguous, so we try day-first (the app's default) then month-first — a US receipt's
# 09/15/2024 then parses as 2024-09-15 instead of being silently dropped.
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"), ["%Y-%m-%d"]),
    (re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b"), ["%d/%m/%Y", "%m/%d/%Y"]),
    (re.compile(r"\b(\d{1,2}-\d{1,2}-\d{4})\b"), ["%d-%m-%Y", "%m-%d-%Y"]),
    (re.compile(r"\b(\d{1,2}\.\d{1,2}\.\d{4})\b"), ["%d.%m.%Y", "%m.%d.%Y"]),
    (re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2})\b"), ["%d/%m/%y", "%m/%d/%y"]),
    (re.compile(r"\b(\d{1,2} [A-Za-z]{3,9} \d{4})\b"), ["%d %b %Y"]),
]


def _normalise_single_sep(raw: str, sep: str) -> str:
    """Normalise a number that uses only ``sep``: multiple separators, or a single one
    with exactly 3 trailing digits, are thousands grouping (1,234 / 1.234 / 1,234,567
    → drop them); 1–2 trailing digits is the decimal point (12,50 → 12.50, 45.5)."""
    parts = raw.split(sep)
    if len(parts) > 2 or len(parts[-1]) == 3:
        return raw.replace(sep, "")
    return raw.replace(sep, ".")


def _to_decimal(raw: str) -> Decimal | None:
    raw = raw.strip()
    if "." in raw and "," in raw:
        # Both present: the LAST separator is the decimal point, the other is thousands.
        dec = "." if raw.rfind(".") > raw.rfind(",") else ","
        norm = raw.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif "," in raw:
        norm = _normalise_single_sep(raw, ",")
    elif "." in raw:
        norm = _normalise_single_sep(raw, ".")
    else:
        norm = raw
    try:
        return Decimal(norm).quantize(Decimal("0.01"))
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
    for pattern, fmts in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            for fmt in fmts:  # day-first, then month-first for ambiguous numeric dates
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


# A clean merchant header is short. Anything much longer is almost certainly a
# run-on OCR line (e.g. a card-payment slip collapsed onto one line) rather than a
# shop name, so we don't accept it as the merchant.
_MAX_MERCHANT_LEN = 60

# Payment-terminal / card-slip boilerplate that is never the merchant name. Lines
# matching this are skipped when guessing the merchant, so a "merchant copy" debit
# slip (REG/SESSION/PAN/Terminal ID/PAYMENT APPROVED…) doesn't leak into the field.
_NON_MERCHANT_RE = re.compile(
    r"\b(?:"
    r"cardholder|card|debit|credit|payment|terminal|merchant\s*id|pan\s*(?:seq|no)|"
    r"application\s*id|aid|txn|trx|transaction|session|approved|verification|"
    r"visa|mastercard|maestro|amex|contactless|sequence|seq\s*no|reg\s*no|"
    r"vat\s*(?:no|reg)|receipt|invoice|customer\s*copy|merchant\s*copy"
    r")\b",
    re.I,
)


def detect_merchant(text: str) -> str | None:
    # The merchant is usually the first short, mostly-alphabetic line that isn't a
    # number/date/total or payment-terminal boilerplate. Skipping the boilerplate
    # (and over-long run-on lines) avoids dumping card-slip gibberish in the field;
    # when nothing qualifies we return None → the user fills it in via review.
    for line in text.splitlines():
        s = line.strip()
        if len(s) < 2 or len(s) > _MAX_MERCHANT_LEN:
            continue
        if _AMOUNT_RE.fullmatch(s) or detect_date(s):
            continue
        if _NON_MERCHANT_RE.search(s):
            continue
        letters = sum(c.isalpha() for c in s)
        if letters >= 2 and letters >= len(s) * 0.4:
            return s[:_MAX_MERCHANT_LEN]
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
