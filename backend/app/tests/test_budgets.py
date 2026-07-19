"""Budget tests (spec §19, §24.9 — Stage 6)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event, select

from app.db import session as dbsession
from app.models import Budget, Category, Transaction, TransactionSplit
from app.services import budget_service


def _curve(rows: list[tuple[str, str, str]]) -> bytes:
    head = "Date,Description,Amount,Currency,Card,Category\n"
    body = "".join(f"{d},{desc},{amt},GBP,Visa,\n" for d, desc, amt in rows)
    return (head + body).encode()


def _import(client, content: bytes, name: str = "budgets.csv"):
    up = client.post(
        "/api/imports/upload",
        files={"file": (name, content, "text/csv")},
        data={"parser_id": "curve_csv"},
    ).json()
    return client.post(f"/api/imports/{up['import_id']}/confirm").json()


def _txn(client, desc: str) -> dict:
    return next(
        t for t in client.get("/api/transactions").json()["items"] if t["description_raw"] == desc
    )


def _cat(client, name: str) -> int:
    return next(c["id"] for c in client.get("/api/categories").json() if c["name"] == name)


def _summary(client, month: str):
    return {b["budget_id"]: b for b in client.get(f"/api/budgets/summary?month={month}").json()}


# --- CRUD + validation ---

def test_budget_crud(client):
    groceries = _cat(client, "Groceries")
    res = client.post(
        "/api/budgets",
        json={"name": "Groceries", "amount": "300.00", "period": "monthly", "category_id": groceries},
    )
    assert res.status_code == 201, res.text
    bid = res.json()["id"]
    assert res.json()["currency"] == "GBP"  # defaulted to base

    assert client.patch(f"/api/budgets/{bid}", json={"amount": "350.00"}).json()["amount"] == "350.00"
    assert any(b["id"] == bid for b in client.get("/api/budgets").json())
    assert client.delete(f"/api/budgets/{bid}").status_code == 204
    assert all(b["id"] != bid for b in client.get("/api/budgets").json())


def test_budget_rejects_bad_period_and_category(client):
    assert client.post("/api/budgets", json={"name": "X", "amount": "10", "period": "fortnightly"}).status_code == 400
    assert client.post("/api/budgets", json={"name": "X", "amount": "10", "category_id": 9999}).status_code == 400


# --- spend calculation + status (spec §19.2) ---

def test_category_budget_status(client):
    groceries = _cat(client, "Groceries")
    # Two TESCO transactions in May -> Groceries by keyword.
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-120.00"),
                            ("2026-05-10", "TESCO METRO", "-90.00")]))
    bid = client.post(
        "/api/budgets",
        json={"name": "Groceries", "amount": "300.00", "period": "monthly",
              "category_id": groceries, "alert_threshold_percent": 80},
    ).json()["id"]

    s = _summary(client, "2026-05-01")[bid]
    assert s["spent"] == "210.00"
    assert s["remaining"] == "90.00"
    assert s["percent"] == pytest.approx(70.0)
    assert s["status"] == "ok"


def test_budget_warn_and_over(client):
    groceries = _cat(client, "Groceries")
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-250.00")]))
    # 250 / 300 = 83% -> warn (threshold 80).
    warn = client.post(
        "/api/budgets",
        json={"name": "Groceries", "amount": "300.00", "category_id": groceries, "alert_threshold_percent": 80},
    ).json()["id"]
    assert _summary(client, "2026-05-01")[warn]["status"] == "warn"

    # A tighter budget of 200 is overspent.
    over = client.post(
        "/api/budgets",
        json={"name": "Tight", "amount": "200.00", "category_id": groceries},
    ).json()["id"]
    s = _summary(client, "2026-05-01")[over]
    assert s["status"] == "over"
    assert s["remaining"] == "-50.00"

    # Spending exactly 100% of the budget is "over", not merely "warn" (SR-B6).
    exact = client.post(
        "/api/budgets",
        json={"name": "Exact", "amount": "250.00", "category_id": groceries, "alert_threshold_percent": 80},
    ).json()["id"]
    es = _summary(client, "2026-05-01")[exact]
    assert es["percent"] == pytest.approx(100.0)
    assert es["status"] == "over"
    assert es["remaining"] == "0.00"


def test_total_budget_counts_all_spend(client):
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-120.00"),
                            ("2026-05-03", "SHELL FUEL", "-60.00"),
                            ("2026-05-04", "SALARY", "2000.00")]))  # income excluded
    bid = client.post("/api/budgets", json={"name": "All spending", "amount": "500.00"}).json()["id"]
    s = _summary(client, "2026-05-01")[bid]
    assert s["spent"] == "180.00"  # only the two debits
    assert s["category_id"] is None and s["project_id"] is None


def test_budget_uses_splits(client):
    # A split transaction should count toward each split's category budget.
    cats = client.get("/api/categories").json()
    cat_a, cat_b = cats[0]["id"], cats[1]["id"]
    _import(client, _curve([("2026-05-04", "AMAZON", "-100.00")]))
    txn = _txn(client, "AMAZON")
    client.post(
        f"/api/transactions/{txn['id']}/split",
        json={"splits": [{"amount": "-70.00", "category_id": cat_a},
                         {"amount": "-30.00", "category_id": cat_b}]},
    )
    ab = client.post("/api/budgets", json={"name": "A", "amount": "100", "category_id": cat_a}).json()["id"]
    bb = client.post("/api/budgets", json={"name": "B", "amount": "100", "category_id": cat_b}).json()["id"]
    summary = _summary(client, "2026-05-01")
    assert summary[ab]["spent"] == "70.00"
    assert summary[bb]["spent"] == "30.00"


def test_budget_transactions_drill_down(client):
    groceries = _cat(client, "Groceries")
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-120.00"),
                            ("2026-05-10", "TESCO METRO", "-90.00")]))
    bid = client.post(
        "/api/budgets",
        json={"name": "Groceries", "amount": "300.00", "category_id": groceries},
    ).json()["id"]
    txns = client.get(f"/api/budgets/{bid}/transactions?month=2026-05-01").json()
    assert len(txns) == 2
    assert {t["description"] for t in txns} == {"TESCO STORES", "TESCO METRO"}
    assert all("amount" in t and "transaction_date" in t for t in txns)
    assert client.get("/api/budgets/99999/transactions").status_code == 404


def test_annual_view_scales_cap_and_window(client):
    groceries = _cat(client, "Groceries")
    # Spend in two different months of 2026.
    _import(client, _curve([("2026-03-10", "TESCO STORES", "-100.00"),
                            ("2026-08-10", "TESCO METRO", "-50.00")]))
    bid = client.post(
        "/api/budgets",
        json={"name": "Groceries", "amount": "300.00", "period": "monthly", "category_id": groceries},
    ).json()["id"]
    annual = {
        b["budget_id"]: b
        for b in client.get("/api/budgets/summary?month=2026-05-01&annual=true").json()
    }[bid]
    assert annual["spent"] == "150.00"        # whole year counted
    assert annual["amount"] == "3600.00"      # 300 × 12 monthly periods = annual cap
    assert annual["period_start"] == "2026-01-01"
    # The drill over the year returns both months' transactions.
    ytxns = client.get(f"/api/budgets/{bid}/transactions?month=2026-05-01&annual=true").json()
    assert len(ytxns) == 2


def test_annual_view_custom_budget_not_falsely_over(client):
    """A custom-period budget on the "This year" view must compare its ×1 cap
    against the SAME custom span, not a full year of spend (SR-B6). Otherwise a
    single-month custom budget looks wildly over-budget on the annual view."""
    groceries = _cat(client, "Groceries")
    # In-window spend (May) and out-of-window spend (August) in the same year.
    _import(client, _curve([("2026-05-05", "TESCO STORES", "-100.00"),
                            ("2026-08-10", "TESCO METRO", "-100.00")]))
    bid = client.post(
        "/api/budgets",
        json={"name": "May groceries", "amount": "300.00", "period": "custom",
              "category_id": groceries,
              "start_date": "2026-05-01", "end_date": "2026-05-31"},
    ).json()["id"]

    annual = {
        b["budget_id"]: b
        for b in client.get("/api/budgets/summary?month=2026-05-01&annual=true").json()
    }[bid]
    # Only the May transaction counts (window = the custom span, not the year).
    assert annual["spent"] == "100.00"
    # Cap stays ×1 for custom, and matches the window duration.
    assert annual["amount"] == "300.00"
    assert annual["period_start"] == "2026-05-01"
    assert annual["period_end"] == "2026-06-01"  # end-date + 1 (half-open)
    assert annual["remaining"] == "200.00"
    # 100 / 300 ≈ 33% -> well within budget, NOT over.
    assert annual["percent"] == pytest.approx(100.0 / 3.0, abs=0.1)
    assert annual["status"] == "ok"
    # Drill-down over the annual view mirrors the window: just the May txn.
    ytxns = client.get(f"/api/budgets/{bid}/transactions?month=2026-05-01&annual=true").json()
    assert len(ytxns) == 1
    assert ytxns[0]["description"] == "TESCO STORES"


def test_annual_view_monthly_budget_unchanged_regression(client):
    """Monthly budgets on the annual view are unaffected by the custom fix:
    cap ×12 and spend counted across the whole calendar year (SR-B6 regression)."""
    groceries = _cat(client, "Groceries")
    _import(client, _curve([("2026-02-10", "TESCO STORES", "-100.00"),
                            ("2026-09-10", "TESCO METRO", "-50.00")]))
    bid = client.post(
        "/api/budgets",
        json={"name": "Groceries", "amount": "300.00", "period": "monthly", "category_id": groceries},
    ).json()["id"]
    annual = {
        b["budget_id"]: b
        for b in client.get("/api/budgets/summary?month=2026-05-01&annual=true").json()
    }[bid]
    assert annual["spent"] == "150.00"        # whole year counted, both months
    assert annual["amount"] == "3600.00"      # 300 × 12 -> unchanged
    assert annual["period_start"] == "2026-01-01"
    assert annual["period_end"] == "2027-01-01"
    assert annual["status"] == "ok"


def test_weekly_period_window(client):
    groceries = _cat(client, "Groceries")
    # 2026-05-13 is a Wednesday; the week is Mon 2026-05-11 .. Sun 2026-05-17.
    _import(client, _curve([("2026-05-13", "TESCO STORES", "-40.00"),
                            ("2026-05-20", "TESCO METRO", "-25.00")]))  # next week, excluded
    bid = client.post(
        "/api/budgets",
        json={"name": "Weekly groceries", "amount": "100", "period": "weekly", "category_id": groceries},
    ).json()["id"]
    s = _summary(client, "2026-05-13")[bid]
    assert s["spent"] == "40.00"
    assert s["period_start"] == "2026-05-11"
    assert s["period_end"] == "2026-05-18"


# --- pace / prorated status (additive; does not change over/warn/ok) ---

def test_elapsed_fraction_edges():
    start, end = date(2026, 5, 1), date(2026, 5, 31)  # 30-day span
    # Period not started.
    assert budget_service.elapsed_fraction(start, end, date(2026, 4, 20)) == Decimal("0")
    # Period ended -> full period.
    assert budget_service.elapsed_fraction(start, end, date(2026, 6, 10)) == Decimal("1")
    # Day 15 of 30 -> exactly half (Decimal, not float ==).
    assert budget_service.elapsed_fraction(start, end, date(2026, 5, 15)) == Decimal("15") / Decimal("30")
    # Zero-length window is treated as fully elapsed (divide-by-zero guard).
    assert budget_service.elapsed_fraction(start, start, date(2026, 5, 1)) == Decimal("1")


def _spend(db, day: str, amount: str) -> None:
    db.add(
        Transaction(
            transaction_date=date.fromisoformat(day),
            description_raw="SPEND",
            amount=Decimal(amount),
            base_amount=Decimal(amount),
            currency="GBP",
            direction="debit",
        )
    )


def _total_budget(db, amount: str) -> Budget:
    b = Budget(name="All", period="monthly", amount=Decimal(amount), currency="GBP")
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def test_pace_fields_surface_over_api(client):
    # The prorated pace signal must be visible on the summary endpoint, not just
    # computed internally (the response schema used to strip these fields).
    groceries = _cat(client, "Groceries")
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-250.00")]))
    bid = client.post(
        "/api/budgets",
        json={"name": "Groceries", "amount": "300.00", "period": "monthly", "category_id": groceries},
    ).json()["id"]
    s = _summary(client, "2026-05-15")[bid]
    # By mid-May, 250 of a 300 cap is well over the elapsed pace -> "ahead".
    assert s["pace_status"] == "ahead"
    assert Decimal(str(s["pace_remaining"])) < Decimal("0")  # over the prorated pace
    assert 0.0 < s["elapsed_fraction"] <= 1.0
    assert Decimal(str(s["pace_expected"])) > Decimal("0")


def test_pace_ahead_mid_period(db):
    # Cap 300, May (31 days), ref = 15th -> expected ~= 145.16; 250 spent is
    # well over the elapsed pace -> "ahead" (but only 83% of cap, so status warn).
    b = _total_budget(db, "300.00")
    _spend(db, "2026-05-05", "-250.00")
    db.commit()
    s = budget_service.status_for(db, b, date(2026, 5, 15))
    assert s["pace_status"] == "ahead"
    assert Decimal(s["pace_remaining"]) < Decimal("0")  # over the prorated pace
    assert s["status"] == "warn"  # existing total-vs-cap semantics unchanged


def test_pace_behind_mid_period(db):
    # Same window, only 50 spent by the 15th -> well under the pace -> "behind".
    b = _total_budget(db, "300.00")
    _spend(db, "2026-05-05", "-50.00")
    db.commit()
    s = budget_service.status_for(db, b, date(2026, 5, 15))
    assert s["pace_status"] == "behind"
    assert Decimal(s["pace_remaining"]) > Decimal("0")  # under the prorated pace
    assert s["status"] == "ok"


def test_pace_period_not_started():
    # ref before the window -> expected 0, so any spend reads as ahead; with no
    # spend it is on_track, and elapsed_fraction is 0.
    start, end = date(2026, 6, 1), date(2026, 7, 1)
    fields = budget_service._pace_fields(Decimal("0.00"), Decimal("300.00"), start, end, date(2026, 5, 1))
    assert fields["elapsed_fraction"] == 0.0
    assert fields["pace_expected"] == "0.00"
    assert fields["pace_status"] == "on_track"


def test_pace_period_ended():
    # ref on/after the window end -> pace uses the full period (expected == cap).
    start, end = date(2026, 5, 1), date(2026, 6, 1)
    fields = budget_service._pace_fields(Decimal("300.00"), Decimal("300.00"), start, end, date(2026, 7, 1))
    assert fields["elapsed_fraction"] == 1.0
    assert fields["pace_expected"] == "300.00"
    assert fields["pace_status"] == "on_track"  # spent == full-period expectation


# --- N+1 / shared-window summary (backlog #16) ---

def _category(db, name: str) -> Category:
    cat = Category(name=name)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def _debit(db, day: str, amount: str, *, category_id: int | None = None) -> Transaction:
    txn = Transaction(
        transaction_date=date.fromisoformat(day),
        description_raw="SPEND",
        amount=Decimal(amount),
        base_amount=Decimal(amount),
        currency="GBP",
        direction="debit",
        category_id=category_id,
        fx_rate=Decimal("1"),
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def _split_debit(db, day: str, parts: list[tuple[str, int]]) -> Transaction:
    """A split debit whose parts (amount, category_id) sum to base_amount."""
    total = sum(Decimal(a) for a, _cid in parts)
    txn = Transaction(
        transaction_date=date.fromisoformat(day),
        description_raw="SPLIT",
        amount=total,
        base_amount=total,
        currency="GBP",
        direction="debit",
        fx_rate=Decimal("1"),
        is_split=True,
        splits=[TransactionSplit(amount=Decimal(a), category_id=cid) for a, cid in parts],
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def _make_budget(db, name: str, amount: str, *, category_id: int | None = None) -> Budget:
    b = Budget(name=name, period="monthly", amount=Decimal(amount), currency="GBP", category_id=category_id)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _count_transaction_scans(fn):
    """Run ``fn`` and return (result, number of top-level ``FROM transactions``
    statements executed) — the per-budget scan the N+1 fix collapses to one."""
    engine = dbsession.require_engine()
    statements: list[str] = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _before)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    scans = sum(1 for s in statements if "from transactions" in s.lower())
    return result, scans


def _seed_mixed_budgets(db):
    cat_a = _category(db, "Groceries")
    cat_b = _category(db, "Transport")
    _debit(db, "2026-05-03", "-120.00", category_id=cat_a.id)
    _debit(db, "2026-05-07", "-40.00", category_id=cat_b.id)
    # A split debit contributing 70 to A and 30 to B (split-aware allocation).
    _split_debit(db, "2026-05-10", [("-70.00", cat_a.id), ("-30.00", cat_b.id)])
    return [
        _make_budget(db, "All", "1000.00"),
        _make_budget(db, "A", "300.00", category_id=cat_a.id),
        _make_budget(db, "B", "300.00", category_id=cat_b.id),
    ]


def test_summary_matches_per_budget_status_for(db):
    """The batched ``summary`` must return the exact same figures as calling
    ``status_for`` per budget (identical window, split allocation, currency)."""
    _seed_mixed_budgets(db)
    ref = date(2026, 5, 15)

    rows = {r["budget_id"]: r for r in budget_service.summary(db, ref)}
    budgets = db.scalars(select(Budget)).all()
    for b in budgets:
        expected = budget_service.status_for(db, b, ref)
        assert rows[b.id] == expected

    # Split allocation stays correct: A gets 120 + 70, B gets 40 + 30 (Decimal).
    by_name = {r["name"]: r for r in rows.values()}
    assert Decimal(by_name["A"]["spent"]) == Decimal("190.00")
    assert Decimal(by_name["B"]["spent"]) == Decimal("70.00")
    assert Decimal(by_name["All"]["spent"]) == Decimal("260.00")  # every debit counts


def test_summary_scans_transactions_once_regardless_of_budget_count(db):
    """``summary`` fetches the window's transactions ONCE instead of once per
    budget (backlog #16 N+1), so the scan count does not grow with N budgets."""
    _seed_mixed_budgets(db)  # 3 budgets
    ref = date(2026, 5, 15)

    (rows, scans) = _count_transaction_scans(lambda: budget_service.summary(db, ref))
    assert len(rows) == 3
    # One shared windowed scan, not one per budget (would be 3 pre-fix). The
    # eager split load is a separate IN-query on transaction_splits, not counted.
    assert scans == 1
