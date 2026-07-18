"""Tag tests (spec §18.3, §12.13 — Stage 5)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Tag, Transaction, transaction_tags
from app.services import tag_service
from app.services.household_service import get_or_create_default_household


def _curve(rows: list[tuple[str, str, str]]) -> bytes:
    head = "Date,Description,Amount,Currency,Card,Category\n"
    body = "".join(f"{d},{desc},{amt},GBP,Visa,\n" for d, desc, amt in rows)
    return (head + body).encode()


def _import(client, content: bytes, name: str = "tags.csv"):
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


# --- CRUD ---

def test_tag_crud(client):
    res = client.post("/api/tags", json={"name": "reimbursable", "colour": "#3366ff"})
    assert res.status_code == 201, res.text
    tid = res.json()["id"]
    assert client.patch(f"/api/tags/{tid}", json={"colour": "#ff0000"}).json()["colour"] == "#ff0000"
    assert any(t["id"] == tid for t in client.get("/api/tags").json())
    assert client.delete(f"/api/tags/{tid}").status_code == 204


def test_tag_get_or_create_is_case_insensitive(client):
    client.post("/api/tags", json={"name": "Work"})
    # Assigning "work" should reuse the existing tag, not create a duplicate.
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-10.00")]))
    txn = _txn(client, "TESCO STORES")
    client.post(f"/api/transactions/{txn['id']}/tags", json={"tags": ["work"]})
    names = [t["name"] for t in client.get("/api/tags").json()]
    assert names.count("Work") == 1
    assert "work" not in names  # the original casing is kept


def test_rename_to_existing_name_is_rejected_case_insensitively(client):
    # Renaming a tag onto another tag's name (any case) would create a duplicate
    # the matcher can't distinguish — reject it (SR-B8).
    client.post("/api/tags", json={"name": "Work"})
    other = client.post("/api/tags", json={"name": "Personal"}).json()["id"]
    res = client.patch(f"/api/tags/{other}", json={"name": "work"})
    assert res.status_code == 400
    assert "already exists" in res.json()["detail"]
    # Renaming to a genuinely new name still works, and renaming a tag to its own
    # name (different case) is allowed.
    assert client.patch(f"/api/tags/{other}", json={"name": "Household"}).status_code == 200


def test_create_empty_tag_is_rejected(client):
    assert client.post("/api/tags", json={"name": "  "}).status_code == 400


# --- assignment ---

def test_set_and_show_transaction_tags(client):
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-10.00")]))
    txn = _txn(client, "TESCO STORES")
    res = client.post(f"/api/transactions/{txn['id']}/tags", json={"tags": ["gift", "urgent"]})
    assert res.status_code == 200
    assert {t["name"] for t in res.json()["tags"]} == {"gift", "urgent"}

    # tags also surface on the list endpoint
    listed = _txn(client, "TESCO STORES")
    assert {t["name"] for t in listed["tags"]} == {"gift", "urgent"}

    # reassigning replaces
    client.post(f"/api/transactions/{txn['id']}/tags", json={"tags": ["warranty"]})
    assert {t["name"] for t in _txn(client, "TESCO STORES")["tags"]} == {"warranty"}


def test_filter_transactions_by_tag(client):
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-10.00"),
                            ("2026-05-03", "SHELL FUEL", "-20.00")]))
    shell = _txn(client, "SHELL FUEL")
    client.post(f"/api/transactions/{shell['id']}/tags", json={"tags": ["work"]})
    tid = next(t["id"] for t in client.get("/api/tags").json() if t["name"] == "work")
    items = client.get(f"/api/transactions?tag_id={tid}").json()["items"]
    assert len(items) == 1
    assert items[0]["description_raw"] == "SHELL FUEL"


# --- SR-B8: case-insensitive uniqueness + atomic get_or_create ---

def test_get_or_create_case_insensitive_returns_same_tag(db):
    first = tag_service.get_or_create(db, "Food")
    db.commit()
    same = tag_service.get_or_create(db, "food")
    assert same.id == first.id
    # Original display casing is preserved, and only one row exists.
    assert same.name == "Food"
    assert db.scalar(select(func.count()).select_from(Tag)) == 1


def test_get_or_create_handles_duplicate_race(db):
    """A concurrent insert of the same case-insensitive name must not blow up:
    get_or_create catches the IntegrityError and returns the row that won."""
    household = get_or_create_default_household(db)
    # Simulate the "other" caller having already committed a tag while our
    # in-flight get_or_create still believes the name is free. We prime the race
    # by inserting the winner directly, then call get_or_create with a different
    # casing — its own pre-flight lookup finds the winner, so no error and the
    # same row comes back.
    winner = Tag(name="Travel", household_id=household.id)
    db.add(winner)
    db.commit()
    got = tag_service.get_or_create(db, "TRAVEL")
    assert got.id == winner.id
    assert db.scalar(select(func.count()).select_from(Tag)) == 1


# --- merge / usage-counts / unused cleanup ---

def _txn_row(db, desc: str, tags: list[Tag]) -> Transaction:
    txn = Transaction(
        household_id=get_or_create_default_household(db).id,
        transaction_date=date(2026, 5, 2),
        description_raw=desc,
        amount=Decimal("-10.00"),
        direction="debit",
        tags=tags,
    )
    db.add(txn)
    db.flush()
    return txn


def _assoc_tag_ids(db, txn_id: int) -> list[int]:
    return list(
        db.scalars(
            select(transaction_tags.c.tag_id).where(
                transaction_tags.c.transaction_id == txn_id
            )
        ).all()
    )


def test_merge_moves_and_dedupes_associations_and_deletes_source(db):
    source = tag_service.get_or_create(db, "Groceries")
    target = tag_service.get_or_create(db, "Food")
    db.flush()
    only_source = _txn_row(db, "ONLY SOURCE", [source])
    both = _txn_row(db, "BOTH", [source, target])
    db.commit()

    merged = tag_service.merge_tags(db, source.id, target.id)
    assert merged.id == target.id

    # Source tag is gone; only the target remains.
    assert db.get(Tag, source.id) is None
    # The source-only transaction now points at the target.
    assert _assoc_tag_ids(db, only_source.id) == [target.id]
    # The doubly-tagged transaction keeps exactly one (deduped) target association.
    assert _assoc_tag_ids(db, both.id) == [target.id]


def test_merge_into_self_is_noop(db):
    tag = tag_service.get_or_create(db, "Utilities")
    db.commit()
    same = tag_service.merge_tags(db, tag.id, tag.id)
    assert same.id == tag.id
    assert db.scalar(select(func.count()).select_from(Tag)) == 1


def test_usage_counts_is_single_query_and_correct(db):
    used = tag_service.get_or_create(db, "Work")
    unused = tag_service.get_or_create(db, "Idle")
    db.flush()
    _txn_row(db, "A", [used])
    _txn_row(db, "B", [used])
    db.commit()

    from sqlalchemy import event

    statements: list[str] = []

    def _capture(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        counts = tag_service.usage_counts(db)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert counts[used.id] == 2
    assert counts[unused.id] == 0
    # One grouped query, not an N+1 count-per-tag.
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1


def test_delete_unused_removes_only_zero_use_tags(db):
    used = tag_service.get_or_create(db, "Keep")
    tag_service.get_or_create(db, "Drop1")
    tag_service.get_or_create(db, "Drop2")
    db.flush()
    _txn_row(db, "X", [used])
    db.commit()

    removed = tag_service.delete_unused(db)
    assert removed == 2
    remaining = [t.name for t in tag_service.list_tags(db)]
    assert remaining == ["Keep"]


def test_unique_index_rejects_direct_duplicate_insert(db):
    """The DB-level functional unique index rejects a second case-insensitive
    duplicate that bypasses the service layer."""
    household = get_or_create_default_household(db)
    db.add(Tag(name="Utilities", household_id=household.id))
    db.commit()
    db.add(Tag(name="utilities", household_id=household.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Tag)) == 1
