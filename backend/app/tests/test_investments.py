"""Investment & pension accounts: value snapshots and holdings (spec §12.4, §27)."""

from __future__ import annotations

from decimal import Decimal

import pytest


def _account(client, name="Trading 212", account_type="investment") -> int:
    r = client.post("/api/investments/accounts", json={"name": name, "account_type": account_type})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _holding(client, account_id, **kw):
    body = {"symbol": "AAPL", "units": "10"} | kw
    return client.post(f"/api/investments/accounts/{account_id}/holdings", json=body)


def test_create_account_type_validated(client):
    assert _account(client, account_type="investment")
    assert _account(client, name="SIPP", account_type="pension")
    # An unknown type is rejected.
    bad = client.post("/api/investments/accounts", json={"name": "x", "account_type": "crypto"})
    assert bad.status_code == 400


def test_value_snapshots_latest_and_history(client):
    aid = _account(client, name="SIPP", account_type="pension")
    # Insert out of date order — "latest" must be by date, not insertion.
    client.post(f"/api/investments/accounts/{aid}/values", json={"as_of_date": "2026-03-31", "value": "15000"})
    client.post(f"/api/investments/accounts/{aid}/values", json={"as_of_date": "2026-01-31", "value": "12000"})
    client.post(f"/api/investments/accounts/{aid}/values", json={"as_of_date": "2026-02-28", "value": "13500"})

    hist = client.get(f"/api/investments/accounts/{aid}/values").json()
    assert [h["as_of_date"] for h in hist] == ["2026-01-31", "2026-02-28", "2026-03-31"]

    acct = next(a for a in client.get("/api/investments/accounts").json() if a["id"] == aid)
    assert Decimal(acct["current_value"]) == Decimal("15000")
    assert acct["has_holdings"] is False
    assert acct["value_count"] == 3


def test_contribution_and_withdrawal_adjust_value(client):
    aid = _account(client, name="SIPP", account_type="pension")
    client.post(f"/api/investments/accounts/{aid}/values", json={"as_of_date": "2026-01-31", "value": "10000"})

    up = client.post(f"/api/investments/accounts/{aid}/adjust", json={"amount": "500", "direction": "contribution"})
    assert up.status_code == 201
    assert Decimal(up.json()["value"]) == Decimal("10500.00")

    down = client.post(f"/api/investments/accounts/{aid}/adjust", json={"amount": "200", "direction": "withdrawal"})
    assert Decimal(down.json()["value"]) == Decimal("10300.00")

    # A bad direction is rejected.
    assert client.post(
        f"/api/investments/accounts/{aid}/adjust", json={"amount": "5", "direction": "sideways"}
    ).status_code == 400


def test_holding_market_value_and_gain(client):
    aid = _account(client)
    r = _holding(client, aid, symbol="aapl", units="10", avg_cost="150", last_price="180")
    assert r.status_code == 201
    h = r.json()
    assert h["symbol"] == "AAPL"  # uppercased
    assert Decimal(h["market_value"]) == Decimal("1800.00")  # 10 * 180
    assert Decimal(h["cost_basis"]) == Decimal("1500.00")  # 10 * 150
    assert Decimal(h["gain"]) == Decimal("300.00")
    assert h["gain_pct"] == pytest.approx(20.0)

    # Account rolls up to the holding's market value + gain.
    acct = next(a for a in client.get("/api/investments/accounts").json() if a["id"] == aid)
    assert acct["has_holdings"] is True
    assert Decimal(acct["current_value"]) == Decimal("1800.00")
    assert Decimal(acct["gain"]) == Decimal("300.00")


def test_holding_without_price_has_no_market_value(client):
    aid = _account(client)
    h = _holding(client, aid, symbol="VWRL", units="5", avg_cost="90").json()
    assert h["market_value"] is None
    assert h["gain"] is None
    # An unpriced-only account reports no current value.
    acct = next(a for a in client.get("/api/investments/accounts").json() if a["id"] == aid)
    assert acct["current_value"] is None


def test_holding_update_and_delete(client):
    aid = _account(client)
    hid = _holding(client, aid, units="10", last_price="100").json()["id"]

    patched = client.patch(f"/api/investments/holdings/{hid}", json={"units": "20", "last_price": "110"}).json()
    assert Decimal(patched["units"]) == Decimal("20")
    assert Decimal(patched["market_value"]) == Decimal("2200.00")

    # Clearing the price drops market value + the timestamp.
    cleared = client.patch(f"/api/investments/holdings/{hid}", json={"last_price": None}).json()
    assert cleared["last_price"] is None
    assert cleared["market_value"] is None
    assert cleared["last_price_at"] is None

    assert client.delete(f"/api/investments/holdings/{hid}").status_code == 204
    assert client.get(f"/api/investments/accounts/{aid}/holdings").json() == []


def test_summary_totals_and_by_type(client):
    inv = _account(client, name="ISA", account_type="investment")
    _holding(client, inv, symbol="AAPL", units="10", avg_cost="100", last_price="120")  # value 1200, cost 1000
    pen = _account(client, name="SIPP", account_type="pension")
    client.post(f"/api/investments/accounts/{pen}/values", json={"as_of_date": "2026-01-31", "value": "8000"})

    s = client.get("/api/investments/summary").json()
    assert Decimal(s["total_value"]) == Decimal("9200.00")  # 1200 + 8000
    assert Decimal(s["total_cost"]) == Decimal("1000.00")
    assert Decimal(s["total_gain"]) == Decimal("200.00")
    assert Decimal(s["by_type"]["investment"]) == Decimal("1200.00")
    assert Decimal(s["by_type"]["pension"]) == Decimal("8000.00")


def test_holding_validation(client):
    aid = _account(client)
    # units must be > 0 (schema)
    assert _holding(client, aid, units="0").status_code == 422
    # missing account → 404
    assert _holding(client, 9999, units="1").status_code == 404


def test_value_on_missing_account_404(client):
    assert client.post(
        "/api/investments/accounts/9999/values", json={"as_of_date": "2026-01-01", "value": "1"}
    ).status_code == 404


def test_savings_account_is_not_an_investment_account(client):
    # A savings account must not be reachable through the investments endpoints.
    sid = client.post("/api/savings/accounts", json={"name": "Cash ISA"}).json()["id"]
    assert client.post(
        f"/api/investments/accounts/{sid}/values", json={"as_of_date": "2026-01-01", "value": "1"}
    ).status_code == 404
    # And it doesn't show up in the investment account list.
    assert all(a["id"] != sid for a in client.get("/api/investments/accounts").json())
