"""Scanned / photographed statement import (backlog: import a photo/scan).

Like the PDF tests, the OCR engine layer is mocked so these run with or without
Tesseract/pypdfium2 — the text→rows step is the already-tested ``parse_statement_text``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.parsers import detect_parser
from app.parsers.base import ParseError
from app.parsers.generic_pdf import GenericPdfParser
from app.parsers.image_statement import ImageStatementParser
from app.services import ocr_service

STATEMENT = """01/05/2026  TESCO STORES 3142           42.18       1,234.56
03/05/2026  ACME PAYROLL                2,000.00 CR 3,234.56
05/05/2026  AMAZON UK                   12.99
"""

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # just enough to look like a PNG


# --- image statement parser (OCR mocked) --------------------------------

def test_image_parser_ocrs_then_parses(monkeypatch):
    monkeypatch.setattr(ocr_service, "extract_text", lambda path: (STATEMENT, 0.8))
    rows = ImageStatementParser().parse("statement.jpg", b"fake-bytes")
    assert len(rows) == 3
    assert all(r.needs_review for r in rows)  # OCR rows are always review-flagged
    assert rows[0].transaction_date == date(2026, 5, 1)
    assert rows[0].amount == Decimal("-42.18")
    assert rows[1].amount == Decimal("2000.00")  # CR → credit


def test_image_parser_clear_error_without_engine(monkeypatch):
    def _raise(_path):
        raise ocr_service.OcrUnavailable("no tesseract")

    monkeypatch.setattr(ocr_service, "extract_text", _raise)
    with pytest.raises(ParseError, match="OCR"):
        ImageStatementParser().parse("statement.png", b"fake-bytes")


def test_image_parser_no_rows(monkeypatch):
    monkeypatch.setattr(ocr_service, "extract_text", lambda path: ("just a blurry header", 0.3))
    with pytest.raises(ParseError, match="No transactions recognised"):
        ImageStatementParser().parse("statement.png", b"fake-bytes")


# --- detection ------------------------------------------------------------

def test_image_parser_can_parse_by_suffix_and_magic():
    p = ImageStatementParser()
    assert p.can_parse("scan.JPG", b"whatever")
    assert p.can_parse("noext", PNG_BYTES)
    assert not p.can_parse("x.csv", b"Date,Amount\n2026-05-01,-1.00")


def test_detect_parser_routes_images():
    assert detect_parser("statement.png", PNG_BYTES).parser_id == "image_statement"
    assert detect_parser("photo.jpeg", b"\xff\xd8\xff\xe0").parser_id == "image_statement"
    # CSV and PDF still route to their own parsers.
    assert detect_parser("x.csv", b"Date,Description,Amount\n01/05/2026,x,-1.00").parser_id != "image_statement"
    assert detect_parser("s.pdf", b"%PDF-1.4 data").parser_id == "generic_pdf"


def test_image_parser_listed_in_api(client):
    ids = [p["parser_id"] for p in client.get("/api/imports/parsers").json()]
    assert "image_statement" in ids


# --- scanned-PDF OCR fallback (engine mocked) ----------------------------

def test_generic_pdf_ocr_fallback_delegates(monkeypatch):
    monkeypatch.setattr(ocr_service, "ocr_pdf_pages", lambda p, **k: "01/03/2026 CAFE 3.50")
    assert GenericPdfParser._ocr_scanned(b"%PDF-fake").strip() == "01/03/2026 CAFE 3.50"


def test_ocr_pdf_pages_empty_without_rasteriser(monkeypatch):
    monkeypatch.setattr(ocr_service, "_pdfium_ok", lambda: False)
    assert ocr_service.ocr_pdf_pages(Path("nope.pdf")) == ""


def test_status_reports_pdf_ocr_capability():
    assert "pdf_ocr" in ocr_service.status()


# --- embedded-text trust requires a money amount (don't block the OCR fallback) ----

class _FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakeReader:
    def __init__(self, *pages: _FakePage) -> None:
        self.pages = list(pages)


def test_money_signal_distinguishes_amounts_from_scraps():
    assert ocr_service._MONEY_RE.search("TOTAL 42.18")
    assert ocr_service._MONEY_RE.search("Balance 1,234.56")
    assert not ocr_service._MONEY_RE.search("Page 1 of 3")
    assert not ocr_service._MONEY_RE.search("Dated 01.05.2026")  # a dotted date, not money


def test_pdf_text_trusts_embedded_only_with_money(monkeypatch, tmp_path):
    """A tiny embedded text layer with no money amount (an image-only PDF) must fall
    through to OCR, not return a high-confidence empty parse."""
    import pypdf

    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(ocr_service, "ocr_pdf_pages", lambda *a, **k: "01/05/2026 CAFE 3.50")

    # Embedded text WITH a money amount → trusted at 0.95 (OCR not used).
    monkeypatch.setattr(pypdf, "PdfReader", lambda _p: _FakeReader(_FakePage("ACME LTD\nTOTAL 42.18\n")))
    text, conf = ocr_service._pdf_text(pdf)
    assert conf == 0.95 and "42.18" in text

    # A no-money text scrap → falls through to rasterise + OCR (0.5).
    monkeypatch.setattr(pypdf, "PdfReader", lambda _p: _FakeReader(_FakePage("Page 1 of 3")))
    text, conf = ocr_service._pdf_text(pdf)
    assert conf == 0.5 and "CAFE" in text
