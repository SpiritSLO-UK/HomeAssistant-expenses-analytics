"""Investment value-over-time history + day/month/year change (spec §27)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models import AccountValue, HoldingPrice
from app.services import investment_service


def test_holding_price_recorded_on_create_and_update(client):
    aid = client.post(
        "/api/investments/accounts", json={"name": "ISA", "account_type": "investment"}
    ).json()["id"]
    h = client.post(
        f"/api/investments/accounts/{aid}/holdings",
        json={"symbol": "AAPL", "units": "10", "last_price": "100"},
    ).json()

    hist = client.get("/api/investments/history").json()
    assert Decimal(hist["total_value"]) == Decimal("1000.00")  # 10 * 100
    assert hist["points"], "expected at least one history point"
    # A brand-new holding has no prior price → nothing comparable, so the day change is
    # 0, not the whole value: adding a position isn't a gain (SR like-for-like fix).
    assert Decimal(hist["change_day"]["change"]) == Decimal("0.00")
    assert hist["change_day"]["pct"] is None

    client.patch(f"/api/investments/holdings/{h['id']}", json={"last_price": "120"})
    hist = client.get("/api/investments/history").json()
    assert Decimal(hist["total_value"]) == Decimal("1200.00")


def test_period_changes_from_value_snapshots(db):
    today = date.today()
    acct = investment_service.create_account(db, name="SIPP", account_type="pension")
    # Seed snapshots at specific past dates (record_value uses today, so insert directly).
    for offset, value in [(300, "8000"), (20, "10000"), (0, "11000")]:
        db.add(AccountValue(
            account_id=acct.id, as_of_date=today - timedelta(days=offset),
            value=Decimal(value), currency="GBP",
        ))
    db.commit()

    h = investment_service.history(db)
    assert Decimal(h["total_value"]) == Decimal("11000.00")
    # Day: vs the 10000 snapshot (today-20, the latest ≤ yesterday).
    assert Decimal(h["change_day"]["change"]) == Decimal("1000.00")
    # Month (today-30): vs the 8000 snapshot (today-300).
    assert Decimal(h["change_month"]["change"]) == Decimal("3000.00")
    # Year (today-365): the account wasn't valued that long ago → not comparable, so the
    # change is 0 (not the whole value), pct None.
    assert Decimal(h["change_year"]["change"]) == Decimal("0.00")
    assert h["change_year"]["pct"] is None
    # Points cover the seeded dates (+ today already among them).
    assert len(h["points"]) == 3


def test_change_excludes_newly_added_holdings(db):
    """Like-for-like: a holding added after the comparison date doesn't inflate the
    change — only positions priced at BOTH endpoints count (SR money-correctness fix)."""
    today = date.today()
    acct = investment_service.create_account(db, name="ISA", account_type="investment")
    held = investment_service.create_holding(db, acct.id, symbol="AAA", units="10", last_price="100")
    # AAA was also priced 40 days ago at 90 → +£100 over the month window (10 * (100 - 90)).
    db.add(HoldingPrice(holding_id=held.id, as_of_date=today - timedelta(days=40), price=Decimal("90")))
    db.commit()
    # BBB is brand-new (priced only today) → must NOT count toward the month change.
    investment_service.create_holding(db, acct.id, symbol="BBB", units="5", last_price="200")

    h = investment_service.history(db)
    assert Decimal(h["total_value"]) == Decimal("2000.00")            # 10*100 + 5*200
    assert Decimal(h["change_month"]["change"]) == Decimal("100.00")  # AAA only; BBB excluded
    assert h["change_month"]["pct"] == pytest.approx(11.1)            # 100 / 900


def test_history_empty_when_no_investments(client):
    h = client.get("/api/investments/history").json()
    assert h["total_value"] == "0.00"
    assert h["points"] == [{"date": date.today().isoformat(), "value": "0.00"}]
