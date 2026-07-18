"""Regression tests for US-formatted statement data (spec §14.4).

US month-first dates once hard-failed (``unrecognised date: '6/28/2026'``);
fixes landed across #219/#348/#367/#390 (``detect_month_first`` /
``_MONTH_FIRST_FORMATS`` / ``parse_date`` in ``app.parsers.base`` plus the
generic CSV parser + an ``ImportProfile.date_format`` override). These lock in
that US dates and US money formats parse correctly end-to-end through the
generic CSV parser and the import/transactions HTTP flow.

Money is compared with :class:`~decimal.Decimal` throughout (never float ``==``).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from app.parsers.base import parse_amount, parse_date
from app.parsers.generic_csv import GenericCsvParser

# --- US month-first date parsing (unit) ---


@pytest.mark.parametrize(
    "text,expected",
    [
        # Day component > 12 is unambiguously month-first; the day-first default
        # fails and the US fallback resolves it (regression for #219).
        ("6/28/2026", date(2026, 6, 28)),
        ("12/31/2025", date(2025, 12, 31)),
        ("06/28/2026", date(2026, 6, 28)),
    ],
)
def test_parse_date_us_unambiguous(text, expected):
    assert parse_date(text) == expected


def test_parse_date_ambiguous_defaults_day_first():
    """``03/04/2026`` is ambiguous. With no hint the app's day-first default wins
    (3 April); told the source is month-first it resolves US-style (March 4)."""
    assert parse_date("03/04/2026") == date(2026, 4, 3)
    assert parse_date("03/04/2026", month_first=True) == date(2026, 3, 4)
    # Dotted + 2-digit-year US variants also parse when month-first.
    assert parse_date("12.31.2025", month_first=True) == date(2025, 12, 31)
    assert parse_date("3/4/26", month_first=True) == date(2026, 3, 4)


# --- US money format parsing (unit) ---


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$1,234.56", Decimal("1234.56")),        # currency symbol + thousands sep
        ("$5,000.00", Decimal("5000.00")),        # thousands separator
        ("$1,234,567.89", Decimal("1234567.89")),  # multiple thousands separators
        ("-$42.50", Decimal("-42.50")),           # leading-sign debit
        ("($29.01)", Decimal("-29.01")),          # accounting parentheses debit
        ("$0.99", Decimal("0.99")),
    ],
)
def test_parse_amount_us_money(text, expected):
    assert parse_amount(text) == expected


# --- Generic parser: whole US statement (unit) ---


def test_generic_parser_us_statement_auto_detects_month_first():
    """A US statement with a row whose day > 12 (6/28) auto-detects month-first for
    the whole file, so even the ambiguous 3/4 row reads US-style (March 4). US money
    formats ($, thousands separators, accounting parentheses) parse alongside."""
    content = (
        b"Date,Description,Amount\n"
        b"3/4/2026,COFFEE HOUSE,($4.25)\n"        # ambiguous on its own
        b"6/28/2026,WHOLE FOODS,-$129.01\n"       # day=28 > 12 -> proves month-first
        b"12/31/2025,YEAR END BONUS,\"$1,234.56\"\n"
    )
    txns = GenericCsvParser().parse("us-bank.csv", content)
    assert [t.transaction_date for t in txns] == [
        date(2026, 3, 4),   # March 4, NOT April 3 -- month-first applied file-wide
        date(2026, 6, 28),  # June 28
        date(2025, 12, 31),
    ]
    assert txns[0].amount == Decimal("-4.25")
    assert txns[0].direction == "debit"
    assert txns[1].amount == Decimal("-129.01")
    assert txns[2].amount == Decimal("1234.56")
    assert txns[2].direction == "credit"


def test_generic_parser_all_ambiguous_us_needs_override():
    """When every day component is <= 12 the auto-detector has no evidence and stays
    on the day-first default (documented limitation). An explicit ``month_first=True``
    override -- what ``ImportProfile.date_format='mdy'`` maps to -- fixes it."""
    content = (
        b"Date,Description,Amount\n"
        b"6/3/2026,GAS STATION,-$40.00\n"
        b"7/8/2026,PHARMACY,($12.34)\n"
    )
    # Default: day-first silently swaps month<->day (the reason the override exists).
    day_first = GenericCsvParser().parse("us-bank.csv", content)
    assert day_first[0].transaction_date == date(2026, 3, 6)   # misread as 6 March
    assert day_first[1].transaction_date == date(2026, 8, 7)   # misread as 7 Aug

    us = GenericCsvParser(month_first=True).parse("us-bank.csv", content)
    assert us[0].transaction_date == date(2026, 6, 3)   # June 3
    assert us[1].transaction_date == date(2026, 7, 8)   # July 8
    assert us[0].amount == Decimal("-40.00")
    assert us[1].amount == Decimal("-12.34")


# --- End-to-end: US CSV through the import + transactions HTTP flow ---


def _amounts_by_desc(items: list[dict]) -> dict[str, Decimal]:
    return {row["description_raw"]: Decimal(row["amount"]) for row in items}


def test_us_statement_imports_end_to_end(client):
    """Upload -> confirm -> list: a US statement (month-first dates auto-detected via
    a day>12 row, US money formats) lands transactions with the right dates/amounts."""
    csv = (
        b"Date,Description,Amount\n"
        b"3/4/2026,COFFEE HOUSE,($4.25)\n"
        b"6/28/2026,WHOLE FOODS,-$129.01\n"
        b"12/31/2025,YEAR END BONUS,\"$1,234.56\"\n"
    )
    mapping = json.dumps({"date": "Date", "amount": "Amount", "description": "Description"})

    up = client.post(
        "/api/imports/upload",
        files={"file": ("us-bank.csv", csv, "text/csv")},
        data={"parser_id": "generic_csv", "mapping": mapping},
    )
    assert up.status_code == 200, up.text
    body = up.json()
    # Auto-detected month-first from the 6/28 row applies to the ambiguous 3/4 row.
    assert body["preview"][0]["transaction_date"] == "2026-03-04"
    assert client.post(f"/api/imports/{body['import_id']}/confirm").status_code == 200

    items = client.get("/api/transactions").json()["items"]
    dates = {row["description_raw"]: row["transaction_date"] for row in items}
    assert dates == {
        "COFFEE HOUSE": "2026-03-04",
        "WHOLE FOODS": "2026-06-28",
        "YEAR END BONUS": "2025-12-31",
    }
    amounts = _amounts_by_desc(items)
    assert amounts["COFFEE HOUSE"] == Decimal("-4.25")
    assert amounts["WHOLE FOODS"] == Decimal("-129.01")
    assert amounts["YEAR END BONUS"] == Decimal("1234.56")


def test_us_all_ambiguous_statement_imports_with_mdy_override(client):
    """An all-ambiguous US file (every day <= 12) needs ``date_format='mdy'`` to import
    month-first; without it the day-first default silently misreads the dates."""
    csv = b"Date,Amount,Description\n6/3/2026,-$40.00,GAS STATION\n7/8/2026,($12.34),PHARMACY\n"
    files = {"file": ("us.csv", csv, "text/csv")}
    mapping = json.dumps({"date": "Date", "amount": "Amount", "description": "Description"})

    auto = client.post(
        "/api/imports/upload", files=files,
        data={"parser_id": "generic_csv", "mapping": mapping},
    ).json()
    assert auto["preview"][0]["transaction_date"] == "2026-03-06"  # misread day-first

    up = client.post(
        "/api/imports/upload", files=files,
        data={"parser_id": "generic_csv", "mapping": mapping, "date_format": "mdy"},
    ).json()
    assert client.post(f"/api/imports/{up['import_id']}/confirm").status_code == 200

    items = client.get("/api/transactions").json()["items"]
    dates = {row["description_raw"]: row["transaction_date"] for row in items}
    assert dates == {"GAS STATION": "2026-06-03", "PHARMACY": "2026-07-08"}
    amounts = _amounts_by_desc(items)
    assert amounts["GAS STATION"] == Decimal("-40.00")
    assert amounts["PHARMACY"] == Decimal("-12.34")
