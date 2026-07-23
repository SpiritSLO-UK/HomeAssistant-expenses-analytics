"""Rule engine tests (spec §29 Stage 3, §12.11, §36, §15.1)."""

from __future__ import annotations


def _curve(rows: list[tuple[str, str, str]]) -> bytes:
    head = "Date,Description,Amount,Currency,Card,Category\n"
    body = "".join(f"{d},{desc},{amt},GBP,Visa,\n" for d, desc, amt in rows)
    return (head + body).encode()


def _import(client, content: bytes, name: str = "rules.csv"):
    up = client.post(
        "/api/imports/upload",
        files={"file": (name, content, "text/csv")},
        data={"parser_id": "curve_csv"},
    ).json()
    return client.post(f"/api/imports/{up['import_id']}/confirm").json()


def _cat(client, name: str) -> int:
    return next(c["id"] for c in client.get("/api/categories").json() if c["name"] == name)


def _by_desc(client):
    return {t["description_raw"]: t for t in client.get("/api/transactions").json()["items"]}


# --- CRUD + validation ---

def test_rule_crud_and_validation(client):
    groceries = _cat(client, "Groceries")
    created = client.post(
        "/api/rules",
        json={
            "condition_type": "description_contains",
            "condition_value": "ZZQ",
            "action_type": "set_category",
            "action_value": str(groceries),
        },
    )
    assert created.status_code == 201
    rid = created.json()["id"]

    assert client.patch(f"/api/rules/{rid}", json={"enabled": False}).json()["enabled"] is False
    assert any(r["id"] == rid for r in client.get("/api/rules").json())

    bad = client.post(
        "/api/rules",
        json={"condition_type": "nope", "condition_value": "x", "action_type": "set_category"},
    )
    assert bad.status_code == 400

    assert client.delete(f"/api/rules/{rid}").status_code == 204


# --- engine behaviour ---

def test_rule_sets_category_on_import(client):
    groceries = _cat(client, "Groceries")
    client.post(
        "/api/rules",
        json={
            "condition_type": "description_contains",
            "condition_value": "ZZQ",
            "action_type": "set_category",
            "action_value": str(groceries),
        },
    )
    # "ZZQ MARKET" matches no keyword, so only the rule can categorise it.
    _import(client, _curve([("2026-05-04", "ZZQ MARKET", "-12.00")]))
    assert _by_desc(client)["ZZQ MARKET"]["category_id"] == groceries


def test_rule_beats_keyword(client):
    # TESCO would be Groceries by keyword; a rule says Eating Out and must win.
    eating_out = _cat(client, "Eating Out")
    client.post(
        "/api/rules",
        json={
            "condition_type": "description_contains",
            "condition_value": "TESCO",
            "action_type": "set_category",
            "action_value": str(eating_out),
            "priority": 200,
        },
    )
    _import(client, _curve([("2026-05-02", "TESCO STORES 3142", "-42.18")]))
    txn = _by_desc(client)["TESCO STORES 3142"]
    assert txn["category_id"] == eating_out


def test_disabled_rule_is_ignored(client):
    groceries = _cat(client, "Groceries")
    client.post(
        "/api/rules",
        json={
            "condition_type": "description_contains",
            "condition_value": "ZZQ",
            "action_type": "set_category",
            "action_value": str(groceries),
            "enabled": False,
        },
    )
    _import(client, _curve([("2026-05-04", "ZZQ MARKET", "-12.00")]))
    assert _by_desc(client)["ZZQ MARKET"]["category_id"] is None


def test_mark_transfer_rule(client):
    client.post(
        "/api/rules",
        json={
            "condition_type": "description_contains",
            "condition_value": "TRANSFER",
            "action_type": "mark_transfer",
        },
    )
    _import(client, _curve([("2026-05-06", "TRANSFER TO SAVINGS", "-100.00")]))
    assert _by_desc(client)["TRANSFER TO SAVINGS"]["is_transfer"] is True


def test_rule_sets_country_on_import(client):
    # A foreign vendor whose currency would otherwise misattribute the spend:
    # a set_country rule tags it ES so the spend-by-location map credits Spain.
    client.post(
        "/api/rules",
        json={
            "condition_type": "description_contains",
            "condition_value": "MERCADONA",
            "action_type": "set_country",
            "action_value": "es",  # lowercase + trailing junk is normalised
        },
    )
    _import(client, _curve([("2026-05-04", "MERCADONA MADRID", "-45.00")]))
    assert _by_desc(client)["MERCADONA MADRID"]["country"] == "ES"


def test_create_rule_from_correction(client):
    # Import an unknown merchant -> uncategorised.
    _import(client, _curve([("2026-05-04", "ZZQ MARKET", "-12.00")]), name="a.csv")
    txn = _by_desc(client)["ZZQ MARKET"]
    assert txn["category_id"] is None
    groceries = _cat(client, "Groceries")

    # Correct it AND learn a rule.
    client.post(
        f"/api/transactions/{txn['id']}/categorise",
        json={"category_id": groceries, "learn_rule": True},
    )
    assert any(r["created_from"] == "manual_correction" for r in client.get("/api/rules").json())

    # A future import of a similar ZZQ merchant is auto-categorised by the rule.
    _import(client, _curve([("2026-06-04", "ZZQ DEPOT", "-20.00")]), name="b.csv")
    assert _by_desc(client)["ZZQ DEPOT"]["category_id"] == groceries


def test_noop_rule_does_not_block_lower_priority(client):
    # A higher-priority set_category rule with a non-numeric action_value is a
    # no-op (it can't set a category). It must NOT consume the set_category slot,
    # so a valid lower-priority set_category rule still applies (SR-A4).
    groceries = _cat(client, "Groceries")
    client.post(
        "/api/rules",
        json={
            "condition_type": "description_contains",
            "condition_value": "ZZQ",
            "action_type": "set_category",
            "action_value": "",  # no-op: not a category id
            "priority": 300,
        },
    )
    client.post(
        "/api/rules",
        json={
            "condition_type": "description_contains",
            "condition_value": "ZZQ",
            "action_type": "set_category",
            "action_value": str(groceries),
            "priority": 100,
        },
    )
    _import(client, _curve([("2026-05-04", "ZZQ MARKET", "-12.00")]))
    assert _by_desc(client)["ZZQ MARKET"]["category_id"] == groceries


def test_mark_subscription_rule(client):
    # A mark_subscription rule records the matched transaction as a (possible)
    # subscription via the existing subscription mechanism (SR-A4 wiring).
    client.post(
        "/api/rules",
        json={
            "condition_type": "description_contains",
            "condition_value": "NETFLIX",
            "action_type": "mark_subscription",
        },
    )
    _import(client, _curve([("2026-05-06", "NETFLIX MONTHLY", "-9.99")]))
    items = client.get("/api/subscriptions").json()
    names = [s["name"] for s in items]
    assert any("NETFLIX" in n.upper() for n in names)


def test_mark_subscription_idempotent(client):
    # Two matching charges must not create two subscriptions for the same name.
    client.post(
        "/api/rules",
        json={
            "condition_type": "description_contains",
            "condition_value": "SPOTIFY",
            "action_type": "mark_subscription",
        },
    )
    _import(client, _curve([("2026-05-06", "SPOTIFY", "-11.99")]), name="s1.csv")
    _import(client, _curve([("2026-06-06", "SPOTIFY", "-11.99")]), name="s2.csv")
    items = client.get("/api/subscriptions").json()
    spotify = [s for s in items if "SPOTIFY" in s["name"].upper()]
    assert len(spotify) == 1


def test_amount_between_validation_bad_input_never_matches():
    # Malformed / out-of-order amount_between values are handled gracefully in the
    # rule engine (never raise, never spuriously match).
    from decimal import Decimal

    from app.models import Rule, Transaction
    from app.services import rule_service

    def _txn(amount: str) -> Transaction:
        return Transaction(description_raw="X", amount=Decimal(amount), direction="debit")

    def _rule(value: str) -> Rule:
        return Rule(condition_type="amount_between", condition_value=value)

    # In-range, well-formed.
    assert rule_service.matches(_rule("10,50"), _txn("-30")) is False  # signed: -30 not in 10..50
    assert rule_service.matches(_rule("10,50"), _txn("30")) is True
    assert rule_service.matches(_rule("-50,-10"), _txn("-30")) is True

    # Out-of-order bounds are auto-swapped, so this still matches.
    assert rule_service.matches(_rule("50,10"), _txn("30")) is True

    # Malformed: wrong part count, empty, unparseable — never match, never raise.
    for bad in ("10", "10,20,30", "", "abc,def", "10,x"):
        assert rule_service.matches(_rule(bad), _txn("15")) is False


def test_amount_bounds_parsing():
    from decimal import Decimal

    from app.services import rule_service

    assert rule_service._amount_bounds("10,50") == (Decimal("10"), Decimal("50"))
    # Pipe separator + out-of-order swap.
    assert rule_service._amount_bounds("50|10") == (Decimal("10"), Decimal("50"))
    # Decimal point preserved.
    assert rule_service._amount_bounds("10.50,20.00") == (Decimal("10.50"), Decimal("20.00"))
    for bad in ("10", "10,20,30", "", "abc,1", "1,abc"):
        assert rule_service._amount_bounds(bad) is None


def test_block_cloud_ai_is_documented_noop():
    # block_cloud_ai stays a no-op (no transaction-level lever without a migration)
    # so it must NOT claim its action slot / report as fired.
    from decimal import Decimal

    from app.models import Rule, Transaction
    from app.services import rule_service

    txn = Transaction(description_raw="X", amount=Decimal("-5"), direction="debit")
    rule = Rule(action_type="block_cloud_ai", action_value=None)
    assert rule_service.apply_action(rule, txn, None) is False


def test_rule_action_ignores_deleted_reference(db):
    """A set_vendor / set_category rule whose target was deleted must no-op instead
    of writing a dangling foreign key — which used to fail on flush and could 500 a
    later import or the demo seed (the pollution behind a real support report)."""
    from datetime import date
    from decimal import Decimal

    from app.models import Account, Category, Transaction, Vendor
    from app.services import rule_service

    acct = Account(name="A", account_type="current_account", currency="GBP")
    cat = Category(name="TempCat")
    vendor = Vendor(canonical_name="TempVendor")
    db.add_all([acct, cat, vendor])
    db.flush()
    cat_id, vendor_id = cat.id, vendor.id

    rule_service.create_rule(db, {"condition_type": "description_contains", "condition_value": "ACME",
                                  "action_type": "set_vendor", "action_value": str(vendor_id), "priority": 100})
    rule_service.create_rule(db, {"condition_type": "description_contains", "condition_value": "ACME",
                                  "action_type": "set_category", "action_value": str(cat_id), "priority": 90})

    # The targets are removed after the rules were made — the rules are now stale.
    db.delete(vendor)
    db.delete(cat)
    db.flush()

    txn = Transaction(account_id=acct.id, transaction_date=date(2026, 5, 1), description_raw="ACME CORP",
                      amount=Decimal("-5"), direction="debit", currency="GBP")
    db.add(txn)
    db.flush()

    fired = rule_service.apply_rules(db, txn)
    assert fired == []              # both stale actions no-op (nothing claims its slot)
    assert txn.merchant_id is None  # no dangling vendor FK written
    assert txn.category_id is None  # no dangling category FK written
    db.flush()                      # raised FOREIGN KEY constraint failed before the fix


def test_rule_test_endpoint(client):
    _import(client, _curve([("2026-05-02", "TESCO STORES 3142", "-42.18"),
                            ("2026-05-03", "COSTA COFFEE", "-3.85")]))
    res = client.post(
        "/api/rules/test", json={"condition_type": "description_contains", "condition_value": "TESCO"}
    ).json()
    assert res["match_count"] == 1
    assert res["total"] == 2
    assert res["sample"][0]["description_raw"] == "TESCO STORES 3142"
