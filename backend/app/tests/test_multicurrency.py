"""Multi-currency / FX tests (backlog #29)."""

from __future__ import annotations

from decimal import Decimal

from app.services import fx_service

EUR_CSV = (
    b"Date,Description,Amount,Currency,Card,Category\n"
    b"2026-05-03,SUPERMARKET BERLIN,-20.00,EUR,Visa,Groceries\n"
    b"2026-05-10,CAFE WIEN,-5.00,EUR,Visa,Eating Out\n"
)


def _upload(client, name, content, parser="curve_csv"):
    up = client.post(
        "/api/imports/upload",
        files={"file": (name, content, "text/csv")},
        data={"parser_id": parser},
    ).json()
    client.post(f"/api/imports/{up['import_id']}/confirm")
    return up


def test_same_currency_gets_base_amount(client, samples_dir):
    _upload(client, "curve.csv", (samples_dir / "curve-sample.csv").read_bytes())
    txns = client.get("/api/transactions").json()["items"]
    # Base is GBP by default; every GBP row gets base_amount == amount, no rate needed.
    assert all(t["needs_rate"] is False for t in txns)
    assert all(t["base_amount"] == t["amount"] for t in txns)
    assert client.get("/api/dashboard/summary", params={"month": "2026-05-01"}).json()["needs_rate"] == 0


def test_foreign_currency_needs_rate_then_manual_backfill(client):
    _upload(client, "eur.csv", EUR_CSV)
    txns = client.get("/api/transactions").json()["items"]
    assert all(t["currency"] == "EUR" for t in txns)
    # Manual mode (default): no rate yet -> flagged, excluded from totals.
    assert all(t["needs_rate"] is True for t in txns)
    assert all(t["base_amount"] is None for t in txns)
    summ = client.get("/api/dashboard/summary", params={"month": "2026-05-01"}).json()
    assert summ["needs_rate"] == 2
    assert summ["spend_this_month"] == "0"

    # Add a manual rate: 1 EUR = 0.85 GBP, then backfill.
    client.post("/api/fx/rates", json={"rate_date": "2026-05-03", "quote": "EUR", "rate": "0.85"})
    client.post("/api/fx/rates", json={"rate_date": "2026-05-10", "quote": "EUR", "rate": "0.85"})
    filled = client.post("/api/fx/backfill").json()
    assert filled["filled"] == 2

    txns = client.get("/api/transactions").json()["items"]
    by_desc = {t["description_raw"]: t for t in txns}
    assert Decimal(str(by_desc["SUPERMARKET BERLIN"]["base_amount"])) == Decimal("-17.00")  # -20 * 0.85
    summ = client.get("/api/dashboard/summary", params={"month": "2026-05-01"}).json()
    assert summ["currency"] == "GBP"
    assert summ["spend_this_month"] == "21.25"  # (20 + 5) * 0.85


def test_base_currency_change_recomputes(client, samples_dir):
    _upload(client, "curve.csv", (samples_dir / "curve-sample.csv").read_bytes())
    # Switch base to EUR — GBP transactions now need a GBP->EUR rate.
    res = client.put("/api/settings", json={"base_currency": "EUR"}).json()
    assert res["base_currency"] == "EUR"
    assert res["recompute"]["recomputed"] > 0
    assert client.get("/api/fx/missing").json()["needs_rate"] > 0


def test_savings_total_converts_foreign_balance_to_base(client):
    """SR-3: a mixed-currency savings total is converted to base, not summed 1:1; a
    foreign balance with no rate is left out until a rate exists."""
    from datetime import date

    from app.db.session import SessionLocal
    from app.services import savings_service

    with SessionLocal() as db:
        gbp = savings_service.create_account(db, name="GBP Pot", currency="GBP")
        eur = savings_service.create_account(db, name="EUR Pot", currency="EUR")
        savings_service.record_balance(db, gbp.id, as_of=date.today(), balance=Decimal("100.00"))
        savings_service.record_balance(db, eur.id, as_of=date.today(), balance=Decimal("100.00"))

        # No EUR rate yet → the EUR pot is skipped; total is just the GBP pot.
        assert savings_service.total_savings(db) == Decimal("100.00")

        # 1 EUR = 0.85 GBP for today → the EUR pot converts and joins the total.
        fx_service.set_manual_rate(db, date.today(), "GBP", "EUR", Decimal("0.85"))
        assert savings_service.total_savings(db) == Decimal("185.00")  # 100 + 100*0.85
        assert savings_service.summary(db)["currency"] == "GBP"


def test_investment_summary_converts_foreign_value_to_base(client):
    """SR-3: a portfolio's total value converts each account to base before summing."""
    from datetime import date

    from app.db.session import SessionLocal
    from app.services import investment_service

    with SessionLocal() as db:
        gbp = investment_service.create_account(db, name="ISA", currency="GBP")
        eur = investment_service.create_account(db, name="DE Broker", currency="EUR")
        investment_service.record_value(db, gbp.id, as_of=date.today(), value=Decimal("1000.00"))
        investment_service.record_value(db, eur.id, as_of=date.today(), value=Decimal("1000.00"))

        # No EUR rate → the EUR account is skipped from the base total.
        assert Decimal(investment_service.summary(db)["total_value"]) == Decimal("1000.00")

        fx_service.set_manual_rate(db, date.today(), "GBP", "EUR", Decimal("0.85"))
        summ = investment_service.summary(db)
        assert summ["currency"] == "GBP"
        assert Decimal(summ["total_value"]) == Decimal("1850.00")  # 1000 + 1000*0.85


def test_vendor_stats_total_uses_base_amount(client):
    """SR-3: a vendor's total spend sums each txn's converted base_amount, not the raw
    amount, so a vendor billed in several currencies isn't summed 1:1."""
    from datetime import date

    from app.db.session import SessionLocal
    from app.models import Transaction
    from app.services import vendor_service

    with SessionLocal() as db:
        v = vendor_service.create_vendor(db, {"canonical_name": "Globex"})
        db.add(Transaction(
            transaction_date=date(2026, 5, 1), description_raw="gbp buy", merchant_id=v.id,
            amount=Decimal("-10.00"), currency="GBP", direction="debit",
            base_amount=Decimal("-10.00"), fx_rate=Decimal("1"),
        ))
        db.add(Transaction(  # €20 billed, converted to £17 base
            transaction_date=date(2026, 5, 2), description_raw="eur buy", merchant_id=v.id,
            amount=Decimal("-20.00"), currency="EUR", direction="debit",
            base_amount=Decimal("-17.00"), fx_rate=Decimal("0.85"),
        ))
        db.commit()
        stats = vendor_service.vendor_stats(db, v.id)
        assert stats["transaction_count"] == 2
        assert Decimal(stats["total_amount"]) == Decimal("-27.00")  # base -10 + -17, NOT raw -30


def test_frankfurter_mode_auto_fetches(client, monkeypatch):
    # Mock the network call so the test is offline and deterministic.
    monkeypatch.setattr(fx_service, "fetch_frankfurter", lambda on, base, quote: Decimal("0.90"))
    client.put("/api/settings", json={"fx_mode": "frankfurter"})

    _upload(client, "eur.csv", EUR_CSV)
    txns = client.get("/api/transactions").json()["items"]
    assert all(t["needs_rate"] is False for t in txns)
    assert Decimal(str(txns[0]["fx_rate"])) == Decimal("0.9")
    # The fetched rate was cached.
    rates = client.get("/api/fx/rates").json()
    assert any(r["source"] == "frankfurter" and r["quote"] == "EUR" for r in rates)


def test_upsert_rate_normalises_currency_case(db):
    # A lowercase upsert then an uppercase manual entry must hit the SAME row,
    # not create a duplicate (SR-A6).
    from datetime import date

    from sqlalchemy import select

    from app.models import FxRate

    on = date(2026, 5, 1)
    fx_service.upsert_rate(db, on, "gbp", "eur", Decimal("0.85"), "frankfurter")
    db.commit()
    fx_service.set_manual_rate(db, on, "GBP", "EUR", Decimal("0.88"))

    rows = db.scalars(select(FxRate).where(FxRate.rate_date == on)).all()
    assert len(rows) == 1
    assert rows[0].base == "GBP" and rows[0].quote == "EUR"
    assert rows[0].rate == Decimal("0.88")  # manual correction applied to the same row
    # And it's findable case-insensitively.
    assert fx_service.get_cached_rate(db, on, "gbp", "eur") == Decimal("0.88")
