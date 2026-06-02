"""Tests for categories, vendors, auto-categorisation and the dashboard (Stage 2).

Covers spec §29 Stage 2 acceptance: user can categorise transactions, vendor
aliases work, and the dashboard groups by category.
"""

from __future__ import annotations

from app.services import category_service, vendor_service

# A fixed month matching the sample CSV data, so dashboard tests are
# independent of the real system clock.
MONTH = "2026-05-15"


def _import_curve(client, samples_dir):
    content = (samples_dir / "curve-sample.csv").read_bytes()
    up = client.post(
        "/api/imports/upload",
        files={"file": ("curve-sample.csv", content, "text/csv")},
    ).json()
    client.post(f"/api/imports/{up['import_id']}/confirm")
    return up


def _category_id(client, name: str) -> int:
    cats = client.get("/api/categories").json()
    return next(c["id"] for c in cats if c["name"] == name)


# --- library import ---

def test_library_seeded_on_startup(client):
    cats = client.get("/api/categories").json()
    assert len(cats) == 22
    names = {c["name"] for c in cats}
    assert {"Groceries", "DIY", "Subscriptions", "Income"} <= names


def test_import_library_idempotent(db):
    first = category_service.import_library(db)
    second = category_service.import_library(db)
    assert first == 22
    assert second == 0  # nothing new on re-import
    assert len(category_service.list_categories(db)) == 22


# --- category CRUD ---

def test_category_crud(client):
    created = client.post("/api/categories", json={"name": "Childcare", "colour": "#FF8800"})
    assert created.status_code == 201
    cid = created.json()["id"]

    patched = client.patch(f"/api/categories/{cid}", json={"name": "Kids"})
    assert patched.json()["name"] == "Kids"

    assert client.delete(f"/api/categories/{cid}").status_code == 204
    assert client.get(f"/api/categories/{cid}").status_code == 404


# --- keyword auto-categorisation on import ---

def test_auto_categorisation_by_keyword(client, samples_dir):
    _import_curve(client, samples_dir)
    txns = client.get("/api/transactions").json()["items"]
    by_desc = {t["description_raw"]: t for t in txns}

    groceries = _category_id(client, "Groceries")
    diy = _category_id(client, "DIY")
    subscriptions = _category_id(client, "Subscriptions")
    transport = _category_id(client, "Transport")
    assert by_desc["TESCO STORES 3142 DARTFORD"]["category_id"] == groceries
    assert by_desc["SCREWFIX DIRECT DARTFORD"]["category_id"] == diy
    # Word-boundary matching: "netflix" -> Subscriptions, not Transport via the
    # "tfl" keyword being a substring of "neTFLix".
    assert by_desc["NETFLIX.COM"]["category_id"] == subscriptions
    assert by_desc["TfL TRAVEL CHARGE"]["category_id"] == transport
    # AMZNMKTPLACE has no keyword match -> stays uncategorised.
    assert by_desc["AMZNMKTPLACE*A1B2C3"]["category_id"] is None


def test_keyword_matching_is_word_boundary(db):
    category_service.import_library(db)
    # "tfl" must not match inside "netflix"; "netflix" -> Subscriptions.
    cat_id, _ = category_service.categorise_text(db, "NETFLIX.COM")
    sub = next(c for c in category_service.list_categories(db) if c.name == "Subscriptions")
    assert cat_id == sub.id
    # Prefix still works: "sainsbury" keyword matches "SAINSBURYS".
    cat_id2, _ = category_service.categorise_text(db, "SAINSBURYS S/MKT 0421")
    groceries = next(c for c in category_service.list_categories(db) if c.name == "Groceries")
    assert cat_id2 == groceries.id


# --- vendor alias matching ---

def test_vendor_alias_matching(db):
    category_service.import_library(db)
    groceries = next(c for c in category_service.list_categories(db) if c.name == "Groceries")
    vendor_service.create_vendor(
        db,
        {
            "canonical_name": "Tesco",
            "alias": "TESCO",
            "match_type": "contains",
            "default_category_id": groceries.id,
        },
    )
    vendor, match_type = vendor_service.match_vendor(db, "TESCO STORES 3142 DARTFORD")
    assert vendor is not None
    assert vendor.canonical_name == "Tesco"
    assert match_type == "contains"


def test_vendor_default_category_beats_keyword(client, samples_dir):
    # A vendor default category should win over the keyword fallback.
    shopping = _category_id(client, "Shopping")
    client.post(
        "/api/vendors",
        json={"canonical_name": "Amazon", "alias": "AMZNMKTPLACE", "default_category_id": shopping},
    )
    _import_curve(client, samples_dir)
    txns = client.get("/api/transactions").json()["items"]
    amazon = next(t for t in txns if t["description_raw"].startswith("AMZNMKTPLACE"))
    assert amazon["category_id"] == shopping
    assert amazon["merchant_id"] is not None


# --- manual categorisation + vendor learning (spec §15.3) ---

def test_manual_categorise_with_vendor_learning(client, samples_dir):
    _import_curve(client, samples_dir)
    txns = client.get("/api/transactions").json()["items"]
    amazon = next(t for t in txns if t["description_raw"].startswith("AMZNMKTPLACE"))
    shopping = _category_id(client, "Shopping")

    res = client.post(
        f"/api/transactions/{amazon['id']}/categorise",
        json={"category_id": shopping, "learn_vendor": True},
    )
    assert res.status_code == 200
    assert res.json()["category_id"] == shopping

    # A vendor was learned with that default category.
    vendors = client.get("/api/vendors").json()
    assert any(v["default_category_id"] == shopping for v in vendors)


def test_batch_categorise(client, samples_dir):
    _import_curve(client, samples_dir)
    txns = client.get("/api/transactions").json()["items"]
    ids = [t["id"] for t in txns[:3]]
    cash = _category_id(client, "Cash")
    res = client.post(
        "/api/transactions/categorise-batch", json={"transaction_ids": ids, "category_id": cash}
    )
    assert res.json()["updated"] == 3
    updated = client.get("/api/transactions").json()["items"]
    assert sum(1 for t in updated if t["category_id"] == cash) == 3


# --- dashboard ---

def test_dashboard_summary(client, samples_dir):
    _import_curve(client, samples_dir)
    summary = client.get("/api/dashboard/summary", params={"month": MONTH}).json()
    # Debits: 42.18+38.99+6.40+23.49+3.85+10.99+29.00 = 154.90; salary 2450 income.
    assert summary["spend_this_month"] == "154.90"
    assert summary["income_this_month"] == "2450.00"
    assert summary["net_this_month"] == "2295.10"
    assert summary["total_transactions"] == 8


def test_dashboard_category_breakdown(client, samples_dir):
    _import_curve(client, samples_dir)
    rows = client.get("/api/dashboard/categories", params={"month": MONTH}).json()
    totals = {r["name"]: r["total"] for r in rows}
    assert totals["Groceries"] == "42.18"
    assert totals["DIY"] == "38.99"
    assert totals["Subscriptions"] == "10.99"  # Netflix
    assert totals["Transport"] == "6.40"  # TfL only (not Netflix)
    assert totals["Uncategorised"] == "23.49"  # the Amazon row
    # Income (a credit) must not appear in the spend breakdown.
    assert "Income" not in totals


def test_category_cloud_privacy_is_user_editable(client):
    """A user can choose what each category sends to cloud AI (#28): a category's
    privacy level is editable (e.g. lock 'Income' to never_cloud), and invalid
    levels are rejected on both update and create."""
    income_id = _category_id(client, "Income")

    patched = client.patch(
        f"/api/categories/{income_id}", json={"privacy_sensitivity": "never_cloud"}
    )
    assert patched.status_code == 200
    assert patched.json()["privacy_sensitivity"] == "never_cloud"

    # It persists on the list view (so the 🔒 shows in the UI).
    income = next(c for c in client.get("/api/categories").json() if c["id"] == income_id)
    assert income["privacy_sensitivity"] == "never_cloud"

    # Invalid levels are rejected (defense-in-depth) on update and create.
    assert client.patch(
        f"/api/categories/{income_id}", json={"privacy_sensitivity": "public"}
    ).status_code == 400
    assert client.post(
        "/api/categories", json={"name": "Bogus", "privacy_sensitivity": "weird"}
    ).status_code == 400
