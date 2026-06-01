"""Accounts management API — shared vs private (Stage 12-B2; #66/#82)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.db.session import SessionLocal
from app.models import Account, Transaction


def _hdr(uid: str, name: str | None = None) -> dict[str, str]:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name or uid}


def _member(client, uid: str, name: str) -> int:
    client.get("/api/users/me", headers=_hdr(uid, name))
    uid_row = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == uid)
    client.patch(f"/api/users/{uid_row}", json={"role": "member", "status": "approved"})
    return uid_row


def _setup(client) -> dict:
    client.get("/api/users/me")  # local owner
    alice = _member(client, "ha-alice", "Alice")
    bob = _member(client, "ha-bob", "Bob")
    with SessionLocal() as db:
        shared = Account(name="Shared", account_type="current_account", currency="GBP")
        bobs = Account(name="Bobs Card", account_type="credit_card", currency="GBP")
        db.add_all([shared, bobs])
        db.flush()
        db.add(Transaction(account_id=bobs.id, transaction_date=date(2026, 5, 2),
                           description_raw="SECRET", amount=Decimal("-5.00"), currency="GBP",
                           direction="debit", base_amount=Decimal("-5.00"), fx_rate=Decimal("1")))
        ids = {"alice": alice, "bob": bob, "shared": shared.id, "bobs": bobs.id}
        db.commit()
    return ids


def _acct(client, headers, account_id: int) -> dict | None:
    rows = client.get("/api/accounts", headers=headers).json()
    return next((a for a in rows if a["id"] == account_id), None)


def test_owner_lists_all_accounts(client):
    ids = _setup(client)
    rows = client.get("/api/accounts").json()
    assert {a["id"] for a in rows} >= {ids["shared"], ids["bobs"]}
    assert all(a["owner_user_id"] is None for a in rows)  # unowned by default


def test_owner_assigns_owner_making_account_private(client):
    ids = _setup(client)
    r = client.patch(f"/api/accounts/{ids['bobs']}", json={"owner_user_id": ids["bob"], "is_shared": False})
    assert r.status_code == 200
    assert r.json()["is_private"] is True
    assert r.json()["owner_name"] == "Bob"
    # Alice no longer sees the account or its transactions; the owner still does.
    assert _acct(client, _hdr("ha-alice", "Alice"), ids["bobs"]) is None
    assert _acct(client, None, ids["bobs"]) is not None
    alice_txns = client.get("/api/transactions", headers=_hdr("ha-alice", "Alice")).json()["items"]
    assert all(t["description_raw"] != "SECRET" for t in alice_txns)


def test_member_can_toggle_is_shared_on_own_account(client):
    ids = _setup(client)
    client.patch(f"/api/accounts/{ids['bobs']}", json={"owner_user_id": ids["bob"], "is_shared": False})
    # Bob re-shares his own account → Alice can see it again.
    bob = _hdr("ha-bob", "Bob")
    assert client.patch(f"/api/accounts/{ids['bobs']}", json={"is_shared": True}, headers=bob).status_code == 200
    assert _acct(client, _hdr("ha-alice", "Alice"), ids["bobs"]) is not None


def test_member_cannot_change_ownership(client):
    ids = _setup(client)
    client.patch(f"/api/accounts/{ids['bobs']}", json={"owner_user_id": ids["bob"]})
    bob = _hdr("ha-bob", "Bob")
    r = client.patch(f"/api/accounts/{ids['bobs']}", json={"owner_user_id": ids["alice"]}, headers=bob)
    assert r.status_code == 403


def test_member_cannot_edit_unowned_account(client):
    ids = _setup(client)
    r = client.patch(f"/api/accounts/{ids['shared']}", json={"name": "Hijack"}, headers=_hdr("ha-alice", "Alice"))
    assert r.status_code == 403


def test_member_patch_others_private_404(client):
    ids = _setup(client)
    client.patch(f"/api/accounts/{ids['bobs']}", json={"owner_user_id": ids["bob"], "is_shared": False})
    r = client.patch(f"/api/accounts/{ids['bobs']}", json={"name": "x"}, headers=_hdr("ha-alice", "Alice"))
    assert r.status_code == 404


def test_patch_invalid_account_type(client):
    ids = _setup(client)
    r = client.patch(f"/api/accounts/{ids['shared']}", json={"account_type": "bogus"})
    assert r.status_code == 400
