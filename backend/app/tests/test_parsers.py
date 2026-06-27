"""Unit tests for CSV parsers and shared helpers (spec §32.1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.parsers import detect_parser, get_parser
from app.parsers.barclaycard_csv import BarclaycardCsvParser
from app.parsers.barclays_csv import BarclaysCsvParser
from app.parsers.base import detect_month_first, parse_amount, parse_date
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


def test_curve_app_export_parser():
    """The real Curve app statement export (different headers, positive=spend)."""
    content = (
        b"Export Format,Date (YYYY-MM-DD as UTC),Time (HH:MM:SS as UTC),Merchant,"
        b"Txn Amount (Funding Card),Txn Currency (Funding Card),"
        b"Txn Amount (Foreign Spend),Txn Currency (Foreign Spend),Card Name,"
        b"Card Last 4 Digits,Type,Category,Notes,Fees\n"
        b"CSV,2025-07-20,21:14:46,Kwik Save,3.69,GBP,,,Credit Card,1006,Personal,Groceries,,\n"
        b"CSV,2025-07-22,10:00:00,Refund Store,-5.00,GBP,,,Credit Card,1006,Personal,Shopping,,\n"
    )
    txns = CurveCsvParser().parse("curve.csv", content)
    assert len(txns) == 2
    assert txns[0].transaction_date == date(2025, 7, 20)
    # Funding-card charge is positive for a spend -> negated to money out.
    assert txns[0].amount == Decimal("-3.69")
    assert txns[0].direction == "debit"
    assert txns[0].description_raw == "Kwik Save"
    assert txns[0].merchant_raw == "Kwik Save"
    assert txns[0].currency == "GBP"
    assert txns[0].category_hint == "Groceries"
    assert txns[0].card_hint == "Credit Card"
    # Funding-card label (Card Name + last 4) carried for cross-account dedup.
    assert txns[0].funding_source == "Credit Card ••1006"
    # Curve exports refunds/credits negative -> flips to money in.
    assert txns[1].amount == Decimal("5.00")
    assert txns[1].direction == "credit"


def test_curve_app_export_detected_by_content():
    """An app export with no 'curve' in the filename still routes to curve_csv."""
    content = (
        b"Export For,Date (YYYY-MM-DD as UTC),Time (HH:MM:SS as UTC),Merchant,"
        b"Txn Amount (Funding Card),Txn Currency (Funding Card),Card Name,Category\n"
        b"CSV,2025-07-20,21:14:46,Kwik Save,3.69,GBP,Credit Card,Groceries\n"
    )
    assert isinstance(detect_parser("statement-export.csv", content), CurveCsvParser)


def test_curve_cash_earned_is_income():
    """Earned Curve Cash (Merchant 'Curve Cash: …', CPT only) -> Cashback income."""
    content = (
        b"Export Format,Date (YYYY-MM-DD as UTC),Time,Merchant,"
        b"Txn Amount (Funding Card),Txn Currency (Funding Card),"
        b"Txn Amount (Foreign Spend),Txn Currency (Foreign Spend),Card Name,"
        b"Card Last 4 Digits,Type,Category\n"
        b"CSV,2025-07-24,12:23:09,Curve Cash: Lidl,50,CPT,,,Curve Cash,,,\n"
    )
    txn = CurveCsvParser().parse("curve.csv", content)[0]
    assert txn.amount == Decimal("0.50")  # 50 CPT = £0.50, money in
    assert txn.direction == "credit"
    assert txn.currency == "GBP"
    assert txn.is_income is True
    assert txn.category_library_id == "income.cashback"
    assert txn.merchant_raw == "Lidl"
    assert txn.funding_source is None  # rewards wallet, not an underlying card


def test_curve_cash_redeemed_is_spend():
    """Redeemed Curve Cash (real merchant, GBP Foreign Spend) -> a normal spend."""
    content = (
        b"Export Format,Date (YYYY-MM-DD as UTC),Time,Merchant,"
        b"Txn Amount (Funding Card),Txn Currency (Funding Card),"
        b"Txn Amount (Foreign Spend),Txn Currency (Foreign Spend),Card Name,"
        b"Card Last 4 Digits,Type,Category\n"
        b"CSV,2025-08-03,11:44:54,Bexley Ringo Ecom,180,CPT,1.8,GBP,Curve Cash,,Personal,Travel\n"
    )
    txn = CurveCsvParser().parse("curve.csv", content)[0]
    assert txn.amount == Decimal("-1.80")  # paid £1.80 from the wallet (money out)
    assert txn.direction == "debit"
    assert txn.currency == "GBP"
    assert txn.is_income is False
    assert txn.category_hint == "Travel"
    assert txn.funding_source is None


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


# --- Barclaycard (tab-separated, no header, debit/credit split) ---

def test_barclaycard_parser():
    content = (
        b"05-Jun-26\t Payment, Thank You \tn/a\tMR A\t\t-1,751.92\t\n"
        b"03-Jun-26\t Crv*Lidl GB, London \tVisa\tMR A\tGroceries\t\t7.56\n"
        b"02-Jun-26\t Crv*TfL Travel Charge, London \tVisa\tMR A\tTravel\t\t0.10\n"
    )
    txns = BarclaycardCsvParser().parse("statement.csv", content)
    assert len(txns) == 3
    # Bill payment (Credit column, signed negative) -> money in.
    assert txns[0].transaction_date == date(2026, 6, 5)
    assert txns[0].amount == Decimal("1751.92")
    assert txns[0].direction == "credit"
    # Purchases (Debit column, positive) -> money out; "Crv*" kept for dedup.
    assert txns[1].amount == Decimal("-7.56")
    assert txns[1].direction == "debit"
    assert txns[1].description_raw.startswith("Crv*Lidl")
    assert txns[1].category_hint == "Groceries"
    assert txns[1].currency == "GBP"
    assert txns[2].amount == Decimal("-0.10")


def test_barclaycard_comma_delimited():
    """The saved .csv is comma-separated with the comma-bearing fields quoted
    (a spreadsheet copy-paste is tab-separated) — both must parse."""
    content = (
        b'05-Jun-26,"Payment, Thank You",n/a,MR A,,"-1,751.92",\n'
        b'03-Jun-26,"Crv*Lidl GB, London",Visa,MR A,Groceries,,7.56\n'
    )
    txns = BarclaycardCsvParser().parse("barclaycard.csv", content)
    assert len(txns) == 2
    assert txns[0].amount == Decimal("1751.92")  # bill payment -> money in
    assert txns[0].direction == "credit"
    assert txns[1].amount == Decimal("-7.56")  # purchase -> money out
    assert txns[1].description_raw == "Crv*Lidl GB, London"
    assert txns[1].category_hint == "Groceries"


def test_barclaycard_detected_by_content():
    """Headerless tab-separated + a DD-Mon-YY first column routes to barclaycard."""
    content = b"03-Jun-26\t Crv*Lidl GB, London \tVisa\tMR A\tGroceries\t\t7.56\n"
    assert isinstance(detect_parser("export.csv", content), BarclaycardCsvParser)


def test_barclaycard_detected_by_content_comma():
    """A comma-separated headerless DD-Mon-YY file also routes to barclaycard."""
    content = b'03-Jun-26,"Crv*Lidl GB, London",Visa,MR A,Groceries,,7.56\n'
    assert isinstance(detect_parser("export.csv", content), BarclaycardCsvParser)


def test_barclaycard_space_date():
    """The saved file dates the rows "05 Jun 26" (spaces, 2-digit year)."""
    content = b'05 Jun 26,"Payment, Thank You",n/a,MR A,,"-150.00",\n02 Jun 26,Crv*TfL,Visa,MR A,Travel,,0.10\n'
    txns = BarclaycardCsvParser().parse("barclaycard.csv", content)
    assert txns[0].transaction_date == date(2026, 6, 5)
    assert txns[0].amount == Decimal("150.00")
    assert txns[1].transaction_date == date(2026, 6, 2)
    assert txns[1].amount == Decimal("-0.10")
    assert isinstance(detect_parser("export.csv", content), BarclaycardCsvParser)


def test_barclaycard_does_not_claim_curve_csv():
    """A comma CSV (Curve) must not be mis-detected as Barclaycard."""
    content = b"Date,Description,Amount,Currency,Card\n2026-05-02,TESCO,-1.00,GBP,Visa\n"
    assert not BarclaycardCsvParser().can_parse("x.csv", content)


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


# --- US (month-first) date support ---

def test_parse_date_month_first_and_fallback():
    assert parse_date("15/03/2024") == date(2024, 3, 15)   # UK day-first (default)
    assert parse_date("09/15/2024") == date(2024, 9, 15)   # US, day-first fails → fallback
    assert parse_date("03/04/2024") == date(2024, 4, 3)    # ambiguous → day-first default
    # Told the file is month-first, ambiguous dates resolve US-style; ISO is unaffected.
    assert parse_date("03/04/2024", month_first=True) == date(2024, 3, 4)
    assert parse_date("12.31.2024", month_first=True) == date(2024, 12, 31)
    assert parse_date("2024-09-15", month_first=True) == date(2024, 9, 15)


def test_detect_month_first():
    assert detect_month_first(["09/15/2024", "10/03/2024"]) is True   # 2nd > 12 ⇒ month-first
    assert detect_month_first(["15/09/2024", "03/10/2024"]) is False  # 1st > 12 ⇒ day-first
    assert detect_month_first(["03/04/2024", "05/06/2024"]) is False  # all ambiguous ⇒ default
    assert detect_month_first(["15/09/2024", "09/15/2024"]) is False  # contradictory ⇒ safe default
    assert detect_month_first(["2024-09-15", "5 Jun 2024"]) is False  # unambiguous don't sway it


def test_generic_parser_detects_us_month_first_statement():
    """A whole US-format statement imports with the right dates — the file's order is
    inferred from a row whose day component is > 12 (03/15 ⇒ MM/DD)."""
    content = (
        b"Date,Description,Amount\n"
        b"03/04/2024,COFFEE,-3.50\n"      # ambiguous on its own
        b"03/15/2024,GROCERIES,-42.18\n"  # day=15 > 12 ⇒ proves month-first
    )
    txns = GenericCsvParser().parse("us-bank.csv", content)
    assert txns[0].transaction_date == date(2024, 3, 4)   # March 4, NOT April 3
    assert txns[1].transaction_date == date(2024, 3, 15)


def test_generic_parser_keeps_uk_day_first_statement():
    content = (
        b"Date,Description,Amount\n"
        b"04/03/2024,COFFEE,-3.50\n"
        b"15/03/2024,GROCERIES,-42.18\n"  # day=15 ⇒ day-first
    )
    txns = GenericCsvParser().parse("uk-bank.csv", content)
    assert txns[0].transaction_date == date(2024, 3, 4)   # 4 March
    assert txns[1].transaction_date == date(2024, 3, 15)


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
        ("curve-app-export-sample.csv", "curve_csv"),
        ("barclays-sample.csv", "barclays_csv"),
        ("barclaycard-sample.csv", "barclaycard_csv"),
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
