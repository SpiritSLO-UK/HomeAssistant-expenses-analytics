"""Investment value-over-time history + day/month/year change (spec §27)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models import AccountValue
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
    # A brand-new holding has no prior price, so the day change is the whole value.
    assert Decimal(hist["change_day"]["change"]) == Decimal("1000.00")
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
    # Year (today-365): nothing that old → prev 0 → whole value, pct None.
    assert Decimal(h["change_year"]["change"]) == Decimal("11000.00")
    assert h["change_year"]["pct"] is None
    # Points cover the seeded dates (+ today already among them).
    assert len(h["points"]) == 3


def test_history_empty_when_no_investments(client):
    h = client.get("/api/investments/history").json()
    assert h["total_value"] == "0.00"
    assert h["points"] == [{"date": date.today().isoformat(), "value": "0.00"}]
