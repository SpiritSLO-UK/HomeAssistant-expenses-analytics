"""Parser DoS caps (backlog CR-SEC-9).

Bound the work a malicious file can force: CSV row count and PDF page count
(the extracted-text length is additionally clamped). The upload byte cap
(CR-SEC-8) bounds file size; these bound what the parser then does with it.
"""

from __future__ import annotations

import io

import pytest

from app.parsers import base


def test_csv_row_cap_rejects_oversized(monkeypatch):
    monkeypatch.setattr(base, "MAX_CSV_ROWS", 2)
    csv_bytes = b"date,amount\n2026-01-01,1\n2026-01-02,2\n2026-01-03,3\n"
    with pytest.raises(base.ParseError, match="too many rows"):
        base.read_csv_rows(csv_bytes)


def test_csv_under_cap_is_fine(monkeypatch):
    monkeypatch.setattr(base, "MAX_CSV_ROWS", 10)
    headers, rows = base.read_csv_rows(b"date,amount\n2026-01-01,1\n2026-01-02,2\n")
    assert len(rows) == 2


def test_pdf_page_cap_rejects_oversized(monkeypatch):
    pypdf = pytest.importorskip("pypdf")
    from app.parsers import generic_pdf

    monkeypatch.setattr(generic_pdf, "_MAX_PDF_PAGES", 1)
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    data = buf.getvalue()
    with pytest.raises(base.ParseError, match="too many pages"):
        generic_pdf.GenericPdfParser._extract_text(data)
