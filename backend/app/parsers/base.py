"""Parser interface and shared helpers (spec §13, §35).

Every CSV/statement parser converts a raw file into a list of
``StandardTransaction`` objects. The import service then normalises, dedupes
and persists them. Amounts are signed: negative = money out (debit), positive =
money in (credit), matching the standard transaction format in spec §13.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# Date formats tried in order. UK-first (DD/MM/YYYY) since the spec targets UK
# banks (Curve, Barclays, Lloyds, Monzo).
_DATE_FORMATS = (
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d/%m/%y",
    "%d %b %Y",
    "%d %B %Y",
    "%Y/%m/%d",
    # Barclaycard exports use e.g. "05-Jun-26" (and the 4-digit variant).
    "%d-%b-%Y",
    "%d-%b-%y",
)


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


def parse_date(value: str | None) -> date:
    """Parse a date string using the known formats."""
    if value is None or not value.strip():
        raise ParseError("empty date")
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ParseError(f"unrecognised date: {value!r}")


def parse_optional_date(value: str | None) -> date | None:
    if value is None or not value.strip():
        return None
    try:
        return parse_date(value)
    except ParseError:
        return None


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
