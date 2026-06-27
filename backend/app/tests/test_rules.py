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


def test_rule_test_endpoint(client):
    _import(client, _curve([("2026-05-02", "TESCO STORES 3142", "-42.18"),
                            ("2026-05-03", "COSTA COFFEE", "-3.85")]))
    res = client.post(
        "/api/rules/test", json={"condition_type": "description_contains", "condition_value": "TESCO"}
    ).json()
    assert res["match_count"] == 1
    assert res["total"] == 2
    assert res["sample"][0]["description_raw"] == "TESCO STORES 3142"
