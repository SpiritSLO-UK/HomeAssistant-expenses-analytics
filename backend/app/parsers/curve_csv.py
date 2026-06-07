"""Curve CSV parser (spec §14.1, §14.3).

Two Curve export shapes are supported:

1. The simplified columns used by our sample/demo files
   (``Date, Description, Amount, Currency, Card, Category``) where ``Amount`` is
   already signed (negative = spend).

2. The real Curve app statement export, whose columns are::

       Export For, Date (YYYY-MM-DD as UTC), Time (HH:MM:SS as UTC), Merchant,
       Txn Amount (Funding Card), Txn Currency (Funding Card),
       Txn Amount (Merchant), Txn Currency (Merchant), Card Name,
       Card Last 4 Digits, Type, Category, Notes, Fees

   Here ``Txn Amount (Funding Card)`` is the charge applied to the underlying
   funding card and is exported **positive for a spend** (e.g. a £3.69 purchase
   shows as ``3.69``), so we negate it to match our convention (negative = money
   out). Refunds/credits, which Curve exports negative, therefore become
   positive (money in).
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


def _field_by_prefix(row: dict[str, str], prefix: str) -> str | None:
    """First non-empty value whose header starts with ``prefix`` (case-insensitive).

    Curve's real export labels columns with parenthetical detail
    (``Date (YYYY-MM-DD as UTC)``, ``Txn Amount (Funding Card)``) whose exact
    text we don't want to hard-code, so we match on the stable prefix. Header
    keys are already whitespace-stripped by ``read_csv_rows``.
    """
    pl = prefix.lower()
    for key, value in row.items():
        if key.lower().startswith(pl) and value:
            return value
    return None


def _funding_label(name: str | None, last4: str | None) -> str | None:
    """Build a stable funding-card label (e.g. ``Credit Card ••1006``).

    This is the identity the user maps to a real account so a Curve row can be
    deduped against the underlying card's own statement (see curve_link_service).
    """
    name = (name or "").strip()
    last4 = (last4 or "").strip()
    if name and last4:
        return f"{name} ••{last4}"
    return name or None


class CurveCsvParser(BaseStatementParser):
    parser_id = "curve_csv"
    institution = "Curve"

    # Simplified sample/demo format.
    SIGNATURE = {"Date", "Description", "Amount", "Currency", "Card"}

    @staticmethod
    def _is_app_export(headers: list[str]) -> bool:
        """True for the real Curve app export (has a Funding Card amount column)."""
        return any(h.lower().startswith("txn amount (funding") for h in headers)

    def can_parse(self, filename: str, content: bytes) -> bool:
        if "curve" in filename.lower():
            return True
        try:
            headers, _ = read_csv_rows(content)
        except Exception:
            return False
        return headers_match(headers, self.SIGNATURE) or self._is_app_export(headers)

    def parse(self, filename: str, content: bytes) -> list[StandardTransaction]:
        headers, rows = read_csv_rows(content)
        app_export = self._is_app_export(headers)
        out: list[StandardTransaction] = []
        for i, row in enumerate(rows, start=1):
            # Date header is "Date" (simplified) or "Date (YYYY-MM-DD as UTC)".
            date_str = _field_by_prefix(row, "date")
            # Description is "Description" (simplified) or "Merchant" (app export).
            description = get_field(row, "Description", "Merchant")
            if app_export:
                amount_str = _field_by_prefix(row, "txn amount (funding")
                currency = _field_by_prefix(row, "txn currency (funding")
                funding = _funding_label(
                    get_field(row, "Card Name"), get_field(row, "Card Last 4 Digits")
                )
            else:
                amount_str = get_field(row, "Amount")
                currency = get_field(row, "Currency")
                funding = get_field(row, "Card")
            if not date_str or amount_str is None or not description:
                raise ParseError(f"Curve row {i}: missing date/amount/description")
            amount = parse_amount(amount_str)
            if app_export:
                # Funding-card charge is positive for a spend; flip to our
                # convention (negative = money out).
                amount = -amount
            out.append(
                StandardTransaction(
                    transaction_date=parse_date(date_str),
                    posted_date=parse_optional_date(get_field(row, "Completed Date")),
                    amount=amount,
                    currency=(currency or "GBP").upper(),
                    description_raw=description,
                    merchant_raw=get_field(row, "Merchant"),
                    category_hint=get_field(row, "Category"),
                    card_hint=get_field(row, "Card", "Card Name"),
                    funding_source=funding,
                )
            )
        return out
