"""Spend-by-location map: country inference, aggregation, vendor country (§16.3)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models import Transaction, Vendor
from app.services import dashboard_service, geo

# --- geo helpers (pure) ---


def test_country_for_precedence():
    # txn country wins over vendor, which wins over currency.
    assert geo.country_for("EUR", "FR", "ES") == "ES"  # txn (a trip to Spain) wins
    assert geo.country_for("GBP", "FR") == "FR"          # vendor over currency
    assert geo.country_for("EUR", None) == "EU"          # currency fallback (coarse)
    assert geo.country_for("GBP", None) == "GB"
    assert geo.country_for("ZZZ", None) is None           # unknown currency, no vendor


def test_country_for_default_fallback():
    # The household default vendor country slots in below txn/vendor, above the
    # currency guess — so it catches no-country spend but never overrides a real one.
    assert geo.country_for("USD", "FR", None, "GB") == "FR"   # vendor still wins
    assert geo.country_for("USD", None, "ES", "GB") == "ES"   # txn still wins
    assert geo.country_for("USD", None, None, "GB") == "GB"   # default beats currency guess
    assert geo.country_for(None, None, None, "GB") == "GB"    # default with no currency
    assert geo.country_for("USD", None, None, None) == "US"   # no default → currency guess


def test_flag_and_name():
    assert geo.flag("GB") == "\U0001F1EC\U0001F1E7"  # 🇬🇧
    assert geo.name("GB") == "United Kingdom"
    assert geo.flag(None) == "\U0001F3F3️"      # 🏳️
    assert geo.name(None) == "Unknown"
    assert geo.name("ZZ") == "ZZ"                    # unknown code → echoed


# --- aggregation (service level) ---


def _txn(db, *, base, currency="GBP", merchant_id=None, day=15, country=None):
    db.add(Transaction(
        transaction_date=date(2026, 5, day),
        description_raw="x",
        amount=Decimal(base),
        base_amount=Decimal(base),
        currency=currency,
        direction="debit",
        merchant_id=merchant_id,
        country=country,
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


def test_txn_country_overrides_currency_eu(db):
    # A EUR trip to Spain, tagged ES, shows as Spain — not the coarse 'Eurozone'.
    _txn(db, base="-80", currency="EUR", country="ES")
    _txn(db, base="-20", currency="EUR")  # untagged EUR → Eurozone
    db.commit()
    rows = {r["name"]: r for r in dashboard_service.country_breakdown(db, date(2026, 5, 1))}
    assert Decimal(rows["Spain"]["total"]) == Decimal("80.00")
    assert rows["Spain"]["country_code"] == "ES"
    assert Decimal(rows["Eurozone"]["total"]) == Decimal("20.00")


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


def test_country_breakdown_uses_default_vendor_country(db):
    from app.services import settings_service

    settings_service.set_value(db, settings_service.DEFAULT_VENDOR_COUNTRY, "GB")
    fr_vendor = Vendor(canonical_name="Carrefour", country="FR")
    db.add(fr_vendor)
    db.commit()

    _txn(db, base="-40", currency="USD")                              # no country → default GB
    _txn(db, base="-15", currency="GBP", merchant_id=fr_vendor.id)    # vendor FR wins over default
    db.commit()

    rows = {r["name"]: r for r in dashboard_service.country_breakdown(db, date(2026, 5, 1))}
    assert Decimal(rows["United Kingdom"]["total"]) == Decimal("40.00")  # default applied
    assert "United States" not in rows                                   # currency guess overridden
    assert Decimal(rows["France"]["total"]) == Decimal("15.00")         # explicit vendor country kept


# --- API: vendor country setter + endpoint ---


def test_set_vendor_country_and_endpoint(client):
    vid = client.post("/api/vendors", json={"canonical_name": "Tesco"}).json()["id"]
    patched = client.patch(f"/api/vendors/{vid}", json={"country": "GB"}).json()
    assert patched["country"] == "GB"
    # The by-country endpoint is reachable and returns a list (empty without spend).
    r = client.get("/api/dashboard/by-country")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
