"""Parser interface and shared helpers (spec §13, §35).

Every CSV/statement parser converts a raw file into a list of
``StandardTransaction`` objects. The import service then normalises, dedupes
and persists them. Amounts are signed: negative = money out (debit), positive =
money in (credit), matching the standard transaction format in spec §13.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# Date formats tried in order. Day-first (DD/MM/YYYY) by default since the spec
# targets UK banks (Curve, Barclays, Lloyds, Monzo); the numeric d/m/y forms are
# ambiguous, so US month-first variants below are tried too (order per file —
# see parse_date / detect_month_first).
_DATE_FORMATS = (
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d/%m/%y",
    "%d %b %Y",
    "%d %B %Y",
    "%Y/%m/%d",
    # Barclaycard exports use e.g. "05 Jun 26" / "05-Jun-26" (2- or 4-digit year).
    "%d %b %y",
    "%d-%b-%Y",
    "%d-%b-%y",
)

# US month-first numeric variants (MM/DD/YYYY). Tried as a fallback by default — so a
# lone US date doesn't hard-fail — or first when a file is detected as month-first.
_MONTH_FIRST_FORMATS = ("%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y", "%m/%d/%y")

# A purely numeric d/m/y date (the only ambiguous kind). ISO and named-month dates
# are unambiguous and deliberately don't match, so they don't sway detection.
_NUMERIC_DATE_RE = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-]\d{2,4}\s*$")


# A real statement is at most a few thousand rows; cap to bound memory + import
# work against a maliciously huge CSV (DoS — CR-SEC-9). The upload byte cap
# (CR-SEC-8) already bounds the file size; this bounds the parsed row count.
MAX_CSV_ROWS = 100_000


class ParseError(ValueError):
    """Raised when a row cannot be parsed into a StandardTransaction."""


@dataclass
class StandardTransaction:
    transaction_date: date
    amount: Decimal
    currency: str
    description_raw: str
    posted_date: date | None = None
    merchant_raw: str | None = None
    external_id: str | None = None
    account_hint: str | None = None
    category_hint: str | None = None
    card_hint: str | None = None
    # The underlying funding card this row was charged to, as labelled by the
    # source (e.g. Curve's "Card Name ••1234"). Used for cross-account dedup of
    # overlay/pass-through cards like Curve, where the same spend also lands on
    # the funding card's own statement. None for ordinary statements.
    funding_source: str | None = None
    # True for money-in rows a parser can identify as income (e.g. earned Curve
    # Cash cashback). Sets the transaction's is_income flag.
    is_income: bool = False
    # When set, the import forces this library category on the row (highest
    # precedence) — for synthetic rows like earned Curve Cash → "income.cashback"
    # whose merchant text would otherwise keyword-match the wrong category.
    category_library_id: str | None = None
    # Set by low-confidence parsers (e.g. PDF) so the import flags the row for
    # the user to verify (spec §11 review-heavy import).
    needs_review: bool = False

    @property
    def direction(self) -> str:
        return "credit" if self.amount >= 0 else "debit"


def parse_date(value: str | None, *, month_first: bool = False) -> date:
    """Parse a date string using the known formats.

    Numeric d/m/y dates are ambiguous; ``month_first`` (detected per file by
    :func:`detect_month_first`) tries the US MM/DD order first. Either way the other
    order is a fallback, so a lone US/UK date still parses rather than hard-failing.
    The default stays day-first (DD/MM), the app's default locale.
    """
    if value is None or not value.strip():
        raise ParseError("empty date")
    text = value.strip()
    ordered = (
        (*_MONTH_FIRST_FORMATS, *_DATE_FORMATS)
        if month_first
        else (*_DATE_FORMATS, *_MONTH_FIRST_FORMATS)
    )
    for fmt in ordered:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ParseError(f"unrecognised date: {value!r}")


def parse_optional_date(value: str | None, *, month_first: bool = False) -> date | None:
    if value is None or not value.strip():
        return None
    try:
        return parse_date(value, month_first=month_first)
    except ParseError:
        return None


def detect_month_first(values: Iterable[str | None]) -> bool:
    """Infer whether a column of numeric d/m/y dates is US month-first (MM/DD) rather
    than the day-first (UK DD/MM) default — so a whole US statement imports correctly.

    Flips to month-first only on positive, uncontradicted evidence: a value whose 2nd
    component exceeds 12 can't be day-first (no month > 12), so the file is month-first
    — unless some other value's 1st component exceeds 12 (which proves day-first). An
    all-ambiguous, mixed/contradictory, or non-numeric column stays day-first.
    """
    day_first = month_first = False
    for value in values:
        match = _NUMERIC_DATE_RE.match(value or "")
        if match is None:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12 and second <= 12:
            day_first = True
        elif second > 12 and first <= 12:
            month_first = True
    return month_first and not day_first


def parse_amount(value: str | None) -> Decimal:
    """Parse a monetary string into a signed Decimal.

    Handles thousands separators, currency symbols, and accounting-style
    parentheses for negatives, e.g. ``(42.18)`` -> ``-42.18``.
    """
    if value is None or not str(value).strip():
        raise ParseError("empty amount")
    text = str(value).strip()

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    # Strip currency symbols and spaces; keep digits, separators and sign.
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".,-+")
    # Remove thousands separators (commas), keep the decimal point.
    cleaned = cleaned.replace(",", "")
    if not cleaned or cleaned in {"-", "+", "."}:
        raise ParseError(f"unrecognised amount: {value!r}")

    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ParseError(f"unrecognised amount: {value!r}") from exc

    return -amount if negative else amount


def read_csv_rows(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Decode CSV bytes and return (headers, list of row dicts).

    Tolerates a UTF-8 BOM and blank trailing lines.
    """
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    rows: list[dict[str, str]] = []
    for raw in reader:
        # Normalise keys (strip whitespace) and skip fully empty rows.
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        if any(row.values()):
            rows.append(row)
            if len(rows) > MAX_CSV_ROWS:
                raise ParseError(f"CSV has too many rows (limit {MAX_CSV_ROWS:,}).")
    return headers, rows


def headers_match(headers: list[str], required: set[str]) -> bool:
    """True if every required header (case-insensitive) is present."""
    lowered = {h.lower() for h in headers}
    return {r.lower() for r in required}.issubset(lowered)


def get_field(row: dict[str, str], *names: str) -> str | None:
    """Return the first present, case-insensitive field value from ``names``."""
    lowered = {k.lower(): v for k, v in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value:
            return value
    return None


class BaseStatementParser:
    """Base class for all statement parsers (spec §35)."""

    parser_id: str
    institution: str
    format: str = "csv"

    def can_parse(self, filename: str, content: bytes) -> bool:
        raise NotImplementedError

    def parse(self, filename: str, content: bytes) -> list[StandardTransaction]:
        raise NotImplementedError
