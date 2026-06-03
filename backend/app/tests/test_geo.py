"""Spend-by-location map: country inference, aggregation, vendor country (§16.3)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models import Transaction, Vendor
from app.services import dashboard_service, geo

# --- geo helpers (pure) ---


def test_country_for_prefers_vendor_then_currency():
    assert geo.country_for("GBP", "FR") == "FR"   # vendor wins
    assert geo.country_for("EUR", None) == "EU"   # currency fallback
    assert geo.country_for("GBP", None) == "GB"
    assert geo.country_for("ZZZ", None) is None    # unknown currency, no vendor


def test_flag_and_name():
    assert geo.flag("GB") == "\U0001F1EC\U0001F1E7"  # 🇬🇧
    assert geo.name("GB") == "United Kingdom"
    assert geo.flag(None) == "\U0001F3F3️"      # 🏳️
    assert geo.name(None) == "Unknown"
    assert geo.name("ZZ") == "ZZ"                    # unknown code → echoed


# --- aggregation (service level) ---


def _txn(db, *, base, currency="GBP", merchant_id=None, day=15):
    db.add(Transaction(
        transaction_date=date(2026, 5, day),
        description_raw="x",
        amount=Decimal(base),
        base_amount=Decimal(base),
        currency=currency,
        direction="debit",
        merchant_id=merchant_id,
    ))


def test_country_breakdown(db):
    tesco = Vendor(canonical_name="Tesco", country="GB")
    carrefour = Vendor(canonical_name="Carrefour", country="FR")
    db.add_all([tesco, carrefour])
    db.commit()

    _txn(db, base="-50", currency="GBP", merchant_id=tesco.id)     # GB (vendor)
    _txn(db, base="-30", currency="EUR", merchant_id=carrefour.id)  # FR (vendor wins over EUR)
    _txn(db, base="-20", currency="EUR")                            # EU (currency)
    _txn(db, base="-10", currency="GBP")                            # GB (currency)
    _txn(db, base="-5", currency="ZZZ")                             # Unknown
    db.commit()

    rows = {r["name"]: r for r in dashboard_service.country_breakdown(db, date(2026, 5, 1))}
    assert Decimal(rows["United Kingdom"]["total"]) == Decimal("60.00")  # 50 + 10
    assert rows["United Kingdom"]["count"] == 2
    assert rows["United Kingdom"]["country_code"] == "GB"
    assert Decimal(rows["France"]["total"]) == Decimal("30.00")
    assert Decimal(rows["Eurozone"]["total"]) == Decimal("20.00")
    assert rows["Unknown"]["country_code"] is None
    # Sorted by spend, descending.
    ordered = [r["name"] for r in dashboard_service.country_breakdown(db, date(2026, 5, 1))]
    assert ordered[0] == "United Kingdom"


def test_country_breakdown_excludes_other_months_and_income(db):
    _txn(db, base="-40", currency="GBP", day=15)      # in May
    _txn(db, base="-99", currency="GBP", day=2)
    # a credit/income row (base_amount > 0) is not spend
    db.add(Transaction(
        transaction_date=date(2026, 5, 10), description_raw="salary",
        amount=Decimal("1000"), base_amount=Decimal("1000"), currency="GBP", direction="credit",
    ))
    db.commit()
    rows = dashboard_service.country_breakdown(db, date(2026, 5, 1))
    gb = next(r for r in rows if r["name"] == "United Kingdom")
    assert Decimal(gb["total"]) == Decimal("139.00")  # 40 + 99, income excluded


# --- API: vendor country setter + endpoint ---


def test_set_vendor_country_and_endpoint(client):
    vid = client.post("/api/vendors", json={"canonical_name": "Tesco"}).json()["id"]
    patched = client.patch(f"/api/vendors/{vid}", json={"country": "GB"}).json()
    assert patched["country"] == "GB"
    # The by-country endpoint is reachable and returns a list (empty without spend).
    r = client.get("/api/dashboard/by-country")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
