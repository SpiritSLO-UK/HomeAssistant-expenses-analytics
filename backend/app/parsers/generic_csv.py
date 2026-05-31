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
    parse_amount,
    parse_date,
    parse_optional_date,
    read_csv_rows,
)

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


class GenericCsvParser(BaseStatementParser):
    parser_id = "generic_csv"
    institution = "Generic"

    def __init__(
        self,
        mapping: dict[str, str] | None = None,
        default_currency: str = "GBP",
    ) -> None:
        # mapping: logical field -> actual header name in the file.
        self.mapping = mapping
        self.default_currency = default_currency

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
        lowered = {h.lower(): h for h in headers}
        resolved: dict[str, str] = {}
        for field, candidates in _HEURISTICS.items():
            for cand in candidates:
                if cand in lowered:
                    resolved[field] = lowered[cand]
                    break
        return resolved

    def parse(self, filename: str, content: bytes) -> list[StandardTransaction]:
        headers, rows = read_csv_rows(content)
        m = self._resolve_mapping(headers)
        if "date" not in m:
            raise ParseError("Generic CSV: could not identify a date column")
        if not ({"amount", "debit", "credit"} & m.keys()):
            raise ParseError("Generic CSV: could not identify an amount column")

        out: list[StandardTransaction] = []
        for i, row in enumerate(rows, start=1):
            date_str = row.get(m["date"])
            if not date_str:
                raise ParseError(f"Generic row {i}: missing date")

            amount = self._row_amount(row, m, i)
            description = (
                (row.get(m["description"]) if "description" in m else None)
                or (row.get(m["merchant"]) if "merchant" in m else None)
                or "(no description)"
            )
            currency = (row.get(m["currency"]) if "currency" in m else None) or self.default_currency

            out.append(
                StandardTransaction(
                    transaction_date=parse_date(date_str),
                    posted_date=parse_optional_date(
                        row.get(m["posted_date"]) if "posted_date" in m else None
                    ),
                    amount=amount,
                    currency=currency.upper(),
                    description_raw=description,
                    merchant_raw=row.get(m["merchant"]) if "merchant" in m else None,
                    category_hint=row.get(m["category"]) if "category" in m else None,
                    external_id=row.get(m["external_id"]) if "external_id" in m else None,
                )
            )
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
