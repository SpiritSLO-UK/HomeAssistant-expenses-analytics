"""Receipt tests (spec §21 — Stage 8).

OCR engine isn't required: the parser is tested directly on text, and the
upload→manual-fields→match→confirm flow doesn't depend on a Tesseract binary.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.db.session import SessionLocal
from app.models import Category, Receipt, Transaction
from app.services import receipt_parser

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
