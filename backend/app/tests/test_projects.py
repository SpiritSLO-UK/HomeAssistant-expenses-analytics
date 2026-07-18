"""Project tests (spec §18, §24.8 — Stage 5)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest


def _curve(rows: list[tuple[str, str, str]]) -> bytes:
    head = "Date,Description,Amount,Currency,Card,Category\n"
    body = "".join(f"{d},{desc},{amt},GBP,Visa,\n" for d, desc, amt in rows)
    return (head + body).encode()


def _import(client, content: bytes, name: str = "proj.csv"):
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


# --- CRUD + validation ---

def test_project_crud(client):
    res = client.post("/api/projects", json={"name": "Bathroom renovation", "budget_amount": "5000.00"})
    assert res.status_code == 201, res.text
    pid = res.json()["id"]
    assert res.json()["status"] == "active"

    assert client.patch(f"/api/projects/{pid}", json={"status": "complete"}).json()["status"] == "complete"
    assert any(p["id"] == pid for p in client.get("/api/projects").json())
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert all(p["id"] != pid for p in client.get("/api/projects").json())


def test_project_status_validation(client):
    assert client.post("/api/projects", json={"name": "X", "status": "bogus"}).status_code == 400


def test_patch_transaction_unknown_project_400(client):
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-10.00")]))
    txn = _txn(client, "TESCO STORES")
    assert client.patch(f"/api/transactions/{txn['id']}", json={"project_id": 9999}).status_code == 400


# --- summary (spec §18.2) ---

def test_project_summary(client):
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-120.00"),
                            ("2026-05-10", "TESCO METRO", "-90.00")]))
    pid = client.post("/api/projects", json={"name": "Kitchen", "budget_amount": "300.00"}).json()["id"]
    for desc in ("TESCO STORES", "TESCO METRO"):
        t = _txn(client, desc)
        assert client.patch(f"/api/transactions/{t['id']}", json={"project_id": pid}).status_code == 200

    s = client.get(f"/api/projects/{pid}/summary").json()
    assert s["spent"] == "210.00"
    assert s["transaction_count"] == 2
    assert s["first_transaction"] == "2026-05-02"
    assert s["last_transaction"] == "2026-05-10"
    assert s["percent"] == pytest.approx(70.0)
    assert s["remaining"] == "90.00"
    groceries = _cat(client, "Groceries")
    by_cat = {row["id"]: row for row in s["by_category"]}
    assert by_cat[groceries]["total"] == "210.00"


def test_project_summary_uses_splits(client):
    # Only the split part assigned to the project should count.
    cats = client.get("/api/categories").json()
    cat_a, cat_b = cats[0]["id"], cats[1]["id"]
    _import(client, _curve([("2026-05-04", "AMAZON", "-100.00")]))
    txn = _txn(client, "AMAZON")
    pid = client.post("/api/projects", json={"name": "Smart home"}).json()["id"]
    client.post(
        f"/api/transactions/{txn['id']}/split",
        json={"splits": [{"amount": "-60.00", "category_id": cat_a, "project_id": pid},
                         {"amount": "-40.00", "category_id": cat_b}]},
    )
    s = client.get(f"/api/projects/{pid}/summary").json()
    assert s["spent"] == "60.00"
    assert s["transaction_count"] == 1


# --- dashboard ---

def test_dashboard_projects(client):
    _import(client, _curve([("2026-05-02", "SHELL FUEL", "-45.00")]))
    pid = client.post("/api/projects", json={"name": "Car"}).json()["id"]
    t = _txn(client, "SHELL FUEL")
    client.patch(f"/api/transactions/{t['id']}", json={"project_id": pid})
    rows = {p["project_id"]: p for p in client.get("/api/dashboard/projects").json()}
    assert rows[pid]["spent"] == "45.00"
    assert rows[pid]["budget"] is None


def test_project_filter_on_transactions(client):
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-10.00"),
                            ("2026-05-03", "SHELL FUEL", "-20.00")]))
    pid = client.post("/api/projects", json={"name": "Garden"}).json()["id"]
    t = _txn(client, "SHELL FUEL")
    client.patch(f"/api/transactions/{t['id']}", json={"project_id": pid})
    items = client.get(f"/api/transactions?project_id={pid}").json()["items"]
    assert len(items) == 1
    assert items[0]["description_raw"] == "SHELL FUEL"


def test_projects_history(client):
    """Project spend bucketed per month (all projects, split-aware) for the chart."""
    today = date.today().isoformat()
    _import(client, _curve([(today, "KITCHEN TILES", "-100.00")]))
    pid = client.post("/api/projects", json={"name": "Kitchen"}).json()["id"]
    t = _txn(client, "KITCHEN TILES")
    assert client.patch(f"/api/transactions/{t['id']}", json={"project_id": pid}).status_code == 200
    h = client.get("/api/projects/history?months=3").json()
    assert len(h["months"]) == 3
    assert all({"month", "total"} == set(m) for m in h["months"])
    assert h["months"][-1]["total"] == "100.00"  # current month


def test_history_ref_is_deterministic(client):
    """history(ref=<fixed date>) anchors the trailing window to a given date so the
    series is reproducible independent of today's date."""
    from app.db.session import SessionLocal
    from app.services import project_service

    _import(client, _curve([("2026-03-15", "MARCH TILES", "-100.00"),
                            ("2026-05-20", "MAY TILES", "-40.00")]))
    pid = client.post("/api/projects", json={"name": "Kitchen"}).json()["id"]
    for desc in ("MARCH TILES", "MAY TILES"):
        t = _txn(client, desc)
        assert client.patch(f"/api/transactions/{t['id']}", json={"project_id": pid}).status_code == 200

    db = SessionLocal()
    try:
        # Anchor on 2026-05: three months = Mar, Apr, May.
        h = project_service.history(db, months=3, ref=date(2026, 5, 15))
        assert [m["month"] for m in h["months"]] == ["2026-03", "2026-04", "2026-05"]
        totals = {m["month"]: m["total"] for m in h["months"]}
        assert totals["2026-03"] == "100.00"
        assert totals["2026-04"] == "0.00"
        assert totals["2026-05"] == "40.00"

        # Same call again → identical result (deterministic, not tied to today()).
        h2 = project_service.history(db, months=3, ref=date(2026, 5, 15))
        assert h2["months"] == h["months"]

        # A different ref shifts the window without touching today().
        h3 = project_service.history(db, months=2, ref=date(2026, 3, 31))
        assert [m["month"] for m in h3["months"]] == ["2026-02", "2026-03"]
        assert h3["months"][-1]["total"] == "100.00"
    finally:
        db.close()


# --- burn-down / run-rate forecast (spec §18.2) ---

def _summary_with_end(client, pid: int, end_date: date | None) -> dict:
    """summary() for a project after optionally setting its end_date, via a fresh
    session so first/last come from the imported transactions."""
    from app.db.session import SessionLocal
    from app.models import Project
    from app.services import project_service

    db = SessionLocal()
    try:
        proj = db.get(Project, pid)
        proj.end_date = end_date
        db.commit()
        db.refresh(proj)
        return project_service.summary(db, proj)
    finally:
        db.close()


def test_forecast_on_track_under_budget(client):
    """Modest run-rate against a large budget → on_track, with a burn-down remaining,
    a projected total below budget and an exhaustion date."""
    # 120 spent over a 10-day window (2026-05-01 → 2026-05-11) ⇒ 12.00/day.
    _import(client, _curve([("2026-05-01", "TILES A", "-60.00"),
                            ("2026-05-11", "TILES B", "-60.00")]))
    pid = client.post("/api/projects", json={"name": "Kitchen", "budget_amount": "5000.00"}).json()["id"]
    for desc in ("TILES A", "TILES B"):
        t = _txn(client, desc)
        assert client.patch(f"/api/transactions/{t['id']}", json={"project_id": pid}).status_code == 200

    # Planned window first→end_date = 30 days ⇒ forecast_total = 12.00 * 30 = 360.00.
    f = _summary_with_end(client, pid, date(2026, 5, 1) + timedelta(days=30))["forecast"]
    assert f is not None
    assert f["run_rate_per_day"] == "12.00"
    assert f["remaining"] == "4880.00"          # 5000 − 120
    assert f["forecast_total"] == "360.00"
    assert f["on_track"] is True
    assert f["exhaustion_date"] is not None      # rate > 0 and budget not yet spent


def test_forecast_over_budget(client):
    """Same run-rate but a small budget → projected total exceeds budget → not on track."""
    _import(client, _curve([("2026-05-01", "TILES A", "-60.00"),
                            ("2026-05-11", "TILES B", "-60.00")]))
    pid = client.post("/api/projects", json={"name": "Kitchen", "budget_amount": "300.00"}).json()["id"]
    for desc in ("TILES A", "TILES B"):
        t = _txn(client, desc)
        client.patch(f"/api/transactions/{t['id']}", json={"project_id": pid})

    f = _summary_with_end(client, pid, date(2026, 5, 1) + timedelta(days=30))["forecast"]
    assert f["forecast_total"] == "360.00"       # 12.00/day * 30 days > 300 budget
    assert f["on_track"] is False
    assert f["remaining"] == "180.00"            # 300 − 120, still positive (burn-down)


def test_forecast_skipped_without_budget(client):
    """No budget → no forecast (additive field is None, existing fields untouched)."""
    _import(client, _curve([("2026-05-02", "SHELL FUEL", "-45.00")]))
    pid = client.post("/api/projects", json={"name": "Car"}).json()["id"]
    t = _txn(client, "SHELL FUEL")
    client.patch(f"/api/transactions/{t['id']}", json={"project_id": pid})
    s = _summary_with_end(client, pid, None)     # service dict (HTTP response_model omits it)
    assert s["forecast"] is None
    assert s["spent"] == "45.00"                 # existing shape unchanged


def test_forecast_zero_history(client):
    """Budget set but no spend yet → rate-less burn-down: remaining == budget, no
    rate / forecast_total / exhaustion, and trivially on track."""
    pid = client.post("/api/projects", json={"name": "Empty", "budget_amount": "500.00"}).json()["id"]
    f = _summary_with_end(client, pid, None)["forecast"]
    assert f is not None
    assert f["remaining"] == "500.00"
    assert f["run_rate_per_day"] is None
    assert f["forecast_total"] is None
    assert f["exhaustion_date"] is None
    assert f["on_track"] is True


def test_totals_matches_summary_and_is_split_aware(client):
    """totals() (single grouped fetch) yields the same per-project spend as the
    per-project summary(), including split-only attribution."""
    from app.db.session import SessionLocal
    from app.services import project_service

    cats = client.get("/api/categories").json()
    cat_a, cat_b = cats[0]["id"], cats[1]["id"]
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-120.00"),
                            ("2026-05-04", "AMAZON", "-100.00")]))
    p_direct = client.post("/api/projects", json={"name": "Direct"}).json()["id"]
    p_split = client.post("/api/projects", json={"name": "Split"}).json()["id"]

    t_direct = _txn(client, "TESCO STORES")
    client.patch(f"/api/transactions/{t_direct['id']}", json={"project_id": p_direct})
    t_split = _txn(client, "AMAZON")
    client.post(
        f"/api/transactions/{t_split['id']}/split",
        json={"splits": [{"amount": "-60.00", "category_id": cat_a, "project_id": p_split},
                         {"amount": "-40.00", "category_id": cat_b}]},
    )

    rows = {r["project_id"]: r for r in client.get("/api/dashboard/projects").json()}
    assert rows[p_direct]["spent"] == "120.00"
    assert rows[p_split]["spent"] == "60.00"  # only the assigned split part

    # totals() agrees with each project's own summary().
    db = SessionLocal()
    try:
        computed = {r["project_id"]: r["spent"] for r in project_service.totals(db)}
        for pid in (p_direct, p_split):
            s = client.get(f"/api/projects/{pid}/summary").json()
            assert computed[pid] == s["spent"]
    finally:
        db.close()
