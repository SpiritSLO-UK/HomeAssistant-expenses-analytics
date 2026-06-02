"""Tests for backup/restore + demo data (spec §26.5; backlog #9, #10, #16)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal


def test_demo_load_is_idempotent(client):
    first = client.post("/api/backup/demo").json()
    assert first["new"] > 0
    total_after_first = client.get("/api/transactions").json()["total"]
    assert total_after_first == first["new"]

    # Re-loading must not duplicate.
    second = client.post("/api/backup/demo").json()
    assert second["new"] == 0
    assert second["duplicates"] == first["new"]
    assert client.get("/api/transactions").json()["total"] == total_after_first


def test_demo_spans_multiple_months_including_current(client):
    """The demo is generated relative to today, so it always covers the current
    month plus prior months (Trends + the current-month dashboard look populated)."""
    client.post("/api/backup/demo")
    txns = client.get("/api/transactions?limit=500").json()["items"]
    months = {t["transaction_date"][:7] for t in txns}
    assert len(months) >= 3  # current + (at least) two previous months
    assert date.today().isoformat()[:7] in months  # something in the current month


def test_demo_populates_travel(client):
    """Foreign-currency trips are seeded with FX rates so they convert + group."""
    client.post("/api/backup/demo")

    bc = client.get("/api/travel/by-currency").json()
    codes = {r["currency"] for r in bc["currencies"]}
    assert {"EUR", "USD"} <= codes
    # FX seeded → every foreign group has a positive base-currency total.
    for row in bc["currencies"]:
        assert Decimal(row["base_total"]) > 0

    trips = client.get("/api/travel/trips").json()
    assert len(trips) >= 2  # a Eurozone trip + a US trip


def test_demo_populates_business(client):
    """Some transactions are flagged business with VAT for the Business page."""
    client.post("/api/backup/demo")
    summary = client.get("/api/business/summary").json()
    assert Decimal(summary["total"]) > 0
    assert Decimal(summary["vat"]) > 0
    assert summary["transaction_count"] >= 5
    # Reclaimable VAT spread across more than one category.
    assert len(summary["by_category"]) >= 2


def test_demo_seeds_rule_projects_budgets(client):
    """One example rule, ≥2 projects (with a budget), and household budgets."""
    client.post("/api/backup/demo")
    assert len(client.get("/api/rules").json()) >= 1
    projects = client.get("/api/projects").json()
    assert len(projects) >= 2
    assert any(p.get("budget_amount") for p in projects)
    assert len(client.get("/api/budgets").json()) >= 1


def test_demo_seeds_vendor_library(client):
    """Real Vendor records are seeded and the demo transactions link to them."""
    client.post("/api/backup/demo")
    vendors = client.get("/api/vendors").json()
    assert len(vendors) >= 10
    by_name = {v["canonical_name"]: v["id"] for v in vendors}
    assert "Tesco" in by_name
    # Transactions are linked to the seeded vendor (merchant_id), not just text.
    linked = client.get(
        "/api/transactions", params={"vendor_id": by_name["Tesco"], "limit": 500}
    ).json()
    assert linked["total"] >= 1


def test_demo_project_filter_returns_assigned_transactions(client):
    """The Projects drill-down + the Transactions project filter rely on
    ?project_id= returning exactly that project's transactions."""
    client.post("/api/backup/demo")
    projects = client.get("/api/projects").json()
    spain = next((p for p in projects if p["name"] == "Spain City Break"), None)
    assert spain is not None
    linked = client.get(
        "/api/transactions", params={"project_id": spain["id"], "limit": 500}
    ).json()
    assert linked["total"] >= 1
    assert all(t["project_id"] == spain["id"] for t in linked["items"])


def test_demo_seeds_savings(client):
    """A savings account with balances + goals."""
    client.post("/api/backup/demo")
    s = client.get("/api/savings/summary").json()
    assert Decimal(s["total_savings"]) > 0
    assert len(s["accounts"]) >= 1
    assert len(s["goals"]) >= 1


def test_demo_seeds_household_and_allowance(client):
    """A second member + a child with allowance allocations and a pocket-money budget."""
    client.get("/api/users/me")  # owner row
    client.post("/api/backup/demo")
    users = client.get("/api/users").json()
    assert {"member", "child"} <= {u["role"] for u in users}
    child = next(u for u in users if u["role"] == "child")
    summary = client.get(f"/api/allowance/summary?user_id={child['id']}").json()
    assert len(summary["items"]) >= 1  # allocations
    assert len(summary["budgets"]) >= 1  # pocket-money budget


def test_demo_seeds_review_queue(client):
    """A few uncategorised purchases are flagged for review."""
    client.post("/api/backup/demo")
    assert len(client.get("/api/review").json()) >= 1
    assert client.get("/api/transactions?needs_review=true").json()["total"] >= 1


def test_demo_examples_are_idempotent(client):
    """Re-loading the demo doesn't duplicate the seeded examples."""
    client.post("/api/backup/demo")
    rules = len(client.get("/api/rules").json())
    projects = len(client.get("/api/projects").json())
    users = len(client.get("/api/users").json())
    accounts = len(client.get("/api/savings/summary").json()["accounts"])

    client.post("/api/backup/demo")
    assert len(client.get("/api/rules").json()) == rules
    assert len(client.get("/api/projects").json()) == projects
    assert len(client.get("/api/users").json()) == users
    assert len(client.get("/api/savings/summary").json()["accounts"]) == accounts


def test_database_backup_download(client):
    client.post("/api/backup/demo")
    res = client.get("/api/backup/database")
    assert res.status_code == 200
    assert res.content.startswith(b"SQLite format 3\x00")


def test_database_restore_roundtrip(client):
    client.post("/api/backup/demo")
    total = client.get("/api/transactions").json()["total"]
    snapshot = client.get("/api/backup/database").content

    res = client.post(
        "/api/backup/restore",
        files={"file": ("backup.db", snapshot, "application/octet-stream")},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "restored"
    assert client.get("/api/transactions").json()["total"] == total


def test_restore_rejects_non_sqlite(client):
    res = client.post(
        "/api/backup/restore",
        files={"file": ("bad.db", b"this is not a database", "application/octet-stream")},
    )
    assert res.status_code == 400
    assert "SQLite" in res.json()["detail"]


def test_config_export_and_import(client):
    export = client.get("/api/backup/config").json()
    assert len(export["categories"]) == 22  # seeded library
    # Re-importing the same export is a no-op (everything already present).
    same = client.post(
        "/api/backup/config",
        files={"file": ("config.json", json.dumps(export).encode(), "application/json")},
    ).json()
    assert same["categories_added"] == 0

    # Importing a config with a new category + vendor adds them.
    cfg = {
        "categories": [{"name": "Holiday Fund", "colour": "#00BCD4"}],
        "vendors": [
            {"canonical_name": "Octopus Energy", "aliases": [{"alias": "OCTOPUS", "match_type": "contains"}]}
        ],
        "settings": [{"key": "demo_setting", "value": "1"}],
    }
    added = client.post(
        "/api/backup/config",
        files={"file": ("config.json", json.dumps(cfg).encode(), "application/json")},
    ).json()
    assert added["categories_added"] == 1
    assert added["vendors_added"] == 1
    assert added["settings_set"] == 1

    names = {c["name"] for c in client.get("/api/categories").json()}
    assert "Holiday Fund" in names
    assert any(v["canonical_name"] == "Octopus Energy" for v in client.get("/api/vendors").json())
