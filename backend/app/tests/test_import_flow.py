"""Integration tests for the import + transactions flow (spec §32.2).

Covers the Stage 1 acceptance criteria: upload a Curve CSV, transactions appear,
a duplicate upload does not duplicate transactions, and an import report is
returned.
"""

from __future__ import annotations


def _upload(client, samples_dir, filename: str, **form):
    content = (samples_dir / filename).read_bytes()
    return client.post(
        "/api/imports/upload",
        files={"file": (filename, content, "text/csv")},
        data=form,
    )


def test_list_parsers(client):
    res = client.get("/api/imports/parsers")
    assert res.status_code == 200
    ids = {p["parser_id"] for p in res.json()}
    assert {"curve_csv", "barclays_csv", "lloyds_csv", "monzo_csv", "generic_csv"} <= ids


def test_upload_preview_confirm_curve(client, samples_dir):
    res = _upload(client, samples_dir, "curve-sample.csv")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["detected_parser"] == "curve_csv"
    assert body["rows_detected"] == 8
    assert body["report"] == {"rows_detected": 8, "new": 8, "duplicates": 0, "errors": 0}
    assert len(body["preview"]) == 8
    assert body["preview"][0]["is_duplicate"] is False

    import_id = body["import_id"]
    confirm = client.post(f"/api/imports/{import_id}/confirm")
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["status"] == "imported"
    assert confirm.json()["report"]["new"] == 8

    txns = client.get("/api/transactions").json()
    assert txns["total"] == 8
    # Most recent first; salary is a credit.
    assert any(t["direction"] == "credit" for t in txns["items"])


def test_duplicate_upload_does_not_duplicate(client, samples_dir):
    # First import
    first = _upload(client, samples_dir, "curve-sample.csv").json()
    client.post(f"/api/imports/{first['import_id']}/confirm")

    # Second import of the same file
    second = _upload(client, samples_dir, "curve-sample.csv")
    body = second.json()
    assert body["report"]["duplicates"] == 8
    assert body["report"]["new"] == 0
    assert any("already imported" in w for w in body["warnings"])

    confirm = client.post(f"/api/imports/{body['import_id']}/confirm").json()
    assert confirm["report"]["duplicates"] == 8
    assert confirm["report"]["new"] == 0

    assert client.get("/api/transactions").json()["total"] == 8


def test_generic_upload(client, samples_dir):
    body = _upload(client, samples_dir, "generic-sample.csv").json()
    assert body["detected_parser"] == "generic_csv"
    client.post(f"/api/imports/{body['import_id']}/confirm")
    txns = client.get("/api/transactions").json()
    assert txns["total"] == 5
    # Refund row is a credit (money in).
    assert sum(1 for t in txns["items"] if t["direction"] == "credit") == 1


def test_transaction_filters_and_patch(client, samples_dir):
    body = _upload(client, samples_dir, "curve-sample.csv").json()
    client.post(f"/api/imports/{body['import_id']}/confirm")

    # Search filter
    res = client.get("/api/transactions", params={"search": "TESCO"}).json()
    assert res["total"] == 1
    txn_id = res["items"][0]["id"]

    # Patch: mark as transfer
    patched = client.patch(f"/api/transactions/{txn_id}", json={"is_transfer": True})
    assert patched.status_code == 200
    assert patched.json()["is_transfer"] is True

    # Amount filter (debits only)
    debits = client.get("/api/transactions", params={"amount_max": "0"}).json()
    assert debits["total"] == 7  # 8 rows, 1 is the salary credit


def test_delete_pending_import(client, samples_dir):
    body = _upload(client, samples_dir, "monzo-sample.csv").json()
    import_id = body["import_id"]
    assert client.delete(f"/api/imports/{import_id}").status_code == 204
    assert client.get(f"/api/imports/{import_id}").status_code == 404


def _std_txn(desc: str, amount: str = "-10.00"):
    from datetime import date
    from decimal import Decimal

    from app.parsers.base import StandardTransaction

    return StandardTransaction(
        transaction_date=date(2024, 1, 2),
        amount=Decimal(amount),
        currency="GBP",
        description_raw=desc,
    )


def test_already_imported_file_reports_all_duplicates():
    # SR-A1 §1: when the file's content hash matches a prior import, its rows must
    # never be reported as brand-new — even if per-row hashing wouldn't line them
    # up (e.g. a different resolved account). _build_preview must force duplicates.
    from app.services import import_service as imp

    parsed = [_std_txn("TESCO"), _std_txn("SPOTIFY", "-9.99")]
    new_count, dup_count, preview = imp._build_preview(
        parsed, account_id=1, existing_hashes=set(), cross={}, preview_limit=20,
        file_already_imported=True,
    )
    assert new_count == 0
    assert dup_count == 2
    assert all(row["is_duplicate"] for row in preview)
    assert preview[0]["dup_reason"] == "File already imported"

    # Sanity: without the flag the same rows are counted new (regression guard).
    new_only, _, _ = imp._build_preview(
        parsed, account_id=1, existing_hashes=set(), cross={}, preview_limit=20,
    )
    assert new_only == 2


def test_existing_hashes_batched_only_returns_intersection(client, samples_dir):
    # SR-A1 §3: _existing_hashes_for looks up only the hashes present in this batch
    # (a single IN query) and returns just those that already exist for the account.
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models import Account
    from app.services import import_service as imp

    first = _upload(client, samples_dir, "curve-sample.csv").json()
    client.post(f"/api/imports/{first['import_id']}/confirm")

    db = SessionLocal()
    try:
        account_id = db.scalars(select(Account.id).order_by(Account.id)).first()
        content = (samples_dir / "curve-sample.csv").read_bytes()
        parsed = imp._resolve_parser("curve_csv", "curve-sample.csv", content, None).parse(
            "curve-sample.csv", content
        )

        found = imp._existing_hashes_for(db, account_id, parsed)
        # Every re-parsed row already exists → exactly those 8 hashes returned.
        assert found == {imp.source_hash(account_id, t) for t in parsed}
        assert len(found) == 8

        # A brand-new row is not in the batch intersection.
        assert imp._existing_hashes_for(db, account_id, [_std_txn("NEVER SEEN", "-1.00")]) == set()
    finally:
        db.close()


def test_preloaded_categorise_context_loads_and_applies(client, samples_dir):
    # SR-A1 §2: the confirm hot loop categorises via a once-loaded snapshot
    # (rules + vendor aliases). The snapshot loads, and the preloaded path applies
    # a vendor alias the same way the live path would.
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models import Account, Category, Household, Vendor, VendorAlias
    from app.services import category_service
    from app.services import import_service as imp

    body = _upload(client, samples_dir, "curve-sample.csv").json()
    client.post(f"/api/imports/{body['import_id']}/confirm")

    db = SessionLocal()
    try:
        category_service.ensure_default_categories(db)
        household_id = db.scalars(select(Household.id).order_by(Household.id)).first()
        account_id = db.scalars(select(Account.id).order_by(Account.id)).first()
        category_id = db.scalars(select(Category.id).order_by(Category.id)).first()

        # Seed a vendor + alias with a default category so the preloaded path has
        # something to match and apply.
        vendor = Vendor(household_id=household_id, canonical_name="ACME", display_name="ACME",
                        default_category_id=category_id)
        db.add(vendor)
        db.flush()
        db.add(VendorAlias(vendor_id=vendor.id, alias="ACME", match_type="contains", source="user"))
        db.flush()

        ctx = imp._load_categoriser_context(db)
        assert any(a.alias == "ACME" for a, _v in ctx.alias_pairs)

        row = imp._to_transaction(
            _std_txn("ACME CORP 99"), household_id, account_id, None,
            imp.source_hash(account_id, _std_txn("ACME CORP 99")),
        )
        db.add(row)
        db.flush()
        assert imp.auto_categorise(db, row, ctx) is True
        assert row.merchant_id == vendor.id
        assert row.category_id == category_id
    finally:
        db.close()


def test_preloaded_default_category_uses_linked_vendor(db):
    """#7 (preloaded path): _normalise_preloaded applies the LINKED vendor's default
    category, not the freshly matched vendor's, when a rule already linked another
    vendor — parity with the live normalise_transaction fix."""
    from datetime import date
    from decimal import Decimal

    from app.models import Transaction
    from app.services import category_service, vendor_service
    from app.services import import_service as imp

    cat_a = category_service.create_category(db, {"name": "PreCatA"})
    cat_b = category_service.create_category(db, {"name": "PreCatB"})
    vendor_service.create_vendor(
        db, {"canonical_name": "PreA", "alias": "BETA", "match_type": "contains",
             "default_category_id": cat_a.id},
    )
    vendor_b = vendor_service.create_vendor(
        db, {"canonical_name": "PreB", "default_category_id": cat_b.id},
    )
    txn = Transaction(
        description_raw="BETA SHOP 7", amount=Decimal("-2.00"), direction="debit",
        currency="GBP", transaction_date=date(2026, 5, 15), merchant_id=vendor_b.id,
    )
    db.add(txn)
    db.flush()

    ctx = imp._load_categoriser_context(db)
    imp._normalise_preloaded(db, txn, ctx.alias_pairs)
    assert txn.merchant_id == vendor_b.id   # linked vendor preserved
    assert txn.category_id == cat_b.id      # B's default, not A's (#7)


def test_recategorise_loads_context_once(db, monkeypatch):
    """#15: recategorise builds the categoriser context once and threads it through
    every row, rather than re-querying rules + aliases per transaction (N+1)."""
    from datetime import date
    from decimal import Decimal

    from sqlalchemy import select

    from app.models import Account, Transaction
    from app.services import category_service, vendor_service
    from app.services import import_service as imp

    category_service.import_library(db)
    groceries = next(c for c in category_service.list_categories(db) if c.name == "Groceries")
    vendor_service.create_vendor(
        db, {"canonical_name": "Tesco", "alias": "TESCO", "match_type": "contains",
             "default_category_id": groceries.id},
    )
    acct = Account(name="A", account_type="current_account", currency="GBP")
    db.add(acct)
    db.flush()
    for i in range(5):
        db.add(Transaction(
            account_id=acct.id, transaction_date=date(2026, 5, 15),
            description_raw=f"TESCO STORES {i}", amount=Decimal("-1.00"),
            currency="GBP", direction="debit",
        ))
    db.commit()

    calls = {"n": 0}
    real = imp._load_categoriser_context

    def _counting(db_arg):
        calls["n"] += 1
        return real(db_arg)

    monkeypatch.setattr(imp, "_load_categoriser_context", _counting)

    result = imp.recategorise(db, only_uncategorised=True)
    assert result["changed"] == 5   # every row newly categorised
    assert calls["n"] == 1     # context built exactly once, not per row (N+1 -> 1)
    rows = db.scalars(select(Transaction).where(Transaction.account_id == acct.id)).all()
    assert all(r.category_id == groceries.id for r in rows)


def test_recategorise_reapplies_rules_over_existing_and_dry_run(db):
    """only_uncategorised=False re-applies rules to already-categorised rows: a
    rule overrides a low-confidence keyword category ("Cash" 0.70) but never a
    manual one (1.0). dry_run reports the count and persists nothing; conditions
    scope the pass to a filtered subset."""
    from datetime import date
    from decimal import Decimal

    from app.models import Account, Transaction
    from app.services import category_service, rule_service
    from app.services import import_service as imp

    category_service.import_library(db)
    cash = next(c for c in category_service.list_categories(db) if c.name == "Cash")
    groceries = next(c for c in category_service.list_categories(db) if c.name == "Groceries")

    acct = Account(name="A", account_type="current_account", currency="GBP")
    db.add(acct)
    db.flush()
    # Two rows already sitting in "Cash": one keyword-assigned (0.70), one manual (1.0).
    kw = Transaction(
        account_id=acct.id, transaction_date=date(2026, 5, 1), description_raw="TESCO STORES 1",
        amount=Decimal("-5.00"), currency="GBP", direction="debit",
        category_id=cash.id, confidence_score=0.70,
    )
    manual = Transaction(
        account_id=acct.id, transaction_date=date(2026, 5, 2), description_raw="TESCO STORES 2",
        amount=Decimal("-6.00"), currency="GBP", direction="debit",
        category_id=cash.id, confidence_score=1.0,
    )
    db.add_all([kw, manual])
    db.commit()

    # The user just added a rule: description contains TESCO -> Groceries.
    rule_service.create_rule(db, {
        "condition_type": "description_contains", "condition_value": "TESCO",
        "action_type": "set_category", "action_value": str(groceries.id), "priority": 100,
    })

    # Dry-run over all rows: reports the one override (the keyword row), changes nothing.
    preview = imp.recategorise(db, only_uncategorised=False, dry_run=True)
    assert preview == {"changed": 1, "considered": 2}
    db.refresh(kw)
    db.refresh(manual)
    assert kw.category_id == cash.id and manual.category_id == cash.id

    # only_uncategorised=True (the default) leaves both alone — they already have a category.
    assert imp.recategorise(db, only_uncategorised=True)["changed"] == 0
    db.refresh(kw)
    assert kw.category_id == cash.id

    # Conditions scope the pass: restricting to the manual row alone changes nothing.
    scoped = imp.recategorise(db, conditions=[Transaction.id == manual.id], only_uncategorised=False)
    assert scoped == {"changed": 0, "considered": 1}

    # Apply for real over all rows: the keyword "Cash" moves to Groceries; the
    # manual pick is preserved.
    result = imp.recategorise(db, only_uncategorised=False)
    assert result == {"changed": 1, "considered": 2}
    db.refresh(kw)
    db.refresh(manual)
    assert kw.category_id == groceries.id
    assert manual.category_id == cash.id


def test_statement_config_tolerates_malformed_notes():
    # confirm/delete read the parser config from statement.notes; a non-JSON or
    # non-dict value must degrade to {} rather than crash (SR-A1).
    from types import SimpleNamespace

    from app.services import import_service as imp

    assert imp._statement_config(SimpleNamespace(notes=None)) == {}
    assert imp._statement_config(SimpleNamespace(notes="")) == {}
    assert imp._statement_config(SimpleNamespace(notes="legacy free text")) == {}
    assert imp._statement_config(SimpleNamespace(notes='["a", "list"]')) == {}  # JSON, not a dict
    assert imp._statement_config(SimpleNamespace(notes='{"parser_id": "curve_csv"}')) == {
        "parser_id": "curve_csv"
    }
