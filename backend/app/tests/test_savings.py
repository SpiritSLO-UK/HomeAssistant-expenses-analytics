"""Savings accounts, balance snapshots and goals (Stage 12; backlog #96, #91)."""

from __future__ import annotations

from decimal import Decimal


def _account(client, name="ISA") -> int:
    r = client.post("/api/savings/accounts", json={"name": name})
    assert r.status_code == 201
    return r.json()["id"]


def _add_balance(client, account_id, as_of, balance):
    return client.post(
        f"/api/savings/accounts/{account_id}/balances",
        json={"as_of_date": as_of, "balance": balance},
    )


def test_account_balances_and_latest(client):
    aid = _account(client)
    # Insert out of date order — "latest" must be by date, not insertion.
    _add_balance(client, aid, "2026-03-31", "1500")
    _add_balance(client, aid, "2026-01-31", "1000")
    _add_balance(client, aid, "2026-02-28", "1200")

    history = client.get(f"/api/savings/accounts/{aid}/balances").json()
    assert [h["as_of_date"] for h in history] == ["2026-01-31", "2026-02-28", "2026-03-31"]

    summary = client.get("/api/savings/summary").json()
    assert Decimal(summary["total_savings"]) == Decimal("1500")
    assert Decimal(summary["accounts"][0]["latest_balance"]) == Decimal("1500")


def test_balance_on_missing_account_404(client):
    assert _add_balance(client, 9999, "2026-01-01", "10").status_code == 404


def test_total_sums_all_savings_accounts(client):
    a1 = _account(client, "ISA")
    a2 = _account(client, "Emergency")
    _add_balance(client, a1, "2026-01-31", "1000")
    _add_balance(client, a2, "2026-01-31", "2500")
    assert Decimal(client.get("/api/savings/summary").json()["total_savings"]) == Decimal("3500")


def test_deposit_and_withdraw_adjust_latest_balance(client):
    aid = _account(client)
    _add_balance(client, aid, "2026-01-31", "1000")

    dep = client.post(f"/api/savings/accounts/{aid}/adjust", json={"amount": "250", "direction": "deposit"})
    assert dep.status_code == 201
    assert Decimal(dep.json()["balance"]) == Decimal("1250.00")

    wd = client.post(f"/api/savings/accounts/{aid}/adjust", json={"amount": "100", "direction": "withdraw"})
    assert Decimal(wd.json()["balance"]) == Decimal("1150.00")

    assert Decimal(client.get("/api/savings/summary").json()["total_savings"]) == Decimal("1150.00")
    # A bad direction is rejected.
    assert client.post(f"/api/savings/accounts/{aid}/adjust", json={"amount": "5", "direction": "sideways"}).status_code == 400


def test_interest_rate_and_projection(client):
    aid = _account(client)
    _add_balance(client, aid, "2026-01-31", "2000")

    patched = client.patch(f"/api/savings/accounts/{aid}", json={"interest_rate": "4.5"})
    assert patched.status_code == 200
    acct = next(a for a in client.get("/api/savings/summary").json()["accounts"] if a["id"] == aid)
    assert Decimal(acct["interest_rate"]) == Decimal("4.5")
    assert Decimal(acct["projected_annual_interest"]) == Decimal("90.00")  # 2000 * 4.5%

    # Clearing the rate drops the projection.
    client.patch(f"/api/savings/accounts/{aid}", json={"interest_rate": None})
    acct = next(a for a in client.get("/api/savings/summary").json()["accounts"] if a["id"] == aid)
    assert acct["interest_rate"] is None
    assert acct["projected_annual_interest"] is None


def test_goal_linked_to_account_tracks_balance(client):
    aid = _account(client)
    _add_balance(client, aid, "2026-01-31", "500")
    gid = client.post(
        "/api/savings/goals",
        json={"name": "House", "target_amount": "1000", "account_id": aid},
    ).json()["id"]

    goal = next(g for g in client.get("/api/savings/goals").json() if g["id"] == gid)
    assert Decimal(goal["current"]) == Decimal("500")
    assert round(goal["percent"]) == 50
    assert goal["status"] == "active"

    _add_balance(client, aid, "2026-06-30", "1000")  # reached
    goal = next(g for g in client.get("/api/savings/goals").json() if g["id"] == gid)
    assert Decimal(goal["current"]) == Decimal("1000")
    assert round(goal["percent"]) == 100
    assert goal["status"] == "achieved"


def test_goal_manual_progress(client):
    r = client.post(
        "/api/savings/goals",
        json={"name": "Holiday", "target_amount": "200", "current_amount": "50"},
    )
    assert r.status_code == 201
    goal = r.json()
    assert Decimal(goal["current"]) == Decimal("50")
    assert round(goal["percent"]) == 25


def test_goal_validation(client):
    # target must be > 0 (schema)
    assert client.post("/api/savings/goals", json={"name": "x", "target_amount": "0"}).status_code == 422
    gid = client.post("/api/savings/goals", json={"name": "x", "target_amount": "100"}).json()["id"]
    # invalid status (service guard)
    assert client.patch(f"/api/savings/goals/{gid}", json={"status": "bogus"}).status_code == 400


def test_goal_update_and_delete(client):
    gid = client.post("/api/savings/goals", json={"name": "Car", "target_amount": "5000"}).json()["id"]
    client.patch(f"/api/savings/goals/{gid}", json={"current_amount": "2500"})
    goal = next(g for g in client.get("/api/savings/goals").json() if g["id"] == gid)
    assert round(goal["percent"]) == 50
    assert client.delete(f"/api/savings/goals/{gid}").status_code == 204
    assert all(g["id"] != gid for g in client.get("/api/savings/goals").json())
