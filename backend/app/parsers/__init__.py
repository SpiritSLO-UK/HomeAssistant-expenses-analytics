"""Parser registry and detection (spec §14.3).

Bank-specific parsers are tried first (most specific signatures), then the
generic parser as a catch-all fallback.
"""

from __future__ import annotations

from app.parsers.barclays_csv import BarclaysCsvParser
from app.parsers.base import BaseStatementParser, StandardTransaction
from app.parsers.curve_csv import CurveCsvParser
from app.parsers.generic_csv import GenericCsvParser
from app.parsers.generic_pdf import GenericPdfParser
from app.parsers.lloyds_csv import LloydsCsvParser
from app.parsers.monzo_csv import MonzoCsvParser

# Order matters: specific parsers before the generic fallback.
_BANK_PARSERS: list[BaseStatementParser] = [
    CurveCsvParser(),
    BarclaysCsvParser(),
    LloydsCsvParser(),
    MonzoCsvParser(),
]

PARSERS_BY_ID: dict[str, BaseStatementParser] = {p.parser_id: p for p in _BANK_PARSERS}
PARSERS_BY_ID[GenericCsvParser.parser_id] = GenericCsvParser()
PARSERS_BY_ID[GenericPdfParser.parser_id] = GenericPdfParser()


def available_parsers() -> list[dict[str, str]]:
    """List of {parser_id, institution} for the UI parser selector."""
    items = [{"parser_id": p.parser_id, "institution": p.institution} for p in _BANK_PARSERS]
    items.append({"parser_id": "generic_csv", "institution": "Generic (CSV)"})
    items.append({"parser_id": "generic_pdf", "institution": "Generic (PDF)"})
    return items


def _looks_like_pdf(filename: str, content: bytes) -> bool:
    return filename.lower().endswith(".pdf") or content[:5] == b"%PDF-"


def detect_parser(filename: str, content: bytes) -> BaseStatementParser:
    """Return the best parser for a file, falling back to the generic parsers."""
    for parser in _BANK_PARSERS:
        try:
            if parser.can_parse(filename, content):
                return parser
        except Exception:
            continue
    if _looks_like_pdf(filename, content):
        return PARSERS_BY_ID["generic_pdf"]
    return PARSERS_BY_ID["generic_csv"]


def get_parser(parser_id: str) -> BaseStatementParser | None:
    return PARSERS_BY_ID.get(parser_id)


__all__ = [
    "StandardTransaction",
    "BaseStatementParser",
    "CurveCsvParser",
    "BarclaysCsvParser",
    "LloydsCsvParser",
    "MonzoCsvParser",
    "GenericCsvParser",
    "GenericPdfParser",
    "PARSERS_BY_ID",
    "available_parsers",
    "detect_parser",
    "get_parser",
]
