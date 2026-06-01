"""PDF statement import tests (spec §11 — Stage 11).

The text→rows logic is tested directly (no real PDF needed); PDF byte
extraction (pypdf) is the optional add-on layer and degrades gracefully.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.parsers import detect_parser
from app.parsers.base import StandardTransaction
from app.parsers.generic_pdf import GenericPdfParser, parse_statement_text
from app.services import import_service

STATEMENT = """Your statement
Date        Description                 Amount      Balance
01/05/2026  TESCO STORES 3142           42.18       1,234.56
03/05/2026  ACME PAYROLL                2,000.00 CR 3,234.56
05/05/2026  AMAZON UK                   12.99
Page 1 of 1
"""


# --- pure text parser (review-heavy) ---

def test_parse_statement_text():
    txns = parse_statement_text(STATEMENT)
    assert len(txns) == 3
    assert all(t.needs_review for t in txns)  # PDF rows always flagged

    tesco, payroll, amazon = txns
    assert tesco.transaction_date == date(2026, 5, 1)
    assert tesco.amount == Decimal("-42.18")        # debit; trailing balance ignored
    assert "TESCO STORES 3142" in tesco.description_raw
    assert payroll.amount == Decimal("2000.00")     # CR -> credit (positive)
    assert payroll.direction == "credit"
    assert amazon.amount == Decimal("-12.99")


def test_parse_ignores_non_transaction_lines():
    assert parse_statement_text("Your statement\nPage 1 of 1\nBalance brought forward") == []


# --- detection ---

def test_detects_pdf_by_magic_and_extension():
    p = GenericPdfParser()
    assert p.can_parse("x.pdf", b"not-really")
    assert p.can_parse("x", b"%PDF-1.7\n...")
    assert not p.can_parse("x.csv", b"Date,Amount\n2026-05-01,-1.00")


def test_detect_parser_routes_pdf():
    assert detect_parser("statement.pdf", b"%PDF-1.4 data").parser_id == "generic_pdf"
    assert detect_parser("x.csv", b"Date,Description,Amount\n01/05/2026,x,-1.00").parser_id != "generic_pdf"


# --- import wiring ---

def test_needs_review_propagates_to_transaction():
    txn = StandardTransaction(
        transaction_date=date(2026, 5, 1), amount=Decimal("-42.18"),
        currency="GBP", description_raw="TESCO", needs_review=True,
    )
    row = import_service._to_transaction(txn, 1, 1, 1, "h")
    assert row.needs_review is True
    assert row.review_reason == "pdf_unverified"


def test_pdf_parser_listed_in_api(client):
    ids = [p["parser_id"] for p in client.get("/api/imports/parsers").json()]
    assert "generic_pdf" in ids


def test_pdf_upload_degrades_gracefully(client):
    # Not a valid PDF (and pypdf may be absent on this platform) -> a clean 400.
    r = client.post(
        "/api/imports/upload",
        files={"file": ("statement.pdf", b"%PDF-1.4\nnot a real pdf body", "application/pdf")},
    )
    assert r.status_code == 400
