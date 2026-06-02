"""Per-member spend filter (Dashboard + Transactions; backlog #66/#82).

Picking a household member narrows spend to *that member's own accounts*,
intersected with what the caller may already see — so the owner can inspect each
person's spend, but a member can never use the filter to peek at another's
private account.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.db.session import SessionLocal
from app.models import Account, Transaction

MONTH = {"month": "2026-05-01"}


def _hdr(uid: str, name: str | None = None) -> dict[str, str]:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name or uid}


def _member(client, uid: str, name: str) -> int:
    client.get("/api/users/me", headers=_hdr(uid, name))
    row = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == uid)
    client.patch(f"/api/users/{row}", json={"role": "member", "status": "approved"})
    return row


def _txn(account_id: int, amt: str, desc: str) -> Transaction:
    return Transaction(
        account_id=account_id, transaction_date=date(2026, 5, 15),
        description_raw=desc, amount=Decimal(amt), currency="GBP",
        direction="debit" if Decimal(amt) < 0 else "credit",
        base_amount=Decimal(amt), fx_rate=Decimal("1"),
    )


def _seed(client) -> dict:
    """Owner + Alice + Bob; a shared account and one private account each, with a
    transaction in every account."""
    client.get("/api/users/me")  # headerless → local owner
    alice = _member(client, "ha-alice", "Alice")
    bob = _member(client, "ha-bob", "Bob")
    with SessionLocal() as db:
        shared = Account(name="Shared", account_type="current_account", currency="GBP")
        a_priv = Account(name="Alice Acct", account_type="current_account", currency="GBP",
                         owner_user_id=alice, is_shared=False)
        b_priv = Account(name="Bob Acct", account_type="current_account", currency="GBP",
                         owner_user_id=bob, is_shared=False)
        db.add_all([shared, a_priv, b_priv])
        db.flush()
        db.add(_txn(shared.id, "-10.00", "SHARED SHOP"))
        db.add(_txn(a_priv.id, "-3.00", "ALICE BUY"))
        db.add(_txn(b_priv.id, "-7.00", "BOB BUY"))
        db.commit()
    return {"alice": alice, "bob": bob}


def _descs(client, params=None, headers=None) -> set[str]:
    resp = client.get("/api/transactions", params=params, headers=headers)
    return {t["description_raw"] for t in resp.json()["items"]}


def test_members_endpoint_readable_by_any_member(client):
    _seed(client)
    # A non-owner member may read the members list (it powers the filter dropdown).
    members = client.get("/api/users/members", headers=_hdr("ha-alice", "Alice")).json()
    names = {m["display_name"] for m in members}
    assert {"Alice", "Bob"} <= names
    assert all(set(m.keys()) == {"id", "display_name", "role"} for m in members)  # minimal shape, no email


def test_owner_member_filter_narrows_transactions(client):
    ids = _seed(client)
    assert _descs(client, {"member_id": ids["alice"]}) == {"ALICE BUY"}
    assert _descs(client, {"member_id": ids["bob"]}) == {"BOB BUY"}
    assert _descs(client) == {"SHARED SHOP", "ALICE BUY", "BOB BUY"}  # owner, unfiltered


def test_owner_member_filter_narrows_dashboard(client):
    ids = _seed(client)
    alice = client.get("/api/dashboard/summary", params={**MONTH, "member_id": ids["alice"]}).json()
    assert Decimal(alice["spend_this_month"]) == Decimal("3.00")
    assert alice["total_transactions"] == 1


def test_member_cannot_peek_at_others_via_filter(client):
    ids = _seed(client)
    alice = _hdr("ha-alice", "Alice")
    # Alice asking for Bob's accounts: the scope intersection yields nothing.
    assert _descs(client, {"member_id": ids["bob"]}, alice) == set()
    bob_spend = client.get(
        "/api/dashboard/summary", params={**MONTH, "member_id": ids["bob"]}, headers=alice
    ).json()
    assert Decimal(bob_spend["spend_this_month"]) == Decimal("0")


def test_member_filter_export_matches_view(client):
    ids = _seed(client)
    csv = client.get("/api/export/transactions.csv", params={"member_id": ids["bob"]}).content.decode("utf-8-sig")
    assert "BOB BUY" in csv
    assert "ALICE BUY" not in csv and "SHARED SHOP" not in csv
