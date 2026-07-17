"""Explicit-rule proof for ``account_scope_condition`` (SR-E7).

The transaction-visibility predicate has three cases; this file pins each so a
future refactor can't silently broaden access:

  * ``None`` (owner/admin) → no condition → sees every row, **including orphan
    transactions** (``account_id IS NULL``, e.g. a row whose account was deleted
    via the ``ON DELETE SET NULL`` FK).
  * a restricted **set** → ``account_id IN (<set>)`` only. Orphans are NOT
    matched → they are owner-visible only, never leaked to a member.
  * an **empty set** → matches *nothing* (an empty SQL ``IN`` is always false),
    never everything.

DB-level tests exercise the predicate directly; the final test proves the same
orphan rule end-to-end through the real ``/api/transactions`` route.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Account, Transaction, User
from app.services import auth_service
from app.services.scope import account_scope_condition


def _txn(account_id: int | None, amt: str, desc: str) -> Transaction:
    return Transaction(
        account_id=account_id, transaction_date=date(2026, 5, 15),
        description_raw=desc, amount=Decimal(amt), currency="GBP",
        direction="debit", base_amount=Decimal(amt), fx_rate=Decimal("1"),
    )


def _matching_descs(db, account_ids: set[int] | None) -> set[str]:
    """Descriptions of the transactions the scope predicate admits."""
    stmt = select(Transaction.description_raw).where(*account_scope_condition(account_ids))
    return set(db.scalars(stmt).all())


def _seed(db) -> dict[str, int]:
    a1 = Account(name="A1", account_type="current_account", currency="GBP")
    a2 = Account(name="A2", account_type="current_account", currency="GBP")
    db.add_all([a1, a2])
    db.flush()
    db.add_all([
        _txn(a1.id, "-1.00", "IN_A1"),
        _txn(a2.id, "-2.00", "IN_A2"),
        _txn(None, "-3.00", "ORPHAN"),  # account_id IS NULL
    ])
    db.commit()
    return {"a1": a1.id, "a2": a2.id}


# --- predicate shape (no DB) ---

def test_none_returns_empty_condition_list():
    # Owner/admin fast path: unrestricted → no WHERE fragment at all.
    assert account_scope_condition(None) == []


def test_set_returns_exactly_one_condition():
    ids = {1, 2}
    cond = account_scope_condition(ids)
    assert len(cond) == 1  # a single IN(...) term, no OR-orphan clause


def test_empty_set_returns_a_condition_not_empty_list():
    # An empty set is still "restricted" → must yield a (false) condition, NOT the
    # unrestricted ``[]`` — confusing the two would show a member everything.
    cond = account_scope_condition(set())
    assert cond != []
    assert len(cond) == 1


# --- predicate behaviour against real rows ---

def test_none_is_unrestricted_and_includes_orphans(db):
    _seed(db)
    assert _matching_descs(db, None) == {"IN_A1", "IN_A2", "ORPHAN"}


def test_restricted_member_sees_only_scoped_accounts_not_orphans(db):
    ids = _seed(db)
    # (a) a normal member sees exactly their scoped accounts' txns...
    assert _matching_descs(db, {ids["a1"]}) == {"IN_A1"}
    # ...and (c) the orphan follows the new rule: NOT visible to a restricted member.
    assert "ORPHAN" not in _matching_descs(db, {ids["a1"], ids["a2"]})
    assert _matching_descs(db, {ids["a1"], ids["a2"]}) == {"IN_A1", "IN_A2"}


def test_empty_visible_set_matches_nothing_not_everything(db):
    _seed(db)
    # (d) empty visible set → nothing at all (never the whole table, never orphans).
    assert _matching_descs(db, set()) == set()


# --- end-to-end: same orphan rule through the API ---

def _hdr(uid: str) -> dict[str, str]:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": uid}


def test_orphan_txn_owner_only_through_transactions_route(client):
    # Owner (headerless) + one approved member.
    client.get("/api/users/me")  # local owner
    client.get("/api/users/me", headers=_hdr("ha-alice"))
    with SessionLocal() as db:
        alice_row = next(
            u.id for u in db.scalars(select(User)).all() if u.external_id == "ha-alice"
        )
    client.patch(f"/api/users/{alice_row}", json={"role": "member", "status": "approved"})

    with SessionLocal() as db:
        shared = Account(name="Shared", account_type="current_account", currency="GBP")
        db.add(shared)
        db.flush()
        db.add_all([_txn(shared.id, "-1.00", "SHARED"), _txn(None, "-3.00", "ORPHAN")])
        db.commit()

    def descs(headers):
        return {t["description_raw"] for t in client.get("/api/transactions", headers=headers).json()["items"]}

    # (b) owner sees the shared row AND the orphan.
    assert descs(None) == {"SHARED", "ORPHAN"}
    # (c) member sees the shared row but NOT the orphan.
    assert descs(_hdr("ha-alice")) == {"SHARED"}
