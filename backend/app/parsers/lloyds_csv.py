"""Lloyds CSV parser (spec §14.1).

Lloyds exports use separate Debit/Credit columns:
Transaction Date, Transaction Type, Sort Code, Account Number,
Transaction Description, Debit Amount, Credit Amount, Balance.
The signed amount is credit - debit. No currency column -> defaults to GBP.
"""

from __future__ import annotations

from decimal import Decimal

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


class LloydsCsvParser(BaseStatementParser):
    parser_id = "lloyds_csv"
    institution = "Lloyds"

    SIGNATURE = {"Transaction Date", "Transaction Description", "Debit Amount", "Credit Amount"}

    def can_parse(self, filename: str, content: bytes) -> bool:
        if "lloyds" in filename.lower():
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
            date_str = get_field(row, "Transaction Date")
            description = get_field(row, "Transaction Description")
            debit = get_field(row, "Debit Amount")
            credit = get_field(row, "Credit Amount")
            if not date_str or not description:
                raise ParseError(f"Lloyds row {i}: missing date/description")
            if debit:
                amount = -abs(parse_amount(debit))
            elif credit:
                amount = abs(parse_amount(credit))
            else:
                amount = Decimal("0")
            out.append(
                StandardTransaction(
                    transaction_date=parse_date(date_str),
                    amount=amount,
                    currency="GBP",
                    description_raw=description,
                    category_hint=get_field(row, "Transaction Type"),
                    account_hint=get_field(row, "Account Number"),
                )
            )
        return out
