"""Barclays CSV parser (spec §14.1).

Barclays exports use columns: Number, Date, Account, Amount, Subcategory, Memo.
Amounts are signed (negative = debit). No currency column -> defaults to GBP.
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


class BarclaysCsvParser(BaseStatementParser):
    parser_id = "barclays_csv"
    institution = "Barclays"

    SIGNATURE = {"Number", "Date", "Account", "Amount", "Memo"}

    def can_parse(self, filename: str, content: bytes) -> bool:
        if "barclays" in filename.lower():
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
            memo = get_field(row, "Memo")
            if not date_str or amount_str is None or not memo:
                raise ParseError(f"Barclays row {i}: missing date/amount/memo")
            out.append(
                StandardTransaction(
                    transaction_date=parse_date(date_str),
                    amount=parse_amount(amount_str),
                    currency="GBP",
                    description_raw=memo,
                    category_hint=get_field(row, "Subcategory"),
                    account_hint=get_field(row, "Account"),
                )
            )
        return out
