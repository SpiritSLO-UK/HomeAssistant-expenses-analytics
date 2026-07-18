"""Global search across transactions / vendors / categories / projects."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models import Category, Tag, Transaction
from app.services import search_service
from app.services.household_service import get_or_create_default_household


def test_search_matches_transactions_and_vendor(client):
    client.post("/api/backup/demo")
    r = client.get("/api/search", params={"q": "tesco"}).json()
    assert r["query"] == "tesco"
    assert any("TESCO" in t["description"].upper() for t in r["transactions"])
    assert any(v["name"] == "Tesco" for v in r["vendors"])  # demo seeds a Tesco vendor


def test_search_matches_category_and_project(client):
    client.post("/api/backup/demo")
    cats = client.get("/api/search", params={"q": "groceries"}).json()["categories"]
    assert any(c["name"] == "Groceries" for c in cats)
    projects = client.get("/api/search", params={"q": "spain"}).json()["projects"]
    assert any("Spain" in p["name"] for p in projects)  # demo seeds a "Spain City Break" project


def test_search_by_amount(client):
    client.post("/api/backup/demo")
    # The demo mortgage is 875.00 (stored as -875.00); an amount query finds it.
    txns = client.get("/api/search", params={"q": "875.00"}).json()["transactions"]
    assert any(abs(float(t["amount"])) == pytest.approx(875.00) for t in txns)


def test_search_by_amount_strips_currency_symbols(client):
    client.post("/api/backup/demo")
    # The same mortgage as above, but typed with a currency symbol — the symbol
    # is stripped before parsing (SR-B4: not just £, also $/€).
    for q in ("£875.00", "$875.00", "€875"):
        txns = client.get("/api/search", params={"q": q}).json()["transactions"]
        assert any(abs(float(t["amount"])) == pytest.approx(875.00) for t in txns), q


def test_search_short_query_returns_empty(client):
    client.post("/api/backup/demo")
    r = client.get("/api/search", params={"q": "a"}).json()
    assert r["transactions"] == [] and r["vendors"] == [] and r["categories"] == []


# --- richer search: tag-name matches + filter tokens ---


def _txn(db, desc, when, *, category=None, tags=(), amount="-10.00"):
    txn = Transaction(
        household_id=get_or_create_default_household(db).id,
        transaction_date=when,
        description_raw=desc,
        amount=Decimal(amount),
        direction="debit",
        category_id=category.id if category is not None else None,
        tags=list(tags),
    )
    db.add(txn)
    db.flush()
    return txn


def _ids(result):
    return {t["id"] for t in result["transactions"]}


def test_search_by_tag_name_returns_tagged_transaction(db):
    tag = Tag(name="reimbursable", household_id=get_or_create_default_household(db).id)
    db.add(tag)
    db.flush()
    tagged = _txn(db, "LUNCH MEETING", date(2026, 2, 1), tags=[tag])
    _txn(db, "OTHER SPEND", date(2026, 2, 2))
    db.commit()

    got = _ids(search_service.search(db, "reimbursable", account_ids=None))
    assert got == {tagged.id}


def test_category_token_restricts_and_strips_from_text(db):
    groceries = Category(name="Groceries", household_id=get_or_create_default_household(db).id)
    fuel = Category(name="Fuel", household_id=get_or_create_default_household(db).id)
    db.add_all([groceries, fuel])
    db.flush()
    g = _txn(db, "CORNER SHOP", date(2026, 2, 1), category=groceries)
    _txn(db, "CORNER SHOP FUEL", date(2026, 2, 2), category=fuel)
    db.commit()

    # The token both restricts to the category and is stripped from the free text
    # (otherwise "category:groceries" as literal text would match nothing).
    got = _ids(search_service.search(db, "shop category:groceries", account_ids=None))
    assert got == {g.id}


def test_category_token_unknown_name_restricts_to_nothing(db):
    cat = Category(name="Groceries", household_id=get_or_create_default_household(db).id)
    db.add(cat)
    db.flush()
    _txn(db, "CORNER SHOP", date(2026, 2, 1), category=cat)
    db.commit()

    got = search_service.search(db, "shop category:nosuchcat", account_ids=None)
    assert got["transactions"] == []


def test_date_tokens_restrict_and_strip(db):
    jan = _txn(db, "COFFEE", date(2026, 1, 15))
    mar = _txn(db, "COFFEE", date(2026, 3, 15))
    db.commit()

    # after: lower bound (inclusive), text still applied.
    assert _ids(search_service.search(db, "coffee after:2026-02-01", account_ids=None)) == {mar.id}
    # before: upper bound (inclusive).
    assert _ids(search_service.search(db, "coffee before:2026-02-01", account_ids=None)) == {jan.id}
    # range with whole-month tokens: 2026-01 covers all of January only.
    assert _ids(search_service.search(db, "coffee 2026-01..2026-01", account_ids=None)) == {jan.id}


def test_unknown_token_stays_plain_text(db):
    hit = _txn(db, "PAYMENT foo:bar reference", date(2026, 2, 1))
    _txn(db, "UNRELATED", date(2026, 2, 2))
    db.commit()

    # "foo:bar" is not a known token, so it is matched as literal free text.
    got = _ids(search_service.search(db, "foo:bar", account_ids=None))
    assert got == {hit.id}


def test_token_free_query_unchanged(db):
    hit = _txn(db, "TESCO STORES", date(2026, 2, 1))
    _txn(db, "SHELL FUEL", date(2026, 2, 2))
    db.commit()

    got = _ids(search_service.search(db, "tesco", account_ids=None))
    assert got == {hit.id}
