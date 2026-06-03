"""Receipt tests (spec §21 — Stage 8).

OCR engine isn't required: the parser is tested directly on text, and the
upload→manual-fields→match→confirm flow doesn't depend on a Tesseract binary.
"""

from __future__ import annotations

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
    assert f["parse_confidence"] == 1.0


def test_parser_total_falls_back_to_largest_amount():
    f = receipt_parser.extract_fields("CORNER SHOP\n2026-03-01\n3.50\n9.99\n2.00")
    assert str(f["total_amount"]) == "9.99"


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


def test_upload_is_deduplicated(client):
    a = _upload(client, content=b"same-bytes").json()["id"]
    b = _upload(client, content=b"same-bytes").json()["id"]
    assert a == b
    assert len(client.get("/api/receipts").json()) == 1


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

    # ...and the original is served back for viewing.
    served = client.get(f"/api/receipts/{receipt['id']}/file")
    assert served.status_code == 200
    assert served.content == b"fake-image-bytes"


def test_attach_receipt_validation(client):
    _import(client, _curve([("2026-05-02", "BP CONNECT", "-59.80")]))
    txn = _txn(client, "BP CONNECT")
    assert client.post(
        f"/api/transactions/{txn['id']}/receipts", files={"file": ("x.png", b"", "image/png")}
    ).status_code == 400
    assert client.post(
        "/api/transactions/999999/receipts", files={"file": ("x.png", b"data", "image/png")}
    ).status_code == 404
