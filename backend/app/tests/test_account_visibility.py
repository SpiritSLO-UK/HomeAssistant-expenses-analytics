"""Shared vs private account visibility — the non-leak proof (Stage B; #66/#82).

A non-admin member must NOT see another member's private account anywhere: the
transactions list, a single transaction (404), dashboard totals + counts,
category/vendor breakdowns, CSV export, budgets, savings, subscriptions, or AI
batch. The owner/admin sees everything; legacy unowned accounts stay visible to
all (no regression); an empty visible set yields nothing (never everything).

Private accounts are manufactured directly in the DB (the Accounts UI that lets a
user flip visibility is Stage B2), so enforcement is proven before any UI exists.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Account, SavingsBalance, Subscription, Transaction, User
from app.services import auth_service

MONTH = {"month": "2026-05-01"}


def _hdr(uid: str, name: str | None = None) -> dict[str, str]:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name or uid}


def _member(client, uid: str, name: str) -> int:
    client.get("/api/users/me", headers=_hdr(uid, name))
    uid_row = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == uid)
    client.patch(f"/api/users/{uid_row}", json={"role": "member", "status": "approved"})
    return uid_row


def _txn(account_id: int, amt: str, desc: str, merchant: str | None = None) -> Transaction:
    return Transaction(
        account_id=account_id, transaction_date=date(2026, 5, 15),
        description_raw=desc, merchant_raw=merchant, amount=Decimal(amt), currency="GBP",
        direction="debit" if Decimal(amt) < 0 else "credit",
        base_amount=Decimal(amt), fx_rate=Decimal("1"),
    )


def _seed(client) -> dict:
    """Owner + members Alice/Bob; a shared account and a Bob-private account, each
    with a transaction; a shared and a Bob-private savings pot; a subscription
    backed only by Bob's private transaction."""
    client.get("/api/users/me")  # headerless → local owner
    alice = _member(client, "ha-alice", "Alice")
    bob = _member(client, "ha-bob", "Bob")
    with SessionLocal() as db:
        shared = Account(name="Shared", account_type="current_account", currency="GBP")
        bob_priv = Account(name="Bob Private", account_type="current_account", currency="GBP",
                           owner_user_id=bob, is_shared=False)
        bob_sav = Account(name="Bob Piggy", account_type="savings", currency="GBP",
                          owner_user_id=bob, is_shared=False)
        shared_sav = Account(name="Shared Pot", account_type="savings", currency="GBP")
        db.add_all([shared, bob_priv, bob_sav, shared_sav])
        db.flush()
        db.add(_txn(shared.id, "-10.00", "SHARED SHOP"))
        db.add(_txn(bob_priv.id, "-7.00", "BOB SECRET", merchant="NETFLIX"))
        db.add(SavingsBalance(account_id=bob_sav.id, as_of_date=date(2026, 5, 1), balance=Decimal("100.00"), currency="GBP"))
        db.add(SavingsBalance(account_id=shared_sav.id, as_of_date=date(2026, 5, 1), balance=Decimal("50.00"), currency="GBP"))
        db.add(Subscription(name="NETFLIX", amount=Decimal("7.00"), currency="GBP", frequency="monthly",
                            interval_days=30, status="active", next_expected_date=date(2026, 6, 14)))
        db.commit()
    return {"alice": alice, "bob": bob}


def _descs(client, headers) -> set[str]:
    return {t["description_raw"] for t in client.get("/api/transactions", headers=headers).json()["items"]}


def test_member_transactions_exclude_others_private(client):
    _seed(client)
    alice = _hdr("ha-alice", "Alice")
    assert _descs(client, alice) == {"SHARED SHOP"}            # not "BOB SECRET"
    assert _descs(client, None) == {"SHARED SHOP", "BOB SECRET"}  # owner sees both


def test_member_single_transaction_404(client):
    _seed(client)
    bob_txn = next(t["id"] for t in client.get("/api/transactions").json()["items"] if t["description_raw"] == "BOB SECRET")
    alice = _hdr("ha-alice", "Alice")
    assert client.get(f"/api/transactions/{bob_txn}", headers=alice).status_code == 404
    assert client.patch(f"/api/transactions/{bob_txn}", json={"category_id": None}, headers=alice).status_code == 404
    assert client.delete(f"/api/transactions/{bob_txn}", headers=alice).status_code == 404
    assert client.get(f"/api/transactions/{bob_txn}").status_code == 200  # owner can


def test_member_dashboard_excludes_others_private(client):
    _seed(client)
    alice = client.get("/api/dashboard/summary", params=MONTH, headers=_hdr("ha-alice", "Alice")).json()
    owner = client.get("/api/dashboard/summary", params=MONTH).json()
    assert alice["spend_this_month"] == "10.00"   # shared only
    assert owner["spend_this_month"] == "17.00"   # shared + Bob's private
    assert alice["total_transactions"] == 1        # count leak guard
    assert owner["total_transactions"] == 2


def test_member_breakdown_and_export_exclude_others_private(client):
    _seed(client)
    alice = _hdr("ha-alice", "Alice")
    cats = client.get("/api/dashboard/categories", params=MONTH, headers=alice).json()
    assert sum(Decimal(c["total"]) for c in cats) == Decimal("10.00")
    csv = client.get("/api/export/transactions.csv", headers=alice).content.decode("utf-8-sig")
    assert "SHARED SHOP" in csv and "BOB SECRET" not in csv


def test_member_savings_excludes_others_private(client):
    _seed(client)
    alice = client.get("/api/savings/summary", headers=_hdr("ha-alice", "Alice")).json()
    names = {a["name"] for a in alice["accounts"]}
    assert names == {"Shared Pot"}
    assert alice["total_savings"] == "50.00"
    owner = client.get("/api/savings/summary").json()
    assert owner["total_savings"] == "150.00"


def test_member_subscriptions_exclude_others_private(client):
    _seed(client)
    alice = client.get("/api/subscriptions", headers=_hdr("ha-alice", "Alice")).json()
    assert all(s["name"] != "NETFLIX" for s in alice)   # backed only by Bob's private txn
    owner = client.get("/api/subscriptions").json()
    assert any(s["name"] == "NETFLIX" for s in owner)


def test_owner_visible_account_ids_is_none(client):
    _seed(client)
    with SessionLocal() as db:
        owner = db.scalars(select(User).where(User.role == "owner")).first()
        assert auth_service.visible_account_ids(db, owner) is None


def test_legacy_unowned_account_visible_to_all(client):
    _seed(client)
    _member(client, "ha-carol", "Carol")  # a fresh approved member with no accounts
    descs = _descs(client, _hdr("ha-carol", "Carol"))
    assert "SHARED SHOP" in descs   # the unowned/shared account is visible to everyone
    assert "BOB SECRET" not in descs


def test_empty_visible_set_returns_nothing_not_everything(client):
    # Only a private account exists (owned by Bob); a different member sees nothing.
    client.get("/api/users/me")  # owner
    bob = _member(client, "ha-bob", "Bob")
    carol = _member(client, "ha-carol", "Carol")
    with SessionLocal() as db:
        priv = Account(name="Bob Only", account_type="current_account", currency="GBP",
                       owner_user_id=bob, is_shared=False)
        db.add(priv)
        db.flush()
        db.add(_txn(priv.id, "-9.00", "PRIVATE"))
        db.commit()
        carol_user = db.get(User, carol)
        assert auth_service.visible_account_ids(db, carol_user) == set()  # empty, NOT None
    assert _descs(client, _hdr("ha-carol", "Carol")) == set()  # nothing, not everything
    assert _descs(client, None) == {"PRIVATE"}                  # owner still sees it


def test_visible_ids_memoized_within_request_and_scope_unchanged(client):
    """SR-E1 perf: repeated scope lookups in one request hit the DB once, and the
    resolved scope is byte-for-byte what a fresh (un-memoized) query would give."""
    from sqlalchemy import event

    ids = _seed(client)
    with SessionLocal() as db:
        alice = db.get(User, ids["alice"])
        # Ground truth: what the base predicate returns with no memo in play.
        expected = set(db.scalars(select(Account.id).where(auth_service._shared_or_own(alice.id))).all())

        account_selects: list[str] = []

        def _count(conn, cursor, statement, params, context, executemany):
            if "FROM accounts" in statement:
                account_selects.append(statement)

        event.listen(db.bind, "after_cursor_execute", _count)
        try:
            first = auth_service.visible_account_ids(db, alice)
            second = auth_service.visible_account_ids(db, alice)
        finally:
            event.remove(db.bind, "after_cursor_execute", _count)

        assert first == expected               # scope is unchanged by memoization
        assert second == expected
        assert len(account_selects) == 1       # the second call was served from cache
        assert first is not second             # a fresh mutable set each call
        first.add(-999)                        # mutating the result...
        assert -999 not in auth_service.visible_account_ids(db, alice)  # ...never corrupts the memo


def test_visible_ids_not_shared_across_users_or_requests(client):
    """Two users resolved on one session get their own scope (no cross-user leak),
    and a fresh session (i.e. a new request) starts with an empty memo."""
    ids = _seed(client)
    with SessionLocal() as db:
        alice = db.get(User, ids["alice"])
        bob = db.get(User, ids["bob"])
        alice_scope = auth_service.visible_account_ids(db, alice)
        bob_scope = auth_service.visible_account_ids(db, bob)
        # Bob owns a private account Alice cannot see, so the sets must differ and
        # Bob must NOT have inherited Alice's cached scope.
        assert alice_scope != bob_scope
        assert bob_scope == auth_service.visible_account_ids(db, bob)
    # A brand-new session (next request) carries no memo from the previous one.
    with SessionLocal() as fresh:
        assert auth_service._VISIBLE_IDS_MEMO not in fresh.info


def test_view_toggle_narrows(client):
    ids = _seed(client)
    # Give Alice her own private account so Mine/Shared/All differ for her.
    with SessionLocal() as db:
        a_priv = Account(name="Alice Private", account_type="current_account", currency="GBP",
                         owner_user_id=ids["alice"], is_shared=False)
        db.add(a_priv)
        db.flush()
        db.add(_txn(a_priv.id, "-3.00", "ALICE SECRET"))
        db.commit()
    alice = _hdr("ha-alice", "Alice")
    # The transactions list (scope=all) never shows Bob's private spend.
    assert _descs(client, alice) == {"SHARED SHOP", "ALICE SECRET"}

    def spend(view):
        return client.get("/api/dashboard/summary", params={**MONTH, "view": view}, headers=alice).json()["spend_this_month"]

    assert spend("all") == "13.00"     # shared (10) + Alice's own (3), never Bob's (7)
    assert spend("mine") == "3.00"     # only Alice's own account
    assert spend("shared") == "10.00"  # only the shared account
