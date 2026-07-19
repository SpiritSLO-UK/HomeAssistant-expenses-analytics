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
    parser = ImageStatementParser()
    with pytest.raises(ParseError, match="OCR"):
        parser.parse("statement.png", b"fake-bytes")


def test_image_parser_no_rows(monkeypatch):
    monkeypatch.setattr(ocr_service, "extract_text", lambda path: ("just a blurry header", 0.3))
    parser = ImageStatementParser()
    with pytest.raises(ParseError, match="No transactions recognised"):
        parser.parse("statement.png", b"fake-bytes")


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


# A substantial digital text layer: a money amount plus a real body of words.
_DIGITAL_TEXT = (
    "ACME LTD INVOICE\nDate 01/05/2026\nCoffee beans 500g 12.99\n"
    "Delivery 3.50\nSubtotal 16.49\nVAT 3.30\nTOTAL 42.18\n"
)


def test_pdf_text_trusts_embedded_only_with_money(monkeypatch, tmp_path):
    """A tiny embedded text layer with no money amount (an image-only PDF) must fall
    through to OCR, not return a high-confidence empty parse."""
    import pypdf

    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(ocr_service, "ocr_pdf_pages", lambda *a, **k: "01/05/2026 CAFE 3.50")

    # A substantial embedded text layer WITH a money amount → trusted at 0.95 (OCR not used).
    monkeypatch.setattr(pypdf, "PdfReader", lambda _p: _FakeReader(_FakePage(_DIGITAL_TEXT)))
    text, conf = ocr_service._pdf_text(pdf)
    assert conf == pytest.approx(0.95) and "42.18" in text

    # A no-money text scrap → falls through to rasterise + OCR (0.5).
    monkeypatch.setattr(pypdf, "PdfReader", lambda _p: _FakeReader(_FakePage("Page 1 of 3")))
    text, conf = ocr_service._pdf_text(pdf)
    assert conf == pytest.approx(0.5) and "CAFE" in text


def test_pdf_text_rejects_lone_money_watermark(monkeypatch, tmp_path):
    """A stray money-looking scrap (a watermark / short header) is NOT enough to trust
    the embedded text layer — it must fall through to the OCR fallback (SR-D6)."""
    import pypdf

    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(ocr_service, "ocr_pdf_pages", lambda *a, **k: "01/05/2026 CAFE 3.50")
    # Money amount present but only a few words → below the substance threshold.
    monkeypatch.setattr(pypdf, "PdfReader", lambda _p: _FakeReader(_FakePage("SALE £9.99")))
    text, conf = ocr_service._pdf_text(pdf)
    assert conf == pytest.approx(0.5) and "CAFE" in text


def test_looks_like_digital_text_threshold():
    assert ocr_service._looks_like_digital_text(_DIGITAL_TEXT)
    assert not ocr_service._looks_like_digital_text("SALE 9.99")  # too few words
    assert not ocr_service._looks_like_digital_text("Page 1 of 3 header footer x y z")  # no money


def test_money_regex_survives_pathological_text():
    """A long separator-free digit run must not blow up the money probe (ReDoS
    finding #3): the bounded pattern + scan cap return 'no amount' fast. The old
    ``\\d[\\d,]*`` shape backtracked O(n^2) on such input."""
    import time

    blob = "9" * 300_000  # no valid ".dd" -> no match, must stay quick
    start = time.perf_counter()
    assert ocr_service._MONEY_RE.search(blob) is None
    assert ocr_service._looks_like_digital_text(blob) is False
    assert time.perf_counter() - start < 5.0  # generous ceiling; the fix runs in ms
    assert ocr_service._MAX_MONEY_SCAN_CHARS <= 65_536


def test_money_regex_still_matches_real_amounts():
    """The hardened money probe still fires on the amounts a real receipt carries."""
    assert ocr_service._MONEY_RE.search("Order total 12,345.67")
    assert ocr_service._MONEY_RE.search("Amount 42.18")
    assert ocr_service._MONEY_RE.search("Big ungrouped 12345.67")  # bounded \\d{1,15}
    assert not ocr_service._MONEY_RE.search("Dated 01.05.2026")  # a date, not money


# --- decompression-bomb guard (no engine needed) -------------------------

def test_ocr_image_refuses_decompression_bomb(monkeypatch):
    """A maliciously huge image degrades to ('', None) rather than crashing (SR-D6)."""
    from PIL import Image

    class _BombImage:
        def load(self):
            raise Image.DecompressionBombError("too many pixels")

    fake_pil = type("F", (), {
        "MAX_IMAGE_PIXELS": None,
        "DecompressionBombError": Image.DecompressionBombError,
        "open": staticmethod(lambda _p: _BombImage()),
    })
    fake_tess = type("T", (), {})

    monkeypatch.setitem(__import__("sys").modules, "PIL", type("P", (), {"Image": fake_pil}))
    monkeypatch.setitem(__import__("sys").modules, "PIL.Image", fake_pil)
    monkeypatch.setitem(__import__("sys").modules, "pytesseract", fake_tess)

    text, conf = ocr_service._ocr_image(Path("huge.png"))
    assert text == "" and conf is None
    # The guard must have bounded (never disabled) Pillow's pixel cap.
    assert fake_pil.MAX_IMAGE_PIXELS == ocr_service._MAX_IMAGE_PIXELS


def test_image_pixel_cap_is_bounded_and_enabled():
    """The configured cap is a real positive bound, not None/disabled (SR-D6)."""
    assert isinstance(ocr_service._MAX_IMAGE_PIXELS, int)
    assert ocr_service._MAX_IMAGE_PIXELS > 0
