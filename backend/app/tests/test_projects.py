"""Project tests (spec §18, §24.8 — Stage 5)."""

from __future__ import annotations

from datetime import date

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
