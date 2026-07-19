"""Receipt tests (spec §21 — Stage 8).

OCR engine isn't required: the parser is tested directly on text, and the
upload→manual-fields→match→confirm flow doesn't depend on a Tesseract binary.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Account, Category, Household, Receipt, ReviewItem, Transaction
from app.services import receipt_parser, receipt_service, review_service, settings_service
from app.services.household_service import get_or_create_default_household

SAMPLE = """TESCO STORES
123 High Street
05/05/2026
Milk 1.20
Bread 0.90
Subtotal £38.00
VAT £4.18
TOTAL £42.18
Thank you for shopping
"""


# --- pure parser (no engine) ---

def test_parser_extracts_level1_fields():
    f = receipt_parser.extract_fields(SAMPLE)
    assert f["merchant_raw"] == "TESCO STORES"
    assert f["receipt_date"].isoformat() == "2026-05-05"
    assert str(f["total_amount"]) == "42.18"   # TOTAL, not the £38.00 subtotal
    assert str(f["vat_amount"]) == "4.18"
    assert f["currency"] == "GBP"
    assert f["parse_confidence"] == pytest.approx(1.0)


def test_parser_total_falls_back_to_largest_amount():
    f = receipt_parser.extract_fields("CORNER SHOP\n2026-03-01\n3.50\n9.99\n2.00")
    assert str(f["total_amount"]) == "9.99"


def test_parser_handles_eu_and_short_decimal_totals():
    """EU comma-decimals and 1-decimal / grouped totals are no longer dropped."""
    assert receipt_parser.detect_total("TOTAL 12,50") == Decimal("12.50")  # EU comma decimal
    assert receipt_parser.detect_total("TOTAL 45.5") == Decimal("45.50")   # single decimal place
    assert receipt_parser.detect_total("TOTAL 1.234,56") == Decimal("1234.56")  # EU grouped
    assert receipt_parser.detect_total("TOTAL 1,234.56") == Decimal("1234.56")  # UK/US grouped
    assert receipt_parser.detect_total("TOTAL €1.299,00") == Decimal("1299.00")


def test_parser_handles_us_and_ambiguous_dates():
    """US month-first dates parse via fallback; day-first is preferred when both work."""
    assert receipt_parser.detect_date("Date: 09/15/2024").isoformat() == "2024-09-15"  # US, day-first invalid
    assert receipt_parser.detect_date("Date: 15/09/2024").isoformat() == "2024-09-15"  # day-first
    assert receipt_parser.detect_date("Date: 05/06/2024").isoformat() == "2024-06-05"  # ambiguous → day-first
    assert receipt_parser.detect_date("Date: 12.31.2024").isoformat() == "2024-12-31"  # US, dotted


def test_parser_whole_number_total():
    """A currency-anchored whole-number total (no decimals) parses instead of dropping."""
    assert receipt_parser.detect_total("TOTAL £42") == Decimal("42.00")
    assert receipt_parser.detect_total("AMOUNT DUE 100 EUR") == Decimal("100.00")
    # Symbol-prefixed and code-suffixed forms both parse (the two whole-amount patterns).
    assert receipt_parser.detect_total("GRAND TOTAL $1,234") == Decimal("1234.00")
    assert receipt_parser.detect_total("BALANCE DUE 1,234 GBP") == Decimal("1234.00")
    # A decimal total on a line still wins over a whole number on the same line.
    assert receipt_parser.detect_total("TOTAL £42.18") == Decimal("42.18")


def test_parser_skips_savings_and_points_lines():
    """Loyalty 'Total savings'/'points' lines must not override the real total."""
    text = (
        "TESCO\n"
        "Clubcard Points 250\n"
        "Total savings £5.00\n"
        "TOTAL £12.34\n"
    )
    assert receipt_parser.detect_total(text) == Decimal("12.34")
    # Even when the savings figure is larger, it doesn't win.
    text2 = "Total savings £99.99\nTOTAL £8.50"
    assert receipt_parser.detect_total(text2) == Decimal("8.50")
    # A lone savings line yields no total (rather than a bogus one).
    assert receipt_parser.detect_total("Total savings £5.00") is None


def test_parser_vat_is_the_smaller_tax_figure():
    """VAT on a 'Net … VAT …' line is the tax charged, not the larger net; % ignored."""
    assert receipt_parser.detect_vat("Net 38.00 VAT 4.18") == Decimal("4.18")
    assert receipt_parser.detect_vat("VAT 20% 4.18") == Decimal("4.18")
    assert receipt_parser.detect_vat("VAT £4.18") == Decimal("4.18")


def test_amount_regex_survives_pathological_line(monkeypatch):
    """A long separator-free digit run must not blow up the amount regexes (ReDoS
    finding #3): the bounded patterns + per-line cap yield no amount, fast. The old
    ``\\d+``/``\\d[\\d,]*`` shapes backtracked O(n^2) (measured ~76s at 160k chars)."""
    import time

    pathological = "1" * 200_000  # no valid decimal -> nothing to match, must stay quick
    start = time.perf_counter()
    assert receipt_parser._amounts_on(pathological) == []
    assert receipt_parser._whole_amounts_on(pathological) == []
    assert receipt_parser.detect_total(pathological) is None
    assert receipt_parser.detect_vat("VAT " + pathological) is None
    assert time.perf_counter() - start < 5.0  # generous ceiling; the fix runs in ms

    # The per-line cap really bounds the scanned text.
    assert receipt_parser._MAX_AMOUNT_LINE_CHARS <= 65_536


def test_amount_regex_still_parses_real_amounts():
    """The ReDoS-hardened patterns still match the amounts receipts legitimately carry."""
    assert receipt_parser._amounts_on("Coffee 12.00") == [Decimal("12.00")]
    assert receipt_parser._amounts_on("Total 1,234.56") == [Decimal("1234.56")]
    assert receipt_parser._amounts_on("EU 1.234,56") == [Decimal("1234.56")]
    assert receipt_parser._amounts_on("Short 45.5") == [Decimal("45.50")]
    # An ungrouped multi-digit integer part (no thousands separator) still parses.
    assert receipt_parser._amounts_on("Big 12345.67") == [Decimal("12345.67")]
    assert receipt_parser.detect_total("TOTAL £42") == Decimal("42.00")


# --- characterisation of the _AMOUNT regex (Sonar S5843 simplification guard) ---
# These pin the EXACT current behaviour of _AMOUNT / _amounts_on so the regex can be
# simplified for SonarCloud rule S5843 (regex complexity) without silently changing
# which amounts a receipt yields. Every row below is real ground-truth: _to_decimal
# picks the separator, so 12,50 -> 12.50 (comma decimal) while 12,345 -> 12345.00
# (comma grouping), and bare integers / rate-only tokens deliberately match nothing.

_AMOUNTS_ON_CASES = [
    # UK/US and EU grouped-with-decimal, either separator as the point.
    ("1,234.56", ["1234.56"]),
    ("1.234,56", ["1234.56"]),
    ("1,234,567.89", ["1234567.89"]),
    ("1.234.567,89", ["1234567.89"]),
    ("£1,234.56", ["1234.56"]),
    ("€1.299,00", ["1299.00"]),
    ("1,234.5", ["1234.50"]),
    # Grouped, no decimal tail.
    ("1,234", ["1234.00"]),
    ("1.234", ["1234.00"]),
    ("12,345", ["12345.00"]),
    ("123,456,789", ["123456789.00"]),
    # Plain decimals with either separator, incl. short (single-digit) decimals.
    ("12.34", ["12.34"]),
    ("12,50", ["12.50"]),
    ("45.5", ["45.50"]),
    ("12.3", ["12.30"]),
    ("1.5", ["1.50"]),
    ("0.99", ["0.99"]),
    ("$0.99", ["0.99"]),
    ("100.00", ["100.00"]),
    ("12345.67", ["12345.67"]),
    ("1,23", ["1.23"]),  # 1–2 digit run after the sep is the decimal, not grouping
    # Signs / parentheses / currency are stripped from the captured group(1).
    ("-5.00", ["5.00"]),
    ("-£5.00", ["5.00"]),
    ("(£5.00)", ["5.00"]),
    ("£12.34", ["12.34"]),
    # A space between the currency symbol and the digits is tolerated; the amount is
    # still captured (this is the one superset the simplified prefix relies on).
    ("£ 12.34", ["12.34"]),
    ("$ 5.00", ["5.00"]),
    # Embedded in text / multiple amounts on one line, left-to-right.
    ("Coffee 12.00 and tea 3.50", ["12.00", "3.50"]),
    ("Total: 1,234.56 GBP", ["1234.56"]),
    # Greedy/ordered-alternation quirks that MUST be preserved verbatim.
    ("1234,567", ["1234.56"]),  # comma read as decimal -> 1234.56, trailing 7 dropped
    ("1,2345", ["1234.00"]),    # only the grouped 1,234 matches; stray 5 is not decimal
    ("3.5.5", ["3.50"]),        # matches the first plain decimal 3.5 only
    # No-match cases: bare integers, currency-only, malformed, empty.
    ("1234", []),
    ("5", []),
    ("50", []),
    ("500", []),
    ("5000", []),
    ("£5", []),
    ("5 EUR", []),
    ("1..2", []),
    ("12.", []),
    (".50", []),
    ("abc", []),
    ("", []),
]


@pytest.mark.parametrize("text, expected", _AMOUNTS_ON_CASES)
def test_amounts_on_characterisation(text, expected):
    assert receipt_parser._amounts_on(text) == [Decimal(e) for e in expected]


@pytest.mark.parametrize(
    "text, expected_group",
    [
        ("12.34", "12.34"),
        ("£12.34", "12.34"),      # currency symbol is outside group 1
        ("£ 12.34", "12.34"),     # symbol + space still fully matches an amount line
        ("$ 5.00", "5.00"),
        ("1,234.56", "1,234.56"),
        ("abc", None),
        ("5", None),              # bare integer is not a money amount
        ("12", None),
        ("-5.00", None),          # the sign breaks a *full* match (finditer still finds 5.00)
    ],
)
def test_amount_regex_fullmatch_characterisation(text, expected_group):
    m = receipt_parser._AMOUNT_RE.fullmatch(text)
    assert (m.group(1) if m else None) == expected_group


def test_amount_regex_group_one_index_is_the_number():
    """Downstream reads group(1); the currency/space prefix must never be captured."""
    m = receipt_parser._AMOUNT_RE.search("Paid £ 1,234.56 today")
    assert m is not None
    assert m.group(1) == "1,234.56"


@pytest.mark.parametrize(
    "line, expected",
    [
        ("TOTAL £ 42.18", "42.18"),   # currency symbol + space before the amount
        ("TOTAL $ 5.00", "5.00"),
        ("TOTAL 12,50", "12.50"),
        ("TOTAL 45.5", "45.50"),
        ("TOTAL 1.234,56", "1234.56"),
        ("TOTAL 1,234.56", "1234.56"),
        ("TOTAL €1.299,00", "1299.00"),
    ],
)
def test_detect_total_characterisation(line, expected):
    assert receipt_parser.detect_total(line) == Decimal(expected)


def test_detect_vat_tolerates_symbol_space():
    assert receipt_parser.detect_vat("VAT £ 4.18") == Decimal("4.18")
    assert receipt_parser.detect_vat("Net 38.00 VAT 4.18") == Decimal("4.18")


# Real card-payment slip a user uploaded — OCR collapsed it to one long line of
# terminal/transaction boilerplate. The merchant heuristic must NOT dump that into
# the merchant field (backlog: receipt-OCR merchant gibberish, 2026-06-06).
_CARD_SLIP = (
    "REG No f Issue: SESSION: M CUSTOMER DEBIT CARD PAYMENT VEDI d Number: "
    "492181XXXXXX1031 h Code: 026242 Merchant ID; **#*265 Terminal ID; ****0647 "
    "Application ID: A0000000031010 PAN Seq No: 00 HTxn ID: SAT6A2713444 "
    "TRX ID: 486143548512099 Amount NO CARDHOLDER VERIFICATION PAYMENT APPROVED CARDHOLDER R"
)


def test_merchant_rejects_card_slip_one_line():
    # One giant run-on OCR line → too long to be a name → no merchant (→ review).
    assert receipt_parser.detect_merchant(_CARD_SLIP) is None


def test_merchant_skips_payment_boilerplate_lines():
    slip = "CARDHOLDER COPY\nDEBIT CARD PAYMENT\nMERCHANT ID: 12345\nTERMINAL ID: 0647\nPAYMENT APPROVED"
    assert receipt_parser.detect_merchant(slip) is None


def test_merchant_keeps_real_name_above_payment_lines():
    # A genuine shop header is still picked even when payment boilerplate follows.
    slip = "COSTA COFFEE\nDEBIT CARD PAYMENT\nTERMINAL ID: 0647\nTOTAL 4.50"
    assert receipt_parser.detect_merchant(slip) == "COSTA COFFEE"


def test_merchant_rejects_overlong_line():
    assert receipt_parser.detect_merchant("A" + " WORD" * 20) is None  # > 60 chars, not a name


# --- API: status + flow ---

def _curve(rows):
    head = "Date,Description,Amount,Currency,Card,Category\n"
    body = "".join(f"{d},{desc},{amt},GBP,Visa,\n" for d, desc, amt in rows)
    return (head + body).encode()


def _import(client, content, name="r.csv"):
    up = client.post("/api/imports/upload", files={"file": (name, content, "text/csv")},
                     data={"parser_id": "curve_csv"}).json()
    return client.post(f"/api/imports/{up['import_id']}/confirm").json()


def _txn(client, desc):
    return next(t for t in client.get("/api/transactions").json()["items"] if t["description_raw"] == desc)


def _upload(client, content=b"not-a-real-image", name="receipt.png"):
    return client.post("/api/receipts/upload", files={"file": (name, content, "image/png")})


def test_ocr_status_endpoint(client):
    s = client.get("/api/receipts/status").json()
    assert "available" in s and "image_ocr" in s and "pdf_text" in s


# --- reuse the receipt's AI-suggested category on its matched transaction (#110) ---


def _receipt_with_ai_category(client, *, ai_name: str) -> tuple[int, int, int]:
    """Import an uncategorisable txn + a receipt that already carries an AI-suggested
    category. Returns (transaction_id, receipt_id, ai_category_id)."""
    _import(client, _curve([("2026-05-02", "ZZQ MYSTERY PURCHASE", "-42.18")]))
    txn = _txn(client, "ZZQ MYSTERY PURCHASE")
    rid = _upload(client).json()["id"]
    client.patch(f"/api/receipts/{rid}", json={"merchant_raw": "ZZQ", "receipt_date": "2026-05-02", "total_amount": "42.18"})
    with SessionLocal() as db:
        cat = Category(name=ai_name, is_active=True)
        db.add(cat)
        db.flush()
        cat_id = cat.id
        db.get(Receipt, rid).ai_category_id = cat_id
        db.commit()
    return txn["id"], rid, cat_id


def test_receipt_ai_category_reused_on_confirm_match(client):
    txn_id, rid, cat_id = _receipt_with_ai_category(client, ai_name="ReuseCat")
    # The imported row is uncategorisable, so it has no category yet.
    with SessionLocal() as db:
        assert db.get(Transaction, txn_id).category_id is None
    r = client.post(f"/api/receipts/{rid}/confirm-match", json={"transaction_id": txn_id})
    assert r.status_code == 200
    # Reused the receipt's AI category — no separate AI classification call needed.
    with SessionLocal() as db:
        assert db.get(Transaction, txn_id).category_id == cat_id


def test_receipt_ai_category_does_not_override_existing(client):
    txn_id, rid, _ai_id = _receipt_with_ai_category(client, ai_name="AiCat")
    with SessionLocal() as db:
        manual = Category(name="ManualCat", is_active=True)
        db.add(manual)
        db.flush()
        manual_id = manual.id
        db.get(Transaction, txn_id).category_id = manual_id  # already categorised
        db.commit()
    client.post(f"/api/receipts/{rid}/confirm-match", json={"transaction_id": txn_id})
    with SessionLocal() as db:
        assert db.get(Transaction, txn_id).category_id == manual_id  # unchanged


def test_upload_then_manual_fields_then_match_and_confirm(client):
    _import(client, _curve([("2026-05-02", "TESCO STORES 3142", "-42.18")]))
    txn = _txn(client, "TESCO STORES 3142")

    up = _upload(client)
    assert up.status_code == 201, up.text
    rid = up.json()["id"]
    # No Tesseract binary in tests -> OCR can't run on the bytes; pipeline still works.
    assert up.json()["ocr_status"] in {"skipped", "failed", "processed"}

    # Enter fields manually (spec §21.3).
    patched = client.patch(f"/api/receipts/{rid}", json={
        "merchant_raw": "TESCO", "receipt_date": "2026-05-02", "total_amount": "42.18",
    }).json()
    assert patched["total_amount"] == "42.18"
    assert patched["needs_review"] is False  # manual total clears low-confidence

    # Match: exact amount (50) + same day (20) + vendor contained (20) = 90.
    res = client.post(f"/api/receipts/{rid}/match").json()
    assert res["best_score"] == 90
    assert res["status"] == "suggested"
    assert res["candidates"][0]["transaction_id"] == txn["id"]

    # Confirm it.
    confirmed = client.post(f"/api/receipts/{rid}/confirm-match",
                            json={"transaction_id": txn["id"]}).json()
    match = confirmed["matches"][0]
    assert match["match_status"] == "confirmed"
    assert match["matched_by"] == "user"


def test_match_requires_total(client):
    rid = _upload(client).json()["id"]
    # No total set yet -> matching is rejected.
    assert client.post(f"/api/receipts/{rid}/match").status_code == 400


# --- recommend a transaction for an unmatched receipt (user ask) ---

def test_recommendation_for_unmatched_receipt(client):
    """An unmatched receipt with a total recommends a pre-filled transaction; the
    existing create endpoint adds it in one click and the recommendation clears."""
    rid = _upload(client).json()["id"]
    client.patch(f"/api/receipts/{rid}", json={
        "merchant_raw": "Corner Shop", "receipt_date": "2026-05-05", "total_amount": "42.18",
    })
    # No transactions exist → unmatched.
    assert client.post(f"/api/receipts/{rid}/match").json()["status"] == "unmatched"
    rec = client.get(f"/api/receipts/{rid}").json()["recommended_transaction"]
    assert rec is not None
    assert rec["merchant"] == "Corner Shop"
    assert rec["transaction_date"] == "2026-05-05"
    assert rec["amount"] == "-42.18"  # purchase = money out
    assert rec["currency"] == "GBP"
    # One-click add (the recommendation) → receipt becomes matched → no more rec.
    res = client.post(f"/api/receipts/{rid}/create-transaction", json={"new_account": True})
    assert res.status_code == 200, res.text
    assert res.json()["receipt"]["recommended_transaction"] is None


def test_recommendation_absent_without_total(client):
    rid = _upload(client).json()["id"]
    assert client.get(f"/api/receipts/{rid}").json()["recommended_transaction"] is None


def test_recommendation_uses_ai_category(client):
    """The recommended transaction carries the receipt's AI-suggested category."""
    rid = _upload(client).json()["id"]
    client.patch(f"/api/receipts/{rid}", json={"merchant_raw": "ZZQ", "total_amount": "9.99"})
    with SessionLocal() as db:
        cat = Category(name="RecCat", is_active=True)
        db.add(cat)
        db.flush()
        cat_id = cat.id
        db.get(Receipt, rid).ai_category_id = cat_id
        db.commit()
    rec = client.get(f"/api/receipts/{rid}").json()["recommended_transaction"]
    assert rec["category_id"] == cat_id
    assert rec["category_name"] == "RecCat"


def test_recommendation_absent_once_matched(client):
    """A receipt with a suggested/confirmed match doesn't recommend a new txn."""
    _import(client, _curve([("2026-05-02", "TESCO STORES 3142", "-42.18")]))
    txn = _txn(client, "TESCO STORES 3142")
    rid = _upload(client).json()["id"]
    client.patch(f"/api/receipts/{rid}", json={
        "merchant_raw": "TESCO", "receipt_date": "2026-05-02", "total_amount": "42.18",
    })
    client.post(f"/api/receipts/{rid}/match")  # finds the imported txn → suggested
    client.post(f"/api/receipts/{rid}/confirm-match", json={"transaction_id": txn["id"]})
    assert client.get(f"/api/receipts/{rid}").json()["recommended_transaction"] is None


def test_upload_is_deduplicated(client):
    first = _upload(client, content=b"same-bytes").json()
    second = _upload(client, content=b"same-bytes").json()
    assert first["id"] == second["id"]
    assert len(client.get("/api/receipts").json()) == 1
    # The re-upload is flagged so the UI can say "already imported" (#21 dedupe UX).
    assert first["already_imported"] is False
    assert second["already_imported"] is True


def test_delete_receipt(client):
    rid = _upload(client).json()["id"]
    assert client.delete(f"/api/receipts/{rid}").status_code == 204
    assert client.get("/api/receipts").json() == []


# --- per-transaction attach + viewer (drill-down receipt) ---

def test_attach_receipt_to_transaction_keeps_original(client):
    _import(client, _curve([("2026-05-02", "ALDI STORE 412", "-37.85")]))
    txn = _txn(client, "ALDI STORE 412")

    res = client.post(
        f"/api/transactions/{txn['id']}/receipts",
        files={"file": ("aldi.png", b"fake-image-bytes", "image/png")},
    )
    assert res.status_code == 201
    receipt = res.json()
    # The original is kept (viewable) even though delete-after-processing defaults on.
    assert receipt["has_file"] is True
    assert any(
        m["transaction_id"] == txn["id"] and m["match_status"] == "confirmed" for m in receipt["matches"]
    )

    # Listed against the transaction...
    listed = client.get(f"/api/transactions/{txn['id']}/receipts").json()
    assert [r["id"] for r in listed] == [receipt["id"]]

    # ...and the original is served back for viewing — inline (so the in-app
    # popup / browser previews it) rather than as a forced download.
    served = client.get(f"/api/receipts/{receipt['id']}/file")
    assert served.status_code == 200
    assert served.content == b"fake-image-bytes"
    assert "inline" in served.headers.get("content-disposition", "")


def test_receipt_file_nosniff_and_forces_download_for_unsafe_types(client):
    """CR-SEC-14: a safe image previews inline but always with X-Content-Type-Options:
    nosniff; a script-capable type (e.g. SVG) is served as an opaque attachment so it
    can't execute in our origin."""
    rid = _upload(client, content=b"img-bytes", name="ok.png").json()["id"]
    served = client.get(f"/api/receipts/{rid}/file")
    assert served.headers["x-content-type-options"] == "nosniff"
    assert "inline" in served.headers.get("content-disposition", "")

    # Point the stored receipt at a script-capable filename → forced download.
    with SessionLocal() as db:
        receipt = db.get(Receipt, rid)
        receipt.source_filename = "evil.svg"
        db.commit()
    served = client.get(f"/api/receipts/{rid}/file")
    assert "attachment" in served.headers.get("content-disposition", "")
    assert served.headers["content-type"].startswith("application/octet-stream")
    assert served.headers["x-content-type-options"] == "nosniff"


def test_attach_receipt_validation(client):
    _import(client, _curve([("2026-05-02", "BP CONNECT", "-59.80")]))
    txn = _txn(client, "BP CONNECT")
    assert client.post(
        f"/api/transactions/{txn['id']}/receipts", files={"file": ("x.png", b"", "image/png")}
    ).status_code == 400
    assert client.post(
        "/api/transactions/999999/receipts", files={"file": ("x.png", b"data", "image/png")}
    ).status_code == 404


# --- create a transaction from an unmatched receipt (cash / un-imported) ---

def _by_id(client, txn_id):
    return next(t for t in client.get("/api/transactions").json()["items"] if t["id"] == txn_id)


def test_create_transaction_from_receipt_new_account(client):
    rid = _upload(client).json()["id"]
    client.patch(f"/api/receipts/{rid}", json={
        "merchant_raw": "CASH CAFE", "receipt_date": "2026-05-04", "total_amount": "12.50",
    })
    res = client.post(f"/api/receipts/{rid}/create-transaction", json={"new_account": True})
    assert res.status_code == 200, res.text
    body = res.json()
    txn_id = body["transaction_id"]

    # The receipt is now a confirmed match and no longer needs review.
    assert body["receipt"]["needs_review"] is False
    assert any(m["transaction_id"] == txn_id and m["match_status"] == "confirmed"
               for m in body["receipt"]["matches"])

    # The transaction is money out, carries the receipt's merchant/date.
    txn = _by_id(client, txn_id)
    assert Decimal(txn["amount"]) == Decimal("-12.50")
    assert txn["direction"] == "debit"
    assert txn["description_raw"] == "CASH CAFE"

    # ...in the dedicated "Cash & receipts" account.
    accounts = client.get("/api/accounts").json()
    cash = next(a for a in accounts if a["name"] == "Cash & receipts")
    assert txn["account_id"] == cash["id"]


def test_create_transaction_from_receipt_existing_account(client):
    _import(client, _curve([("2026-05-02", "SEED TXN", "-1.00")]))
    account_id = client.get("/api/accounts").json()[0]["id"]
    rid = _upload(client).json()["id"]
    client.patch(f"/api/receipts/{rid}", json={"merchant_raw": "SHOP", "total_amount": "5.00"})
    res = client.post(f"/api/receipts/{rid}/create-transaction", json={"account_id": account_id})
    assert res.status_code == 200, res.text
    assert _by_id(client, res.json()["transaction_id"])["account_id"] == account_id


def test_create_transaction_from_receipt_requires_total_and_account(client):
    rid = _upload(client).json()["id"]
    # No total set yet → 400.
    assert client.post(f"/api/receipts/{rid}/create-transaction", json={"new_account": True}).status_code == 400
    # Total set, but no account chosen → 400.
    client.patch(f"/api/receipts/{rid}", json={"total_amount": "9.99"})
    assert client.post(f"/api/receipts/{rid}/create-transaction", json={}).status_code == 400


# --- SR-D4: match scope + never destroy the sole original on auto-match ---


def _make_txn(db, *, household_id, amount="42.18", days_ago=0, archived=False) -> Transaction:
    """Insert a transaction the matcher could consider (money out, GBP)."""
    txn = Transaction(
        household_id=household_id,
        transaction_date=date.today() - timedelta(days=days_ago),
        description_raw="TESCO",
        merchant_raw="TESCO",
        amount=Decimal(f"-{amount}"),
        currency="GBP",
        direction="debit",
    )
    if archived:
        from app.services.receipt_service import _now  # local import: test-only helper

        txn.archived_at = _now()
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def _seed_receipt(db, *, merchant="TESCO", total="42.18") -> Receipt:
    receipt, _ = receipt_service.store_upload(db, "r.png", b"receipt-bytes-unique")
    receipt.merchant_raw = merchant
    receipt.receipt_date = date.today()
    receipt.total_amount = Decimal(total)
    db.commit()
    db.refresh(receipt)
    return receipt


def test_vendor_similarity_normalises_punctuation():
    """Trivial punctuation/spacing variants score > 0 (M&S vs 'M & S')."""
    assert receipt_service._vendor_similarity("M&S", "M & S") > 0
    assert receipt_service._vendor_similarity("Barnes & Noble", "Barnes&Noble") > 0
    # Unrelated names still score nothing.
    assert receipt_service._vendor_similarity("Tesco", "Aldi") == pytest.approx(0.0)


def test_refund_receipt_matches_credit_transaction(db):
    """A refund receipt (money in) matches a credit transaction of the same size.

    SR-D4: scoring compares amount magnitude on both sides, so a refund receipt —
    even one recorded with a negative total — matches a credit (positive-amount)
    transaction instead of being left unmatchable by a hardcoded debit assumption.
    """
    hh = get_or_create_default_household(db).id
    # A refund: money IN (positive amount, credit direction).
    credit_txn = Transaction(
        household_id=hh,
        transaction_date=date.today(),
        description_raw="TESCO",
        merchant_raw="TESCO",
        amount=Decimal("42.18"),
        currency="GBP",
        direction="credit",
    )
    db.add(credit_txn)
    db.commit()
    db.refresh(credit_txn)

    # The credit transaction is a candidate (no direction filter)...
    receipt = _seed_receipt(db, total="-42.18")
    assert credit_txn.id in {t.id for t in receipt_service._candidates(db, receipt)}
    # ...and a refund receipt recorded as money-in (negative total) scores a full
    # amount match against it (was 0 before the abs() fix).
    _, parts = receipt_service.score_match(receipt, credit_txn)
    assert parts["amount"] == 50
    # A conventional positive-total receipt still matches the same magnitude too.
    _, pos_parts = receipt_service.score_match(_seed_receipt(db, total="42.18"), credit_txn)
    assert pos_parts["amount"] == 50


def test_candidates_confined_to_receipt_household(db):
    """A receipt only matches transactions in its own household (latent
    cross-tenant fix); a same-household txn is still a candidate."""
    default_hh = get_or_create_default_household(db)
    other = Household(name="Other tenant")
    db.add(other)
    db.commit()
    db.refresh(other)

    receipt = _seed_receipt(db)  # store_upload assigns the default household
    assert receipt.household_id == default_hh.id
    mine = _make_txn(db, household_id=default_hh.id)
    theirs = _make_txn(db, household_id=other.id)

    ids = {t.id for t in receipt_service._candidates(db, receipt)}
    assert mine.id in ids
    assert theirs.id not in ids


def test_candidates_exclude_archived_transactions(db):
    """Archived (retention aged-out) transactions are not match candidates."""
    hh = get_or_create_default_household(db).id
    receipt = _seed_receipt(db)
    live = _make_txn(db, household_id=hh)
    gone = _make_txn(db, household_id=hh, archived=True)

    ids = {t.id for t in receipt_service._candidates(db, receipt)}
    assert live.id in ids
    assert gone.id not in ids


def test_candidates_respect_account_scope(db):
    """An explicit visible-account scope narrows candidates; None is unrestricted.

    SR-E7: a restricted (non-None) scope now confines candidates to exactly
    ``account_id IN (<set>)`` — orphan transactions (``account_id IS NULL``) are
    owner-visible only, not leaked into a restricted scope. The seeded txn has no
    account (an orphan), so it is a candidate only on the unrestricted (``None``)
    owner path; a restricted scope that doesn't list it excludes it.

    No production regression: receipt matching is always invoked unrestricted
    (``routes_receipts`` calls ``match()`` with no ``account_ids``), so orphans stay
    matchable in the real flow — this scoped path only mirrors member visibility.
    """
    hh = get_or_create_default_household(db).id
    receipt = _seed_receipt(db)
    txn = _make_txn(db, household_id=hh)

    # Unrestricted (owner) → the orphan txn is a candidate.
    assert txn.id in {t.id for t in receipt_service._candidates(db, receipt, account_ids=None)}
    # Restricted scope that doesn't include the orphan → excluded (owner-only rule).
    scoped = receipt_service._candidates(db, receipt, account_ids={999_999})
    assert {t.id for t in scoped} == set()


def test_auto_match_never_drops_sole_original(db):
    """A purely-automatic score-≥90 match must NOT delete the only copy of the
    receipt, even with 'delete original after processing' turned on."""
    settings_service.set_value(db, settings_service.RECEIPT_DELETE_AFTER_PROCESSING, "true")
    receipt = _seed_receipt(db)
    path = receipt.storage_path
    _make_txn(db, household_id=receipt.household_id)  # exact amount + same day + vendor = 90

    result = receipt_service.match(db, receipt, mode="auto")
    db.refresh(receipt)

    assert result["status"] == "auto_confirmed"
    assert result["best_score"] >= receipt_service.AUTO_MATCH
    # The sole original survives the auto-match.
    assert receipt.storage_path == path
    assert Path(path).exists()
    assert receipt.archived_at is None


def test_confirm_match_still_drops_original_when_enabled(db):
    """The drop only happens on a user-confirmed match (regression guard for the
    auto-match fix — confirm behaviour is unchanged)."""
    settings_service.set_value(db, settings_service.RECEIPT_DELETE_AFTER_PROCESSING, "true")
    receipt = _seed_receipt(db)
    path = receipt.storage_path
    txn = _make_txn(db, household_id=receipt.household_id)

    receipt_service.confirm_match(db, receipt, txn.id)
    db.refresh(receipt)
    assert receipt.storage_path is None
    assert not Path(path).exists()


# --- #23: an auto-match must clear the low-confidence review item too ---


def _open_receipt_reasons(db, receipt_id: int) -> set[str]:
    return set(
        db.scalars(
            select(ReviewItem.reason).where(
                ReviewItem.item_type == "receipt",
                ReviewItem.item_id == receipt_id,
                ReviewItem.status == "open",
            )
        ).all()
    )


def test_auto_match_resolves_low_confidence_review_item(db):
    """A low-OCR-confidence receipt (needs_review + a 'low_confidence' item) that is
    then auto-matched must have BOTH review reasons resolved, so it stops counting in
    the Review Queue — previously only 'receipt_unmatched' was cleared (#23)."""
    receipt = _seed_receipt(db)
    # Simulate the low-confidence OCR outcome: flagged for review with an open item.
    receipt.needs_review = True
    receipt_service._flag(db, receipt, "low_confidence", "Low OCR confidence — check details.")
    db.commit()
    assert _open_receipt_reasons(db, receipt.id) == {"low_confidence"}
    before = review_service.open_count(db)

    _make_txn(db, household_id=receipt.household_id)  # exact amount + same day + vendor = 90
    result = receipt_service.match(db, receipt, mode="auto")
    db.refresh(receipt)

    assert result["status"] == "auto_confirmed"
    assert receipt.needs_review is False
    # No open review item of any reason remains for this receipt (flag + queue consistent).
    assert _open_receipt_reasons(db, receipt.id) == set()
    assert review_service.open_count(db) == before - 1


# --- #18: create-transaction must reject an out-of-scope (other member's private) account ---


def _member(client, uid: str, name: str) -> int:
    client.get("/api/users/me", headers={"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name})
    row = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == uid)
    client.patch(f"/api/users/{row}", json={"role": "member", "status": "approved"})
    return row


def test_create_transaction_rejects_out_of_scope_account(client):
    """A member must not inject a receipt transaction into another member's PRIVATE
    account (IDOR write, #18): the chosen account is validated against the caller's
    visible scope BEFORE any write; an out-of-scope id is rejected and nothing lands."""
    client.get("/api/users/me")  # owner bootstrap
    _member(client, "ha-alice", "Alice")
    bob = _member(client, "ha-bob", "Bob")
    with SessionLocal() as db:
        bob_priv = Account(name="Bob Private", account_type="current_account", currency="GBP",
                           owner_user_id=bob, is_shared=False)
        db.add(bob_priv)
        db.commit()
        bob_priv_id = bob_priv.id

    alice = {"X-Remote-User-Id": "ha-alice", "X-Remote-User-Display-Name": "Alice"}
    rid = client.post(
        "/api/receipts/upload", files={"file": ("r.png", b"idor-receipt-bytes", "image/png")}, headers=alice
    ).json()["id"]
    client.patch(f"/api/receipts/{rid}", json={"merchant_raw": "SHOP", "total_amount": "5.00"}, headers=alice)

    res = client.post(
        f"/api/receipts/{rid}/create-transaction", json={"account_id": bob_priv_id}, headers=alice
    )
    assert res.status_code == 404
    # Nothing was written into Bob's private account, and the receipt stays unmatched.
    with SessionLocal() as db:
        assert db.scalars(select(Transaction).where(Transaction.account_id == bob_priv_id)).first() is None
        assert receipt_service._existing_matches(db, rid) == []


def test_owner_create_transaction_into_any_account_still_works(client):
    """Regression guard for #18: the owner (unrestricted scope) can still target any
    existing account, so the guard only narrows members, never the owner."""
    _import(client, _curve([("2026-05-02", "SEED", "-1.00")]))
    account_id = client.get("/api/accounts").json()[0]["id"]
    rid = _upload(client).json()["id"]
    client.patch(f"/api/receipts/{rid}", json={"merchant_raw": "SHOP", "total_amount": "5.00"})
    res = client.post(f"/api/receipts/{rid}/create-transaction", json={"account_id": account_id})
    assert res.status_code == 200, res.text
    assert _by_id(client, res.json()["transaction_id"])["account_id"] == account_id


# --- #26: the receipts list loads matches in one query, not one-per-receipt ---


def test_list_receipts_batches_match_lookups(client, monkeypatch):
    """The list view must not run an ``_existing_matches`` SELECT per receipt (N+1,
    #26): it eager-loads every listed receipt's matches in a single grouped query and
    hands each ``to_dict`` its own pre-fetched set. Same data, bounded query count."""
    for i in range(4):
        rid = _upload(client, content=f"receipt-{i}".encode(), name=f"r{i}.png").json()["id"]
        client.patch(f"/api/receipts/{rid}", json={"merchant_raw": f"SHOP {i}", "total_amount": "5.00"})

    calls = {"n": 0}
    real = receipt_service._existing_matches

    def _counting(db, receipt_id):
        calls["n"] += 1
        return real(db, receipt_id)

    monkeypatch.setattr(receipt_service, "_existing_matches", _counting)
    listed = client.get("/api/receipts").json()
    assert len(listed) == 4
    # The batch helper supplies matches, so the per-receipt lookup is never hit here.
    assert calls["n"] == 0
    # Shape is unchanged: matches + recommended_transaction still present per receipt.
    assert all("matches" in r and "recommended_transaction" in r for r in listed)
