"""Budget tests (spec §19, §24.9 — Stage 6)."""

from __future__ import annotations


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
    assert s["percent"] == 70.0
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
