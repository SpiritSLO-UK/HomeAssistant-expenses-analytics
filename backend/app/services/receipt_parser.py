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
# The plain-decimal integer part is *bounded* (\d{1,15}, not \d+): a long separator-
# free digit run previously made \d+ backtrack O(n^2) per start offset (ReDoS finding
# #3); a 15-digit cap is far above any real amount yet keeps the scan linear.
_AMOUNT = r"(?:[£$€]\s?)?(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d{1,15}[.,]\d{1,2})"
_AMOUNT_RE = re.compile(_AMOUNT)
# DoS guard (finding #3): cap the per-line text handed to the money regexes so an
# absurdly long OCR/PDF line (a separator-free digit run) can't force heavy scanning.
# Mirrors redaction.py's bounded-input approach; real receipt lines are far shorter.
_MAX_AMOUNT_LINE_CHARS = 4096
# A currency-anchored WHOLE number (no decimals): "TOTAL £42", "TOTAL 42 EUR". Only
# matched where a total is expected (detect_total), so bare quantities/years elsewhere
# aren't mistaken for money. Requires a symbol/code so a stray "12" doesn't win.
# Split into two simpler compiled patterns (symbol-prefixed / code-suffixed) built from
# a shared whole-number sub-pattern, to keep each regex's complexity low. Each captures
# the number in group 1; the two are tried in sequence (see _whole_amounts_on).
# Bounded integer part (\d{1,15}, not \d+) so the code-suffixed pattern below can't
# backtrack O(n^2) on a long separator-free digit run before failing to find a code.
_WHOLE_NUM = r"(\d{1,3}(?:,\d{3})*|\d{1,15})"
_WHOLE_AMOUNT_RES = (
    re.compile(r"[£$€]\s?" + _WHOLE_NUM),
    re.compile(_WHOLE_NUM + r"\s?(?:GBP|USD|EUR)", re.I),
)
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
    line = line[:_MAX_AMOUNT_LINE_CHARS]  # bound regex work on pathological input (#3)
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


def _whole_amounts_on(line: str) -> list[Decimal]:
    """Currency-anchored whole numbers (no decimal part), e.g. "TOTAL £42"."""
    line = line[:_MAX_AMOUNT_LINE_CHARS]  # bound regex work on pathological input (#3)
    out = []
    for pattern in _WHOLE_AMOUNT_RES:
        for m in pattern.finditer(line):
            d = _to_decimal(m.group(1).replace(",", ""))
            if d is not None:
                out.append(d)
    return out


def detect_total(text: str) -> Decimal | None:
    lines = text.splitlines()
    # Prefer a line that names the total but isn't a subtotal.
    keyword = re.compile(r"\b(grand\s*total|total\s*to\s*pay|amount\s*due|balance\s*due|total)\b", re.I)
    subtotal = re.compile(r"\bsub[\s-]*total\b", re.I)
    # Loyalty / savings / points lines often say "total" ("Total savings", "Total
    # points") but carry a rewards figure, not the amount paid — never treat them as
    # the total (spec §21.2: don't let promo lines override the real total).
    loyalty = re.compile(r"\b(savings?|points?|rewards?|loyalty|clubcard|nectar|coupon|voucher|discount)\b", re.I)
    candidates: list[Decimal] = []
    for line in lines:
        if subtotal.search(line) or loyalty.search(line):
            continue
        if keyword.search(line):
            # A decimal money amount is preferred; if the total line has none, accept a
            # currency-anchored whole number ("TOTAL £42") rather than dropping it.
            amts = _amounts_on(line) or _whole_amounts_on(line)
            if amts:
                candidates.append(max(amts))
    if candidates:
        return max(candidates)
    # Fallback: the largest money amount anywhere (still skipping loyalty/savings lines).
    all_amounts = [a for ln in lines if not loyalty.search(ln) for a in _amounts_on(ln)]
    return max(all_amounts) if all_amounts else None


_PERCENT_RE = re.compile(r"\d{1,3}(?:[.,]\d+)?\s*%")


def detect_vat(text: str) -> Decimal | None:
    for line in text.splitlines():
        if re.search(r"\b(vat|tax)\b", line, re.I):
            # Drop "20%"-style rate tokens so the rate isn't mistaken for the amount,
            # then take the SMALLEST money figure: on a "Net 38.00 VAT 4.18" line the
            # VAT is the tax charged (4.18), not the larger net (using max picked net).
            cleaned = _PERCENT_RE.sub(" ", line)
            amts = _amounts_on(cleaned)
            if amts:
                return min(amts)
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
        # Skip an address line ("123 High Street") — a leading street number means it's
        # the address under the header, not the merchant name itself.
        if re.match(r"\d+\s+[A-Za-z]", s):
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
