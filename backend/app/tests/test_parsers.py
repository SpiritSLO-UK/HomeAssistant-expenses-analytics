"""Unit tests for CSV parsers and shared helpers (spec §32.1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.parsers import detect_parser, get_parser
from app.parsers.base import parse_amount, parse_date
from app.parsers.barclays_csv import BarclaysCsvParser
from app.parsers.curve_csv import CurveCsvParser
from app.parsers.generic_csv import GenericCsvParser
from app.parsers.lloyds_csv import LloydsCsvParser
from app.parsers.monzo_csv import MonzoCsvParser


# --- helpers ---

@pytest.mark.parametrize(
    "text,expected",
    [
        ("02/05/2026", date(2026, 5, 2)),
        ("2026-05-02", date(2026, 5, 2)),
        ("2 May 2026", date(2026, 5, 2)),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("-42.18", Decimal("-42.18")),
        ("1,234.56", Decimal("1234.56")),
        ("£38.99", Decimal("38.99")),
        ("(42.18)", Decimal("-42.18")),
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


# --- Curve ---

def test_curve_parser():
    content = (
        b"Date,Description,Amount,Currency,Card,Category\n"
        b"2026-05-02,TESCO STORES 3142,-42.18,GBP,Visa,Groceries\n"
        b"2026-05-15,SALARY ACME,2450.00,GBP,Visa,Income\n"
    )
    txns = CurveCsvParser().parse("curve.csv", content)
    assert len(txns) == 2
    assert txns[0].transaction_date == date(2026, 5, 2)
    assert txns[0].amount == Decimal("-42.18")
    assert txns[0].direction == "debit"
    assert txns[1].amount == Decimal("2450.00")
    assert txns[1].direction == "credit"


# --- Barclays ---

def test_barclays_parser():
    content = (
        b"Number,Date,Account,Amount,Subcategory,Memo\n"
        b"1,02/05/2026,20-11-22 12345678,-55.30,Groceries,SAINSBURYS\n"
    )
    txns = BarclaysCsvParser().parse("barclays.csv", content)
    assert txns[0].amount == Decimal("-55.30")
    assert txns[0].description_raw == "SAINSBURYS"
    assert txns[0].currency == "GBP"


# --- Lloyds (separate debit/credit columns) ---

def test_lloyds_parser_debit_and_credit():
    content = (
        b"Transaction Date,Transaction Type,Sort Code,Account Number,"
        b"Transaction Description,Debit Amount,Credit Amount,Balance\n"
        b"01/05/2026,DEB,30-99-88,87654321,WAITROSE,63.27,,1936.73\n"
        b"12/05/2026,FPI,30-99-88,87654321,SALARY,,2450.00,4286.63\n"
    )
    txns = LloydsCsvParser().parse("lloyds.csv", content)
    assert txns[0].amount == Decimal("-63.27")  # debit -> negative
    assert txns[0].direction == "debit"
    assert txns[1].amount == Decimal("2450.00")  # credit -> positive
    assert txns[1].direction == "credit"


# --- Monzo ---

def test_monzo_parser():
    content = (
        b"Transaction ID,Date,Time,Type,Name,Category,Amount,Currency,"
        b"Local amount,Local currency,Notes and #tags,Description\n"
        b"tx_1,02/05/2026,08:14,Card payment,Pret,Eating out,-4.20,GBP,-4.20,GBP,,PRET 234\n"
    )
    txns = MonzoCsvParser().parse("monzo.csv", content)
    assert txns[0].amount == Decimal("-4.20")
    assert txns[0].merchant_raw == "Pret"
    assert txns[0].external_id == "tx_1"


# --- Generic (heuristic Money Out / Money In) ---

def test_generic_parser_heuristic():
    content = (
        b"Date,Details,Money Out,Money In,Balance\n"
        b"2026-05-02,LOCAL GREENGROCER,18.40,,481.60\n"
        b"2026-05-08,REFUND,,15.00,474.50\n"
    )
    txns = GenericCsvParser().parse("unknown.csv", content)
    assert txns[0].amount == Decimal("-18.40")
    assert txns[1].amount == Decimal("15.00")
    assert txns[0].description_raw == "LOCAL GREENGROCER"


def test_generic_parser_explicit_mapping():
    content = b"when,what,value\n2026-05-02,COFFEE,-3.50\n"
    mapping = {"date": "when", "description": "what", "amount": "value"}
    txns = GenericCsvParser(mapping=mapping).parse("x.csv", content)
    assert txns[0].amount == Decimal("-3.50")
    assert txns[0].description_raw == "COFFEE"


# --- detection ---

def test_detection_by_headers():
    monzo = (
        b"Transaction ID,Date,Time,Type,Name,Category,Amount,Currency,"
        b"Local amount,Local currency,Notes and #tags,Description\n"
    )
    assert detect_parser("export.csv", monzo).parser_id == "monzo_csv"

    lloyds = (
        b"Transaction Date,Transaction Type,Sort Code,Account Number,"
        b"Transaction Description,Debit Amount,Credit Amount,Balance\n"
    )
    assert detect_parser("export.csv", lloyds).parser_id == "lloyds_csv"


def test_detection_falls_back_to_generic():
    weird = b"Date,Details,Money Out,Money In,Balance\n2026-05-02,X,1.00,,9\n"
    assert detect_parser("mystery.csv", weird).parser_id == "generic_csv"


def test_get_parser_unknown_returns_none():
    assert get_parser("nope") is None


# --- sample fixtures parse and match their parser (guards sample/parser drift) ---

@pytest.mark.parametrize(
    "filename,parser_id",
    [
        ("curve-sample.csv", "curve_csv"),
        ("barclays-sample.csv", "barclays_csv"),
        ("lloyds-sample.csv", "lloyds_csv"),
        ("monzo-sample.csv", "monzo_csv"),
        ("generic-sample.csv", "generic_csv"),
    ],
)
def test_sample_files(samples_dir, filename, parser_id):
    content = (samples_dir / filename).read_bytes()
    assert detect_parser(filename, content).parser_id == parser_id
    txns = get_parser(parser_id).parse(filename, content)
    assert len(txns) >= 5
    assert all(t.description_raw for t in txns)
