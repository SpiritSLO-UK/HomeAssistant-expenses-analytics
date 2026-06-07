"""Curve overlay-card cross-account dedup (user ask: Curve is transient).

Curve forwards each payment to an underlying funding card, so the same spend
also appears on that card's own statement. The user maps each Curve ``Card Name``
to a real account; importing both statements then dedups across them — auto-skip
when the bank row carries a Curve marker, flag-but-keep when it doesn't.
"""

from __future__ import annotations

# Real Curve app export: positive funding-card amounts (spend), Card Name column.
CURVE_EXPORT = (
    b"Export For,Date (YYYY-MM-DD as UTC),Time (HH:MM:SS as UTC),Merchant,"
    b"Txn Amount (Funding Card),Txn Currency (Funding Card),Card Name,"
    b"Card Last 4 Digits,Type,Category\n"
    b"CSV,2025-07-20,21:14:46,Kwik Save,3.69,GBP,Credit Card,1006,Personal,Groceries\n"
    b"CSV,2025-07-21,08:12:30,Costa Coffee,3.85,GBP,Credit Card,1006,Personal,Eating Out\n"
)

CURVE_LABEL = "Credit Card ••1006"


def _make_account(client, name, account_type="credit_card"):
    res = client.post("/api/accounts", json={"name": name, "account_type": account_type})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _upload(client, filename, content, account_id, parser_id=None):
    data = {"account_id": str(account_id)}
    if parser_id:
        data["parser_id"] = parser_id
    return client.post(
        "/api/imports/upload",
        files={"file": (filename, content, "text/csv")},
        data=data,
    )


def _import(client, filename, content, account_id, parser_id=None):
    body = _upload(client, filename, content, account_id, parser_id).json()
    confirm = client.post(f"/api/imports/{body['import_id']}/confirm").json()
    return body, confirm


# --- funding-card label surfaces + link CRUD -------------------------------

def test_upload_surfaces_funding_labels(client):
    curve = _make_account(client, "Curve")
    body = _upload(client, "curve.csv", CURVE_EXPORT, curve).json()
    labels = {fl["label"]: fl for fl in body["funding_labels"]}
    assert CURVE_LABEL in labels
    assert labels[CURVE_LABEL]["count"] == 2
    assert labels[CURVE_LABEL]["account_id"] is None  # not mapped yet


def test_funding_link_crud(client):
    barclays = _make_account(client, "Barclays", "current_account")
    # Map the label.
    res = client.put("/api/imports/funding-links", json={"label": CURVE_LABEL, "account_id": barclays})
    assert res.status_code == 200, res.text
    links = res.json()
    assert any(link["label"] == CURVE_LABEL and link["account_id"] == barclays for link in links)
    # Unknown account → 400.
    assert client.put("/api/imports/funding-links", json={"label": "X", "account_id": 9999}).status_code == 400
    # Clear it (null account_id).
    cleared = client.put("/api/imports/funding-links", json={"label": CURVE_LABEL, "account_id": None}).json()
    assert all(link["label"] != CURVE_LABEL for link in cleared)


def test_no_links_means_no_cross_dedup(client):
    """Without a mapping, a Curve import is unaffected (both rows new)."""
    curve = _make_account(client, "Curve")
    body, confirm = _import(client, "curve.csv", CURVE_EXPORT, curve)
    assert confirm["report"]["new"] == 2
    assert confirm["report"]["duplicates"] == 0


# --- direction B: import the bank statement AFTER Curve --------------------

def test_bank_after_curve_skips_marked_match(client):
    curve = _make_account(client, "Curve")
    barclays = _make_account(client, "Barclays", "current_account")
    _import(client, "curve.csv", CURVE_EXPORT, curve)
    client.put("/api/imports/funding-links", json={"label": CURVE_LABEL, "account_id": barclays})

    # Barclays statement (own parser → no funding_source): one Curve-marked row
    # (matches Kwik Save) + one unrelated row.
    bank = (
        b"Number,Date,Account,Amount,Subcategory,Memo\n"
        b"1,21/07/2025,20-11-22 12345678,-3.69,Groceries,CRV*KWIK SAVE\n"  # marked → skip
        b"2,25/07/2025,20-11-22 12345678,-12.00,Groceries,Tesco\n"          # unrelated → new
    )
    preview = _upload(client, "barclays.csv", bank, barclays, parser_id="barclays_csv").json()
    reasons = [r.get("dup_reason") for r in preview["preview"] if r["is_duplicate"]]
    assert any(r and "Curve" in r for r in reasons)

    confirm = client.post(f"/api/imports/{preview['import_id']}/confirm").json()
    assert confirm["report"]["new"] == 1       # only Tesco
    assert confirm["report"]["duplicates"] == 1  # the CRV*KWIK SAVE match


def test_bank_after_curve_keeps_unmarked_match_flagged(client):
    curve = _make_account(client, "Curve")
    barclays = _make_account(client, "Barclays", "current_account")
    _import(client, "curve.csv", CURVE_EXPORT, curve)
    client.put("/api/imports/funding-links", json={"label": CURVE_LABEL, "account_id": barclays})

    # Same amount/date as Kwik Save but NO Curve marker → possible, kept + flagged.
    bank = (
        b"Number,Date,Account,Amount,Subcategory,Memo\n"
        b"1,21/07/2025,20-11-22 12345678,-3.69,Groceries,KWIK SAVE STORES\n"
    )
    preview = _upload(client, "barclays.csv", bank, barclays, parser_id="barclays_csv").json()
    row = preview["preview"][0]
    assert row["is_duplicate"] is False
    assert row["warning"] and "Possible match" in row["warning"]

    confirm = client.post(f"/api/imports/{preview['import_id']}/confirm").json()
    assert confirm["report"]["new"] == 1  # kept

    # Flagged for review.
    flagged = client.get("/api/transactions", params={"account_id": barclays, "needs_review": "true"}).json()
    assert flagged["total"] == 1
    assert flagged["items"][0]["review_reason"] == "possible_duplicate"


# --- direction A: import Curve AFTER the bank statement --------------------

def test_curve_after_bank_skips_marked_match(client):
    curve = _make_account(client, "Curve")
    barclays = _make_account(client, "Barclays", "current_account")
    # Bank first, with a Curve-marked settlement matching the Kwik Save row.
    bank = (
        b"Number,Date,Account,Amount,Subcategory,Memo\n"
        b"1,20/07/2025,20-11-22 12345678,-3.69,Groceries,CRV*KWIK SAVE\n"
    )
    _import(client, "barclays.csv", bank, barclays, parser_id="barclays_csv")
    client.put("/api/imports/funding-links", json={"label": CURVE_LABEL, "account_id": barclays})

    preview = _upload(client, "curve.csv", CURVE_EXPORT, curve).json()
    # Kwik Save (3.69) skipped; Costa (3.85) new.
    assert preview["report"]["new"] == 1
    assert preview["report"]["duplicates"] == 1
    confirm = client.post(f"/api/imports/{preview['import_id']}/confirm").json()
    assert confirm["report"]["new"] == 1
    assert confirm["report"]["duplicates"] == 1
