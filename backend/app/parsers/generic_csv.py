"""Generic CSV parser with a configurable column mapping (spec §14.4).

Used when no bank-specific parser matches. The user maps columns in the UI and
the mapping is saved as a reusable import profile. When no mapping is given, a
best-effort heuristic maps common header names so a preview can still be shown.
"""

from __future__ import annotations

from decimal import Decimal

from app.parsers.base import (
    BaseStatementParser,
    ParseError,
    StandardTransaction,
    detect_month_first,
    parse_amount,
    parse_date,
    parse_optional_date,
    read_csv_rows,
)

# Logical fields the user can map a CSV column to, in UI order. The minimum to
# import is a date AND (amount OR debit/credit) — enforced in the mapping UI.
LOGICAL_FIELDS: list[dict[str, object]] = [
    {"key": "date", "label": "Date", "required": True},
    {"key": "amount", "label": "Amount (signed)", "required": False},
    {"key": "debit", "label": "Money out", "required": False},
    {"key": "credit", "label": "Money in", "required": False},
    {"key": "description", "label": "Description", "required": False},
    {"key": "merchant", "label": "Merchant / payee", "required": False},
    {"key": "currency", "label": "Currency", "required": False},
    {"key": "category", "label": "Category", "required": False},
    {"key": "external_id", "label": "Transaction ID", "required": False},
    {"key": "posted_date", "label": "Posted date", "required": False},
]

# Logical field -> candidate header names (lowercase) for heuristic mapping.
_HEURISTICS: dict[str, tuple[str, ...]] = {
    "date": ("date", "transaction date", "started date", "posted date"),
    "posted_date": ("posted date", "completed date", "value date"),
    "description": ("description", "memo", "details", "narrative", "reference", "name"),
    "merchant": ("merchant", "name", "payee"),
    "amount": ("amount", "value"),
    "debit": ("debit", "debit amount", "money out", "paid out", "withdrawal"),
    "credit": ("credit", "credit amount", "money in", "paid in", "deposit"),
    "currency": ("currency",),
    "category": ("category", "subcategory"),
    "external_id": ("transaction id", "id", "reference"),
}


def suggest_mapping(headers: list[str]) -> dict[str, str]:
    """Best-effort {logical_field: header} guess from common header names — the
    starting point the user adjusts in the mapping UI."""
    lowered = {h.lower(): h for h in headers}
    resolved: dict[str, str] = {}
    for field, candidates in _HEURISTICS.items():
        for cand in candidates:
            if cand in lowered:
                resolved[field] = lowered[cand]
                break
    return resolved


def _mapped(row: dict[str, str], m: dict[str, str], field: str) -> str | None:
    """The raw cell for a logical ``field``, or None when the field isn't mapped."""
    return row.get(m[field]) if field in m else None


def _build_transaction(
    row: dict[str, str], m: dict[str, str], amount: Decimal, default_currency: str,
    *, month_first: bool = False,
) -> StandardTransaction:
    """Assemble one StandardTransaction from a mapped row (amount already resolved)."""
    description = _mapped(row, m, "description") or _mapped(row, m, "merchant") or "(no description)"
    currency = _mapped(row, m, "currency") or default_currency
    return StandardTransaction(
        transaction_date=parse_date(row[m["date"]], month_first=month_first),
        posted_date=parse_optional_date(_mapped(row, m, "posted_date"), month_first=month_first),
        amount=amount,
        currency=currency.upper(),
        description_raw=description,
        merchant_raw=_mapped(row, m, "merchant"),
        category_hint=_mapped(row, m, "category"),
        external_id=_mapped(row, m, "external_id"),
    )


class GenericCsvParser(BaseStatementParser):
    parser_id = "generic_csv"
    institution = "Generic"

    def __init__(
        self,
        mapping: dict[str, str] | None = None,
        default_currency: str = "GBP",
        *,
        month_first: bool | None = None,
    ) -> None:
        # mapping: logical field -> actual header name in the file.
        self.mapping = mapping
        self.default_currency = default_currency
        # None = auto-detect date order per file (default). Set True/False to force
        # US month-first (MM/DD) / UK day-first (DD/MM) — needed when every date is
        # ambiguous (all day components ≤ 12), where auto-detection has no evidence.
        self.month_first = month_first

    def can_parse(self, filename: str, content: bytes) -> bool:
        # Generic is the fallback: it can parse anything with a date column and
        # either an amount or a debit/credit column.
        try:
            headers, _ = read_csv_rows(content)
        except Exception:
            return False
        resolved = self._resolve_mapping(headers)
        has_date = "date" in resolved
        has_amount = "amount" in resolved or "debit" in resolved or "credit" in resolved
        return has_date and has_amount

    def _resolve_mapping(self, headers: list[str]) -> dict[str, str]:
        """Return {logical_field: actual_header} using the explicit mapping or
        heuristics."""
        if self.mapping:
            # Keep only mappings whose target header actually exists.
            present = {h.lower(): h for h in headers}
            return {
                field: present.get(header.lower(), header)
                for field, header in self.mapping.items()
                if header and header.lower() in present
            }
        return suggest_mapping(headers)

    def parse(self, filename: str, content: bytes) -> list[StandardTransaction]:
        headers, rows = read_csv_rows(content)
        m = self._resolve_mapping(headers)
        if "date" not in m:
            raise ParseError("Generic CSV: could not identify a date column")
        if not ({"amount", "debit", "credit"} & m.keys()):
            raise ParseError("Generic CSV: could not identify an amount column")

        # Resolve the date order once for the whole file (US MM/DD vs UK DD/MM). An
        # explicit override wins; otherwise infer it so an ambiguous-but-consistent
        # statement imports with the right dates (SR / US-format). The override is the
        # only way to fix an all-ambiguous US file (every day ≤ 12), where detection
        # has no evidence and correctly stays on the day-first default.
        month_first = (
            self.month_first
            if self.month_first is not None
            else detect_month_first(row.get(m["date"], "") for row in rows)
        )

        out: list[StandardTransaction] = []
        for i, row in enumerate(rows, start=1):
            date_str = row.get(m["date"])
            if not date_str:
                raise ParseError(f"Generic row {i}: missing date")
            amount = self._row_amount(row, m, i)
            out.append(_build_transaction(row, m, amount, self.default_currency, month_first=month_first))
        return out

    def _row_amount(self, row: dict[str, str], m: dict[str, str], i: int) -> Decimal:
        if "amount" in m and row.get(m["amount"]):
            return parse_amount(row[m["amount"]])
        debit = row.get(m["debit"]) if "debit" in m else None
        credit = row.get(m["credit"]) if "credit" in m else None
        if debit:
            return -abs(parse_amount(debit))
        if credit:
            return abs(parse_amount(credit))
        raise ParseError(f"Generic row {i}: missing amount")
