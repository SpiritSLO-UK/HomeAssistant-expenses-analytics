"""Barclaycard credit-card export parser.

Unlike the Barclays *bank* export (comma-separated, with a header), the
Barclaycard credit-card export is **tab-separated with no header row**. Columns
by position::

    Date(DD-Mon-YY)  Description  Network(Visa/n/a)  Cardholder  Category  Credit  Debit

* **Debit** (col 6) — purchases, shown positive → money out (negated here).
* **Credit** (col 5) — a bill payment / refund, which the statement signs
  negative → money in (taken as positive), mirroring the Lloyds debit/credit
  split.

Most purchases are ``Crv*…`` — Curve overlay charges funded by this card — so the
descriptions carry the ``CRV`` marker the cross-account dedup keys on
(curve_link_service): once this card is linked to its Curve "Card Name", the same
spend on both statements is recognised as a duplicate.
"""

from __future__ import annotations

import csv
import io
import re

from app.parsers.base import (
    BaseStatementParser,
    ParseError,
    StandardTransaction,
    parse_amount,
    parse_date,
)

# A first column like "05-Jun-26" (or a 4-digit year) — the headerless format's
# only reliable signature.
_DATE_RE = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{2,4}$")

_COL_DATE = 0
_COL_DESCRIPTION = 1
_COL_CATEGORY = 4
_COL_CREDIT = 5  # bill payment / refund (money in)
_COL_DEBIT = 6  # purchase (money out)


def _col(fields: list[str], idx: int) -> str:
    return fields[idx].strip() if idx < len(fields) else ""


class BarclaycardCsvParser(BaseStatementParser):
    parser_id = "barclaycard_csv"
    institution = "Barclaycard"

    @staticmethod
    def _rows(content: bytes) -> list[list[str]]:
        text = content.decode("utf-8-sig", errors="replace")
        # The saved file is comma-separated (with the comma-containing description
        # and "1,234.56" amounts quoted); a copy-paste out of a spreadsheet is
        # tab-separated. Sniff the first non-empty line so either works.
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        delimiter = "\t" if "\t" in first else ","
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        return [row for row in reader if any((c or "").strip() for c in row)]

    def can_parse(self, filename: str, content: bytes) -> bool:
        if "barclaycard" in filename.lower().replace(" ", ""):
            return True
        try:
            rows = self._rows(content)
        except Exception:
            return False
        # Headerless + a DD-Mon-YY first column is the signature (no header row to
        # match on). Comma or tab delimited.
        return bool(rows) and len(rows[0]) >= 6 and bool(_DATE_RE.match(_col(rows[0], _COL_DATE)))

    def parse(self, filename: str, content: bytes) -> list[StandardTransaction]:
        out: list[StandardTransaction] = []
        for i, fields in enumerate(self._rows(content), start=1):
            date_str = _col(fields, _COL_DATE)
            description = _col(fields, _COL_DESCRIPTION)
            if not date_str or not description:
                raise ParseError(f"Barclaycard row {i}: missing date/description")
            credit = _col(fields, _COL_CREDIT)
            debit = _col(fields, _COL_DEBIT)
            if debit:
                amount = -abs(parse_amount(debit))  # purchase → money out
            elif credit:
                amount = abs(parse_amount(credit))  # payment/refund → money in
            else:
                raise ParseError(f"Barclaycard row {i}: no debit/credit amount")
            out.append(
                StandardTransaction(
                    transaction_date=parse_date(date_str),
                    amount=amount,
                    currency="GBP",
                    # Keep the raw description (incl. any "Crv*" marker) so the
                    # cross-account Curve dedup can recognise these rows.
                    description_raw=description,
                    merchant_raw=description,
                    category_hint=_col(fields, _COL_CATEGORY) or None,
                )
            )
        return out
