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
