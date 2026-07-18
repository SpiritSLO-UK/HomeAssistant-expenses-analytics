"""Savings accounts, balance snapshots and goals (Stage 12; backlog #96, #91)."""

from __future__ import annotations

from datetime import date
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


def test_history_point_in_time_total(client):
    """Total savings over time = the latest snapshot of each account as of each
    month's end. Months before the first snapshot read 0."""
    aid = _account(client, "ISA")
    _add_balance(client, aid, date.today().isoformat(), "500.00")
    h = client.get("/api/savings/history?months=3").json()
    assert len(h["months"]) == 3
    assert all({"month", "total"} == set(m) for m in h["months"])
    assert h["months"][-1]["total"] == "500.00"  # current month
    assert h["months"][0]["total"] == "0.00"     # before any snapshot


# --- N+1 refactor: outputs unchanged + bounded query count -------------------


class _SavingsBalanceSelectCounter:
    """Counts SELECT statements hitting ``savings_balances`` on the shared engine,
    so a test can prove the batched loaders don't scale queries with account count."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self, conn, cursor, statement, params, context, executemany) -> None:
        lowered = statement.lower()
        if "savings_balances" in lowered and lowered.lstrip().startswith("select"):
            self.count += 1

    def __enter__(self):
        from sqlalchemy import event

        from app.db import session as dbsession

        self._engine = dbsession.require_engine()
        event.listen(self._engine, "before_cursor_execute", self)
        return self

    def __exit__(self, *exc) -> None:
        from sqlalchemy import event

        event.remove(self._engine, "before_cursor_execute", self)


def _seed_multi_account(db, n=5):
    from app.services import savings_service

    accounts = []
    for i in range(n):
        acct = savings_service.create_account(db, name=f"Acct{i}")
        # A few out-of-order snapshots so "latest" must be resolved by date.
        savings_service.record_balance(db, acct.id, as_of=date(2026, 3, 31), balance=Decimal(f"{i}300"))
        savings_service.record_balance(db, acct.id, as_of=date(2026, 1, 31), balance=Decimal(f"{i}100"))
        savings_service.record_balance(db, acct.id, as_of=date(2026, 2, 28), balance=Decimal(f"{i}200"))
        accounts.append(acct)
    return accounts


def test_total_savings_batched_matches_naive_and_bounds_queries(db):
    from app.services import savings_service

    accounts = _seed_multi_account(db, n=5)
    # Independent (naive per-account) expected total in the single base currency.
    expected = sum(
        (savings_service.latest_balance(db, a.id) or Decimal("0") for a in accounts),
        Decimal("0.00"),
    )

    with _SavingsBalanceSelectCounter() as counter:
        total = savings_service.total_savings(db)
    assert total == expected
    # One batched load for the balances (the per-account N+1 is gone) — well under n.
    assert counter.count <= 2


def test_history_batched_matches_naive_and_bounds_queries(db):
    from app.services import savings_service

    accounts = _seed_multi_account(db, n=5)

    with _SavingsBalanceSelectCounter() as counter:
        result = savings_service.history(db, months=3)
    # Latest month's total = sum of every account's latest snapshot.
    expected_latest = sum(
        (savings_service.latest_balance(db, a.id) or Decimal("0") for a in accounts),
        Decimal("0.00"),
    )
    # Every snapshot predates the 3-month window, so each month reads the latest.
    assert Decimal(result["months"][-1]["total"]) == expected_latest
    assert all(Decimal(m["total"]) == expected_latest for m in result["months"])
    # A single batched snapshot load regardless of the 5 accounts.
    assert counter.count <= 2


# --- Compound-interest projection (pure) -------------------------------------


def _acct(rate):
    from app.models import Account

    return Account(name="x", account_type="savings", currency="GBP",
                   interest_rate=(Decimal(rate) if rate is not None else None))


def test_project_balance_monthly_compound():
    from app.services import savings_service

    # 1000 at 4.5%/yr compounded monthly for 12 months.
    got = savings_service.project_balance(_acct("4.5"), 12, principal=Decimal("1000"))
    assert got == Decimal("1045.94")


def test_project_balance_annual_compound():
    from app.services import savings_service

    # Annual compounding: one whole period in 12 months → simple 4.5%.
    got = savings_service.project_balance(
        _acct("4.5"), 12, principal=Decimal("1000"), frequency="annual"
    )
    assert got == Decimal("1045.00")
    # Fewer than a full year → no compounding period elapses, principal unchanged.
    partial = savings_service.project_balance(
        _acct("4.5"), 6, principal=Decimal("1000"), frequency="annual"
    )
    assert partial == Decimal("1000.00")


def test_project_balance_no_rate_or_zero_horizon_returns_principal():
    from app.services import savings_service

    assert savings_service.project_balance(_acct(None), 12, principal=Decimal("1000")) == Decimal("1000.00")
    assert savings_service.project_balance(_acct("4.5"), 0, principal=Decimal("1000")) == Decimal("1000.00")


def test_project_balance_rejects_unknown_frequency():
    import pytest

    from app.services import savings_service

    with pytest.raises(ValueError, match="frequency"):
        savings_service.project_balance(_acct("4.5"), 12, principal=Decimal("1000"), frequency="daily")


# --- Clearable goal fields ---------------------------------------------------


def test_update_goal_can_clear_target_date(db):
    from app.services import savings_service

    goal = savings_service.create_goal(
        db, name="Trip", target_amount=Decimal("1000"), target_date=date(2027, 1, 1)
    )
    assert goal.target_date == date(2027, 1, 1)

    # Explicit None clears the nullable field...
    savings_service.update_goal(db, goal, target_date=None)
    assert goal.target_date is None

    # ...but omitting a required field (or passing None for it) never nulls it.
    savings_service.update_goal(db, goal, name=None)
    assert goal.name == "Trip"


# --- Deposit-rate / time-to-goal forecast (pure) -----------------------------

from datetime import timedelta  # noqa: E402


def test_forecast_steady_rate_reaches_goal_by_projected_date():
    from app.services import savings_service

    today = date(2026, 1, 31)
    # 300 saved over exactly 30 days → 300/month. current=300, need 600 more → 2 months.
    snaps = [(date(2026, 1, 1), Decimal("0")), (date(2026, 1, 31), Decimal("300"))]
    fc = savings_service.forecast_goal(
        current=Decimal("300"), target=Decimal("900"), snapshots=snaps,
        target_date=date(2026, 12, 1), today=today,
    )
    assert fc["state"] == "on_track"
    assert Decimal(fc["monthly_deposit_rate"]) == Decimal("300.00")
    assert fc["months_remaining"] == 2.0
    assert fc["projected_date"] == (today + timedelta(days=60)).isoformat()
    assert fc["on_track"] is True

    # Same rate but a target_date before the projected date → behind.
    behind = savings_service.forecast_goal(
        current=Decimal("300"), target=Decimal("900"), snapshots=snaps,
        target_date=date(2026, 2, 1), today=today,
    )
    assert behind["state"] == "behind"
    assert behind["on_track"] is False

    # No target_date → still projects a date, but on_track is undetermined.
    no_deadline = savings_service.forecast_goal(
        current=Decimal("300"), target=Decimal("900"), snapshots=snaps,
        target_date=None, today=today,
    )
    assert no_deadline["state"] == "projected"
    assert no_deadline["on_track"] is None
    assert no_deadline["projected_date"] == (today + timedelta(days=60)).isoformat()


def test_forecast_no_history_gives_no_forecast():
    from app.services import savings_service

    for snaps in ([], [(date(2026, 1, 1), Decimal("100"))]):
        fc = savings_service.forecast_goal(
            current=Decimal("100"), target=Decimal("1000"), snapshots=snaps,
            target_date=None, today=date(2026, 1, 1),
        )
        assert fc["state"] == "no_forecast"
        assert fc["projected_date"] is None
        assert fc["monthly_deposit_rate"] is None


def test_forecast_already_achieved():
    from app.services import savings_service

    fc = savings_service.forecast_goal(
        current=Decimal("1000"), target=Decimal("1000"),
        snapshots=[(date(2026, 1, 1), Decimal("0")), (date(2026, 2, 1), Decimal("1000"))],
        target_date=date(2026, 6, 1), today=date(2026, 2, 1),
    )
    assert fc["state"] == "achieved"
    assert fc["projected_date"] is None
    assert fc["on_track"] is None


def test_forecast_withdrawing_is_not_progressing():
    from app.services import savings_service

    # Net withdrawals over the window → target recedes, no completion date.
    snaps = [(date(2026, 1, 1), Decimal("500")), (date(2026, 1, 31), Decimal("300"))]
    fc = savings_service.forecast_goal(
        current=Decimal("300"), target=Decimal("1000"), snapshots=snaps,
        target_date=date(2026, 6, 1), today=date(2026, 1, 31),
    )
    assert fc["state"] == "not_progressing"
    assert Decimal(fc["monthly_deposit_rate"]) == Decimal("-200.00")
    assert fc["projected_date"] is None
    assert fc["on_track"] is None


def test_goal_summary_exposes_forecast_field(db):
    from app.services import savings_service

    acct = savings_service.create_account(db, name="ISA")
    savings_service.record_balance(db, acct.id, as_of=date(2026, 1, 1), balance=Decimal("0"))
    savings_service.record_balance(db, acct.id, as_of=date(2026, 1, 31), balance=Decimal("300"))
    goal = savings_service.create_goal(
        db, name="House", target_amount=Decimal("900"), account_id=acct.id
    )

    d = savings_service.goal_to_dict(db, goal)
    # Existing fields remain untouched (backward compatible)...
    assert d["current"] == "300.00"
    assert d["remaining"] == "600.00"
    # ...and the additive forecast is present with a positive rate.
    assert Decimal(d["forecast"]["monthly_deposit_rate"]) == Decimal("300.00")
    assert d["forecast"]["state"] in {"on_track", "behind", "projected"}

    # A manual goal has no balance history → no forecast.
    manual = savings_service.create_goal(db, name="Trip", target_amount=Decimal("500"))
    assert savings_service.goal_to_dict(db, manual)["forecast"]["state"] == "no_forecast"


def test_goal_forecast_surfaces_via_api(client):
    """The deposit-rate/time-to-goal forecast is now exposed on the HTTP goals +
    summary responses (was computed but stripped by the strict GoalOut schema)."""
    aid = _account(client)
    _add_balance(client, aid, "2026-01-01", "0")
    _add_balance(client, aid, "2026-01-31", "300")
    gid = client.post(
        "/api/savings/goals", json={"name": "House", "target_amount": "900", "account_id": aid}
    ).json()["id"]

    goal = next(g for g in client.get("/api/savings/goals").json() if g["id"] == gid)
    fc = goal["forecast"]
    assert fc is not None
    # 300 saved over 30 days → 300/month net deposit rate, surfaced as a string.
    assert Decimal(fc["monthly_deposit_rate"]) == Decimal("300.00")
    assert fc["state"] in {"on_track", "behind", "projected"}
    assert fc["projected_date"] is not None

    # Same forecast object rides along on the summary payload.
    summary_goal = next(
        g for g in client.get("/api/savings/summary").json()["goals"] if g["id"] == gid
    )
    assert summary_goal["forecast"]["state"] == fc["state"]

    # A manual (unlinked) goal has no history → forecast surfaces as no_forecast.
    manual_id = client.post(
        "/api/savings/goals", json={"name": "Trip", "target_amount": "500"}
    ).json()["id"]
    manual = next(g for g in client.get("/api/savings/goals").json() if g["id"] == manual_id)
    assert manual["forecast"]["state"] == "no_forecast"
    assert manual["forecast"]["monthly_deposit_rate"] is None
