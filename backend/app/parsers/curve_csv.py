"""Curve CSV parser (spec §14.1, §14.3).

Curve exports include Date, Description, Amount, Currency and Card columns
(spec §14.3). Amounts are signed (negative = spend).
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
    parse_optional_date,
    read_csv_rows,
)


class CurveCsvParser(BaseStatementParser):
    parser_id = "curve_csv"
    institution = "Curve"

    SIGNATURE = {"Date", "Description", "Amount", "Currency", "Card"}

    def can_parse(self, filename: str, content: bytes) -> bool:
        if "curve" in filename.lower():
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
            description = get_field(row, "Description") or get_field(row, "Merchant")
            if not date_str or amount_str is None or not description:
                raise ParseError(f"Curve row {i}: missing date/amount/description")
            out.append(
                StandardTransaction(
                    transaction_date=parse_date(date_str),
                    posted_date=parse_optional_date(get_field(row, "Completed Date")),
                    amount=parse_amount(amount_str),
                    currency=(get_field(row, "Currency") or "GBP").upper(),
                    description_raw=description,
                    merchant_raw=get_field(row, "Merchant"),
                    category_hint=get_field(row, "Category"),
                    card_hint=get_field(row, "Card"),
                )
            )
        return out
