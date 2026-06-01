"""Generic PDF bank-statement parser (spec §11 — Stage 11).

PDF statements have no agreed layout, and text extraction loses the column
structure, so this is deliberately **best-effort and review-heavy**: every row
it produces is flagged ``needs_review=True`` for the user to verify (spec §11
acceptance: "transactions are extracted *or flagged for review*").

The text→rows step (``parse_statement_text``) is pure and unit-tested; the PDF
text extraction uses ``pypdf`` (the optional ``ocr`` extra, present in the
add-on). Scanned/image PDFs yield no text and raise a clear error.
"""

from __future__ import annotations

import io
import re

from app.parsers.base import (
    BaseStatementParser,
    ParseError,
    StandardTransaction,
    parse_amount,
    parse_date,
)

# A date at the start of a line (the common statement layout).
_DATE_AT_START = re.compile(
    r"^\s*(\d{2}[/.-]\d{2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b"
)
# A money amount, optionally currency-symbol'd and/or CR/DR-suffixed.
_MONEY = re.compile(r"(?:[£$€]\s?)?\d{1,3}(?:,\d{3})*\.\d{2}(?:\s?(?:CR|DR))?", re.I)
_CURRENCY = {"£": "GBP", "$": "USD", "€": "EUR"}


def _detect_currency(token: str, default: str = "GBP") -> str:
    for sym, code in _CURRENCY.items():
        if sym in token:
            return code
    return default


def parse_statement_text(text: str, default_currency: str = "GBP") -> list[StandardTransaction]:
    """Extract transactions from statement text (best-effort, review-heavy).

    Heuristic per line: a leading date, then the **first** money amount is taken
    as the transaction amount (a trailing balance, if any, is ignored), and the
    text between them is the description. ``CR`` / leading ``+`` means money in;
    otherwise it's treated as money out (debit). Every row is flagged for review.
    """
    out: list[StandardTransaction] = []
    for line in text.splitlines():
        dm = _DATE_AT_START.match(line)
        if not dm:
            continue
        try:
            txn_date = parse_date(dm.group(1))
        except ParseError:
            continue
        money = list(_MONEY.finditer(line, dm.end()))
        if not money:
            continue

        first = money[0]
        token = first.group(0)
        description = line[dm.end():first.start()].strip(" \t-—|") or "Statement transaction"
        description = re.sub(r"\s{2,}", " ", description)

        upper = token.upper()
        is_credit = "CR" in upper or token.strip().startswith("+")
        cleaned = re.sub(r"(?i)\s?(CR|DR)", "", token)
        try:
            magnitude = abs(parse_amount(cleaned))
        except ParseError:
            continue
        amount = magnitude if is_credit else -magnitude

        out.append(
            StandardTransaction(
                transaction_date=txn_date,
                amount=amount,
                currency=_detect_currency(token, default_currency),
                description_raw=description[:300],
                needs_review=True,  # PDF extraction is unverified
            )
        )
    return out


class GenericPdfParser(BaseStatementParser):
    parser_id = "generic_pdf"
    institution = "Generic (PDF)"
    format = "pdf"

    def __init__(self, default_currency: str = "GBP"):
        self.default_currency = default_currency

    def can_parse(self, filename: str, content: bytes) -> bool:
        return filename.lower().endswith(".pdf") or content[:5] == b"%PDF-"

    def parse(self, filename: str, content: bytes) -> list[StandardTransaction]:
        text = self._extract_text(content)
        rows = parse_statement_text(text, self.default_currency)
        if not rows:
            raise ParseError(
                "No transactions recognised in the PDF. Try the CSV export, or check "
                "it's a text statement (scanned PDFs aren't supported yet)."
            )
        return rows

    @staticmethod
    def _extract_text(content: bytes) -> str:
        try:
            import pypdf
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ParseError(
                "PDF support needs the 'ocr' extra (pypdf) — it's installed in the Home Assistant add-on."
            ) from exc
        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:
            raise ParseError(f"Could not read the PDF: {exc}") from exc
        if not text.strip():
            raise ParseError("No selectable text in this PDF (a scanned image? not supported yet).")
        return text
