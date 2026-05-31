"""Monzo CSV parser (spec §14.1).

Monzo exports are wide; the columns we use are:
Transaction ID, Date, Time, Type, Name, Category, Amount, Currency,
Notes and #tags, Description. Amounts are signed (negative = spend).
"""

from __future__ import annotations

from app.parsers.base import (
    BaseStatementParser,
    ParseError,
    StandardTransaction,
    get_field,
    headers_match,
    parse_amount,
    parse_date,
    read_csv_rows,
)


class MonzoCsvParser(BaseStatementParser):
    parser_id = "monzo_csv"
    institution = "Monzo"

    SIGNATURE = {"Transaction ID", "Date", "Amount", "Name", "Currency"}

    def can_parse(self, filename: str, content: bytes) -> bool:
        if "monzo" in filename.lower():
            return True
        try:
            headers, _ = read_csv_rows(content)
        except Exception:
            return False
        return headers_match(headers, self.SIGNATURE)

    def parse(self, filename: str, content: bytes) -> list[StandardTransaction]:
        _, rows = read_csv_rows(content)
        out: list[StandardTransaction] = []
        for i, row in enumerate(rows, start=1):
            date_str = get_field(row, "Date")
            amount_str = get_field(row, "Amount")
            # Description: prefer the merchant Name, fall back to Description/Notes.
            name = get_field(row, "Name")
            description = name or get_field(row, "Description") or get_field(row, "Notes and #tags")
            if not date_str or amount_str is None or not description:
                raise ParseError(f"Monzo row {i}: missing date/amount/name")
            out.append(
                StandardTransaction(
                    transaction_date=parse_date(date_str),
                    amount=parse_amount(amount_str),
                    currency=(get_field(row, "Currency") or "GBP").upper(),
                    description_raw=description,
                    merchant_raw=name,
                    category_hint=get_field(row, "Category"),
                    external_id=get_field(row, "Transaction ID"),
                )
            )
        return out
