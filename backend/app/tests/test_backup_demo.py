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

    # Re-loading refreshes (remove-then-reload) rather than stacking a second copy:
    # the same-day dataset is regenerated, so the transaction total is unchanged —
    # not doubled — and the manifest never accumulates two datasets.
    second = client.post("/api/backup/demo").json()
    assert second["new"] == first["new"]
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


def test_demo_seeds_assets_and_investments(client):
    """The demo seeds a car (with refuels → MPG) + a home (Cars & Assets), and an
    investment account with holdings + a pension with a value snapshot (Investments)."""
    client.post("/api/backup/demo")

    assets = client.get("/api/assets").json()
    assert {"car", "home"} <= {a["kind"] for a in assets}
    car = next(a for a in assets if a["kind"] == "car")
    logs = client.get(f"/api/assets/{car['id']}/logs").json()
    assert sum(1 for lg in logs if lg["kind"] == "refuel") >= 3  # → tank-to-tank MPG

    accounts = client.get("/api/investments/accounts").json()
    assert {"investment", "pension"} <= {a["account_type"] for a in accounts}
    assert Decimal(client.get("/api/investments/summary").json()["total_value"]) > 0


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


def test_demo_seeds_merge_candidate_vendors(client):
    """Near-duplicate vendors (with real spend) are seeded so the vendor-merge UI
    has obvious candidates to consolidate."""
    client.post("/api/backup/demo")
    vendors = client.get("/api/vendors").json()
    by_name = {v["canonical_name"]: v["id"] for v in vendors}
    # Each duplicate exists alongside its canonical original.
    for dup, original in (("Amazon UK", "Amazon"), ("Costa", "Costa Coffee")):
        assert dup in by_name and original in by_name
        # The duplicate carries at least one re-pointed demo transaction (so a merge
        # actually consolidates spend).
        linked = client.get(
            "/api/transactions", params={"vendor_id": by_name[dup], "limit": 500}
        ).json()
        assert linked["total"] >= 1


def test_demo_seeds_varied_subscription_cadences(client):
    """Subscription detection surfaces more than just monthly cycles — the demo now
    seeds fortnightly and bi-monthly recurring merchants too."""
    client.post("/api/backup/demo")
    subs = client.get("/api/subscriptions").json()
    freqs = {s["frequency"] for s in subs}
    assert {"monthly", "fortnightly", "bi_monthly"} <= freqs


def test_demo_seeds_investment_price_history(client):
    """Holdings get a back-filled price series so the portfolio chart renders a line
    (more than one point)."""
    client.post("/api/backup/demo")
    hist = client.get("/api/investments/history").json()
    assert len(hist["points"]) >= 2


def test_demo_seeds_a_split_transaction(client):
    """One transaction is split across two categories (the split UI example)."""
    client.post("/api/backup/demo")
    txns = client.get("/api/transactions?limit=500").json()["items"]
    split_ids = [t["id"] for t in txns if t.get("is_split")]
    assert len(split_ids) >= 1
    splits = client.get(f"/api/transactions/{split_ids[0]}/splits").json()
    assert splits["is_split"] is True
    assert len(splits["splits"]) >= 2


def test_demo_seeds_a_receipt(client):
    """A receipt is seeded and attached to a transaction."""
    client.post("/api/backup/demo")
    receipts = client.get("/api/receipts").json()
    assert len(receipts) >= 1


def test_demo_remove_clears_receipts_splits_and_prices(client, db):
    """The load→remove round-trip leaves no orphans: the seeded receipt, split rows
    and holding price history all go when the demo is removed."""
    from sqlalchemy import func, select

    from app.models import HoldingPrice, TransactionSplit

    def _counts():
        # End any open read transaction so the next query sees the latest commits
        # from the client's requests (separate connection, WAL snapshot).
        db.rollback()
        return (
            db.scalar(select(func.count()).select_from(TransactionSplit)),
            db.scalar(select(func.count()).select_from(HoldingPrice)),
        )

    client.post("/api/backup/demo")
    assert len(client.get("/api/receipts").json()) >= 1
    splits, prices = _counts()
    assert splits >= 1 and prices >= 1

    body = client.delete("/api/backup/demo").json()
    assert body["removed"] is True
    assert body["counts"].get("receipts", 0) >= 1

    assert client.get("/api/receipts").json() == []
    # No orphaned split parts or holding price points remain.
    assert _counts() == (0, 0)


def test_demo_status_reports_loaded_at_and_age_then_clears(client):
    """GET /api/backup/demo returns a loaded_at timestamp + a non-negative integer
    age_days after a load, and both go back to null once the demo is removed."""
    client.post("/api/backup/demo")
    status = client.get("/api/backup/demo").json()
    assert status["has_demo_data"] is True
    assert status["loaded_at"] is not None
    assert isinstance(status["age_days"], int)
    assert status["age_days"] >= 0

    client.delete("/api/backup/demo")
    cleared = client.get("/api/backup/demo").json()
    assert cleared["has_demo_data"] is False
    assert cleared["loaded_at"] is None
    assert cleared["age_days"] is None


def test_demo_status_age_days_reflects_elapsed_time(client, db):
    """age_days is derived from the stored loaded_at, so back-dating the manifest's
    timestamp by ten days reads back as an age of ten whole days."""
    from datetime import UTC, datetime, timedelta

    from app.services import settings_service

    client.post("/api/backup/demo")
    db.rollback()  # see the client's committed manifest (separate WAL snapshot)
    manifest = json.loads(settings_service.get(db, settings_service.DEMO_MANIFEST))
    manifest["loaded_at"] = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    settings_service.set_value(db, settings_service.DEMO_MANIFEST, json.dumps(manifest))

    status = client.get("/api/backup/demo").json()
    assert status["age_days"] == 10


def test_demo_defaults_to_debug_log_level(client):
    """Loading the demo flips logging to DEBUG so there's something to see."""
    client.post("/api/backup/demo")
    assert client.get("/api/settings").json()["log_level"] == "DEBUG"


def test_demo_examples_are_idempotent(client):
    """Re-loading the demo doesn't duplicate the seeded examples."""
    client.post("/api/backup/demo")
    rules = len(client.get("/api/rules").json())
    projects = len(client.get("/api/projects").json())
    users = len(client.get("/api/users").json())
    accounts = len(client.get("/api/savings/summary").json()["accounts"])
    assets = len(client.get("/api/assets").json())
    investments = len(client.get("/api/investments/accounts").json())

    client.post("/api/backup/demo")
    assert len(client.get("/api/rules").json()) == rules
    assert len(client.get("/api/projects").json()) == projects
    assert len(client.get("/api/users").json()) == users
    assert len(client.get("/api/savings/summary").json()["accounts"]) == accounts
    assert len(client.get("/api/assets").json()) == assets
    assert len(client.get("/api/investments/accounts").json()) == investments


def test_demo_remove_returns_clean_db(client):
    """Removing demo data deletes everything the load seeded, leaving a clean DB
    (default category library + the owner survive)."""
    client.post("/api/backup/demo")
    assert client.get("/api/transactions").json()["total"] > 0
    assert client.get("/api/backup/demo").json()["has_demo_data"] is True
    # The demo's recurring rows (Netflix/Spotify/…) get auto-detected as subscriptions.
    assert len(client.get("/api/subscriptions").json()) > 0

    body = client.delete("/api/backup/demo").json()
    assert body["removed"] is True
    assert body["counts"]["transactions"] > 0

    assert client.get("/api/transactions").json()["total"] == 0
    assert client.get("/api/rules").json() == []
    assert client.get("/api/projects").json() == []
    assert client.get("/api/budgets").json() == []
    assert client.get("/api/savings/summary").json()["accounts"] == []
    assert client.get("/api/assets").json() == []
    assert client.get("/api/investments/accounts").json() == []
    assert client.get("/api/vendors").json() == []
    assert client.get("/api/subscriptions").json() == []  # subscriptions cleared too (bug fix)
    assert client.get("/api/review").json() == []
    roles = {u["role"] for u in client.get("/api/users").json()}
    assert "member" not in roles and "child" not in roles
    # The default category library is not demo data — it stays.
    assert len(client.get("/api/categories").json()) == 23
    # Logging is reset from the demo's DEBUG default.
    assert client.get("/api/settings").json()["log_level"] != "DEBUG"
    # Nothing left to remove.
    assert client.get("/api/backup/demo").json()["has_demo_data"] is False


def test_demo_remove_is_idempotent(client):
    """A second remove (or a remove before any load) is a harmless no-op."""
    client.post("/api/backup/demo")
    client.delete("/api/backup/demo")
    second = client.delete("/api/backup/demo").json()
    assert second["removed"] is False
    assert second["counts"] == {}


def test_demo_remove_preserves_user_data(db):
    """Anything the user created — even sharing a demo name — survives removal: the
    manifest only records the rows the load itself created (a before/after diff)."""
    from sqlalchemy import select

    from app.models import Budget
    from app.services import demo_service
    from app.services.household_service import get_or_create_default_household

    household = get_or_create_default_household(db)
    mine = Budget(household_id=household.id, name="Groceries", period="monthly", amount=Decimal("99.00"))
    db.add(mine)
    db.commit()
    mine_id = mine.id

    demo_service.load_demo(db)  # reuses the existing "Groceries" budget, doesn't re-create it
    result = demo_service.remove_demo(db)
    assert result["removed"] is True

    surviving = db.get(Budget, mine_id)
    assert surviving is not None and surviving.amount == Decimal("99.00")
    # Only the user's own budget remains; the demo's budgets are gone.
    assert len(db.scalars(select(Budget.id)).all()) == 1


def test_demo_reload_on_a_later_day_does_not_duplicate(db, monkeypatch):
    """Re-loading the demo on a LATER day refreshes it (remove-then-reload) instead
    of importing a whole second dataset that stacks in the manifest. The dataset is
    generated relative to today, so we fake ``date.today()`` to two different days."""
    from datetime import date as _date

    from sqlalchemy import func, select

    from app.models import Transaction
    from app.services import demo_service

    class _Day1(_date):
        @classmethod
        def today(cls):
            return _date(2026, 3, 1)

    class _Day30(_date):
        @classmethod
        def today(cls):
            return _date(2026, 3, 31)

    monkeypatch.setattr(demo_service, "date", _Day1)
    demo_service.load_demo(db)
    first_total = db.scalar(select(func.count()).select_from(Transaction))
    assert first_total > 0

    # A later day → shifted dates would import an entirely new dataset before the fix.
    monkeypatch.setattr(demo_service, "date", _Day30)
    demo_service.load_demo(db)
    second_total = db.scalar(select(func.count()).select_from(Transaction))
    assert second_total == first_total  # refreshed in place, not doubled


def test_demo_remove_reverts_account_claim_and_restores_log_level(db):
    """On removal the demo undoes the owner it claimed on a KEPT account (a real
    import also used it) and restores the log level the user had before loading —
    neither should leak the demo's state into the user's real config."""
    from app.models import Account, Statement, Transaction, User
    from app.services import demo_service, settings_service
    from app.services.household_service import get_or_create_default_household

    household = get_or_create_default_household(db)
    owner = User(
        household_id=household.id,
        external_id="owner-1",
        display_name="Owner",
        role="owner",
        status="approved",
        is_active=True,
    )
    db.add(owner)
    # A log level the user chose *before* ever loading the demo.
    settings_service.set_value(db, settings_service.LOG_LEVEL, "WARNING")
    db.commit()
    owner_id = owner.id

    demo_service.load_demo(db)
    # During the demo, logging is flipped to DEBUG (intended), and the main account
    # is claimed for the owner.
    assert settings_service.get(db, settings_service.LOG_LEVEL) == "DEBUG"
    manifest = json.loads(settings_service.get(db, settings_service.DEMO_MANIFEST))
    claimed_id = manifest["claimed_account"]
    assert db.get(Account, claimed_id).owner_user_id == owner_id

    # A real (non-demo) transaction on the claimed account so removal KEEPS the
    # account (its owner claim must be reverted, not the account deleted).
    real_stmt = Statement(account_id=claimed_id, source_filename="real.csv", status="imported")
    db.add(real_stmt)
    db.flush()
    db.add(
        Transaction(
            household_id=household.id,
            account_id=claimed_id,
            statement_id=real_stmt.id,
            transaction_date=date(2026, 3, 15),
            description_raw="REAL SPEND",
            amount=Decimal("-5.00"),
            direction="debit",
            currency="GBP",
        )
    )
    db.commit()

    demo_service.remove_demo(db)

    kept = db.get(Account, claimed_id)
    assert kept is not None  # survived (real txn still references it)
    assert kept.owner_user_id is None  # the demo's owner claim was reverted
    # The user's pre-demo log level is restored (not clobbered to the default).
    assert settings_service.get(db, settings_service.LOG_LEVEL) == "WARNING"


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


def test_restore_leaves_original_db_intact_on_validation_failure(tmp_path, monkeypatch):
    """A restore that fails validation must not touch the live database, and must
    not leave a stray ``.bak``/staging file behind."""
    import sqlite3

    import pytest

    from app.config import settings as app_settings
    from app.services import backup_service

    live = tmp_path / "live.db"
    monkeypatch.setattr(app_settings, "database_path", str(live))
    con = sqlite3.connect(str(live))
    con.execute("CREATE TABLE keep_me (id INTEGER PRIMARY KEY, note TEXT)")
    con.execute("INSERT INTO keep_me (note) VALUES ('original')")
    con.commit()
    con.close()
    original = live.read_bytes()

    # A valid SQLite file that is NOT a HA Finance DB (missing required tables).
    candidate = tmp_path / "candidate.db"
    c2 = sqlite3.connect(str(candidate))
    c2.execute("CREATE TABLE something_else (x INTEGER)")
    c2.commit()
    c2.close()

    candidate_bytes = candidate.read_bytes()
    with pytest.raises(backup_service.RestoreError):
        backup_service.restore_database(candidate_bytes)

    # Live DB is byte-for-byte unchanged; nothing was staged or backed up.
    assert live.read_bytes() == original
    assert not live.with_name(live.name + ".bak").exists()
    assert not live.with_name(live.name + ".restore-tmp").exists()


def test_restore_atomic_swap_replaces_db(tmp_path, monkeypatch):
    """A valid restore swaps the file in atomically and keeps a ``.bak`` safety
    copy of the previous database."""
    import sqlite3

    from app.config import settings as app_settings
    from app.services import backup_service

    def _make_hafi_db(path, marker):
        con = sqlite3.connect(str(path))
        for name in ("transactions", "categories", "statements", "accounts"):
            con.execute(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY, note TEXT)")
        con.execute("INSERT INTO transactions (note) VALUES (?)", (marker,))
        con.commit()
        con.close()

    live = tmp_path / "live.db"
    monkeypatch.setattr(app_settings, "database_path", str(live))
    _make_hafi_db(live, "old")

    incoming = tmp_path / "incoming.db"
    _make_hafi_db(incoming, "new")

    backup_service.restore_database(incoming.read_bytes())

    con = sqlite3.connect(str(live))
    note = con.execute("SELECT note FROM transactions").fetchone()[0]
    con.close()
    assert note == "new"
    assert live.with_name(live.name + ".bak").exists()  # previous DB preserved
    assert not live.with_name(live.name + ".restore-tmp").exists()


def test_config_export_and_import(client):
    export = client.get("/api/backup/config").json()
    assert len(export["categories"]) == 23  # seeded library
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
        "settings": [{"key": "fx_mode", "value": "frankfurter"}],  # allowlisted + valid
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
    assert client.get("/api/settings").json()["fx_mode"] == "frankfurter"  # applied


def test_config_import_only_allowlisted_settings(client):
    """CR-SEC-2: a config import may set only allowlisted, validated settings — it
    must never flip privacy_mode to a cloud mode, point AI/Paperless at an internal
    host (SSRF), change the base currency, or write arbitrary/invalid keys (it
    bypassed every PUT /api/settings validator before)."""
    cfg = {
        "settings": [
            {"key": "fx_mode", "value": "frankfurter"},                      # allowlisted + valid → applied
            {"key": "privacy_mode", "value": "cloud_auto"},                  # cloud flip → rejected
            {"key": "ai_base_url", "value": "http://attacker.example/v1"},   # SSRF vector → rejected
            {"key": "paperless_url", "value": "http://internal.example:8000"},  # SSRF vector → rejected
            {"key": "base_currency", "value": "USD"},                        # recompute side-effect → rejected
            {"key": "investment_price_source", "value": "not-a-source"},     # allowlisted but invalid → rejected
            {"key": "totally_unknown_key", "value": "x"},                    # unknown → rejected
        ],
    }
    res = client.post(
        "/api/backup/config",
        files={"file": ("config.json", json.dumps(cfg).encode(), "application/json")},
    ).json()
    assert res["settings_set"] == 1  # only fx_mode
    assert {"privacy_mode", "ai_base_url", "paperless_url", "base_currency",
            "investment_price_source", "totally_unknown_key"} <= set(res["skipped_setting_keys"])

    s = client.get("/api/settings").json()
    assert s["fx_mode"] == "frankfurter"        # the safe one was applied
    assert s["privacy_mode"] == "strict_local"  # NOT flipped to cloud
    assert s["ai_base_url"] == ""               # NOT pointed at the internal host
    assert s["paperless_url"] == ""             # NOT set
    assert s["base_currency"] == "GBP"          # unchanged


# --- Config export/import v0.2: vendor default category + rules (#562) ---
#
# These build a library directly in an empty DB (the ``db`` fixture) and drive the
# service functions. The suite resets the schema per test rather than offering a
# second independent database, so the round-trip test uses the sanctioned
# alternative: export, drop the referential rows, re-import into the same DB. New
# rows get fresh ids, so faithful reconstruction proves the name<->id translation
# runs correctly in both directions.

def _seed_config_library(db):
    """Create a small category/vendor/project/rule library and return the rows.

    Covers every referential rule shape (``set_category``/``set_vendor``/
    ``set_project`` actions and ``vendor_equals``/``category_equals`` conditions)
    plus a rule that carries a purely literal action value.
    """
    from app.models import Category, Project, Rule, Vendor

    groceries = Category(name="Groceries", path="Groceries")
    utilities = Category(name="Utilities", path="Utilities")
    db.add_all([groceries, utilities])
    db.flush()

    tesco = Vendor(canonical_name="Tesco", default_category_id=groceries.id)
    octopus = Vendor(canonical_name="Octopus Energy")  # deliberately no default category
    db.add_all([tesco, octopus])
    db.flush()

    reno = Project(name="Kitchen Reno")
    db.add(reno)
    db.flush()

    db.add_all([
        Rule(name="Tesco -> Groceries", condition_type="merchant_contains",
             condition_value="TESCO", action_type="set_category",
             action_value=str(groceries.id)),
        Rule(name="Octopus vendor match", condition_type="vendor_equals",
             condition_value=str(octopus.id), action_type="set_country",
             action_value="GB"),
        Rule(name="Utilities -> Reno", condition_type="category_equals",
             condition_value=str(utilities.id), action_type="set_project",
             action_value=str(reno.id)),
        Rule(name="Text -> Octopus", condition_type="description_contains",
             condition_value="OCTO", action_type="set_vendor",
             action_value=str(octopus.id)),
    ])
    db.flush()
    return {"groceries": groceries, "utilities": utilities,
            "tesco": tesco, "octopus": octopus, "reno": reno}


def test_config_export_includes_vendor_default_category(db):
    """A vendor's default category is exported as the category NAME (or null)."""
    from app.services import backup_service

    _seed_config_library(db)
    export = backup_service.export_config(db)

    assert export["version"] == "0.2"
    by_name = {v["canonical_name"]: v for v in export["vendors"]}
    assert by_name["Tesco"]["default_category"] == "Groceries"
    assert by_name["Octopus Energy"]["default_category"] is None


def test_config_export_rules_use_portable_names(db):
    """Referential rule values export as names, never local integer ids; literal
    values pass through unchanged."""
    from app.services import backup_service

    _seed_config_library(db)
    export = backup_service.export_config(db)

    rules = {r["name"]: r for r in export["rules"]}
    # set_category action -> category name (not a numeric id)
    assert rules["Tesco -> Groceries"]["action_value"] == "Groceries"
    assert not rules["Tesco -> Groceries"]["action_value"].isdigit()
    # vendor_equals condition + set_vendor action -> vendor canonical name
    assert rules["Octopus vendor match"]["condition_value"] == "Octopus Energy"
    assert rules["Text -> Octopus"]["action_value"] == "Octopus Energy"
    # category_equals condition + set_project action -> names
    assert rules["Utilities -> Reno"]["condition_value"] == "Utilities"
    assert rules["Utilities -> Reno"]["action_value"] == "Kitchen Reno"
    # A literal action value (set_country) is carried through untouched.
    assert rules["Octopus vendor match"]["action_value"] == "GB"


def test_config_round_trip_reconstructs_local_fks(db):
    """Export, drop the referential rows, re-import: the vendor default category
    and every rule reference resolve to the correct NEW local ids."""
    from sqlalchemy import select

    from app.models import Category, Project, Rule, Vendor
    from app.services import backup_service

    _seed_config_library(db)
    export = backup_service.export_config(db)

    # Simulate importing onto another instance: delete rules + vendors (keep the
    # categories and project), so re-inserted rows get brand-new ids.
    for rule in db.scalars(select(Rule)).all():
        db.delete(rule)
    for vendor in db.scalars(select(Vendor)).all():
        db.delete(vendor)
    db.commit()

    result = backup_service.import_config(db, export)
    assert result["categories_added"] == 0  # already present
    assert result["vendors_added"] == 2
    assert result["rules_added"] == 4
    assert result["rules_skipped"] == 0
    assert result["skipped_rule_names"] == []

    cats = {c.name: c.id for c in db.scalars(select(Category)).all()}
    vendors = {v.canonical_name: v for v in db.scalars(select(Vendor)).all()}
    reno_id = db.scalars(select(Project.id).where(Project.name == "Kitchen Reno")).one()

    assert vendors["Tesco"].default_category_id == cats["Groceries"]
    assert vendors["Octopus Energy"].default_category_id is None

    rules = {r.name: r for r in db.scalars(select(Rule)).all()}
    assert rules["Tesco -> Groceries"].action_value == str(cats["Groceries"])
    assert rules["Utilities -> Reno"].condition_value == str(cats["Utilities"])
    assert rules["Utilities -> Reno"].action_value == str(reno_id)
    assert rules["Octopus vendor match"].condition_value == str(vendors["Octopus Energy"].id)
    assert rules["Text -> Octopus"].action_value == str(vendors["Octopus Energy"].id)
    # Literal value survives the round trip; imported rules are tagged as such.
    assert rules["Octopus vendor match"].action_value == "GB"
    assert all(r.created_from == "import" for r in rules.values())


def test_config_import_skips_rules_with_unresolvable_refs(db):
    """A rule referencing a category/vendor/project name absent locally is skipped
    and reported; no rule row with a dangling FK is written."""
    from sqlalchemy import select

    from app.models import Rule
    from app.services import backup_service

    doc = {
        "version": "0.2",
        "categories": [{"name": "Groceries"}],
        "vendors": [],
        "rules": [
            {"name": "good", "condition_type": "merchant_contains",
             "condition_value": "X", "action_type": "set_category",
             "action_value": "Groceries"},
            {"name": "bad-category", "condition_type": "merchant_contains",
             "condition_value": "Y", "action_type": "set_category",
             "action_value": "Nonexistent"},
            {"name": "bad-vendor", "condition_type": "vendor_equals",
             "condition_value": "Ghost Vendor", "action_type": "set_country",
             "action_value": "GB"},
            {"name": "bad-project", "condition_type": "merchant_contains",
             "condition_value": "Z", "action_type": "set_project",
             "action_value": "Ghost Project"},
        ],
    }

    result = backup_service.import_config(db, doc)
    assert result["rules_added"] == 1
    assert result["rules_skipped"] == 3
    assert result["skipped_rule_names"] == ["bad-category", "bad-project", "bad-vendor"]

    names = {r.name for r in db.scalars(select(Rule)).all()}
    assert names == {"good"}
    # The rule that WAS written points at a real local category id.
    good = db.scalars(select(Rule).where(Rule.name == "good")).one()
    assert good.action_value.isdigit()


def test_config_import_accepts_v0_1_document(db):
    """A legacy v0.1 export (no ``rules`` key, vendors without ``default_category``)
    still imports cleanly and leaves the new FK NULL."""
    from sqlalchemy import select

    from app.models import Vendor
    from app.services import backup_service

    doc = {
        "version": "0.1",
        "categories": [{"name": "Legacy Cat"}],
        "vendors": [{"canonical_name": "Legacy Vendor",
                     "aliases": [{"alias": "LEG", "match_type": "contains"}]}],
        "settings": [],
    }

    result = backup_service.import_config(db, doc)
    assert result["categories_added"] == 1
    assert result["vendors_added"] == 1
    assert result["rules_added"] == 0
    assert result["rules_skipped"] == 0
    assert result["skipped_rule_names"] == []

    vendor = db.scalars(
        select(Vendor).where(Vendor.canonical_name == "Legacy Vendor")
    ).one()
    assert vendor.default_category_id is None
