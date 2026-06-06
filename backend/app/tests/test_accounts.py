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


# --- create / delete / merge (manage accounts, #112) ---


def test_create_account(client):
    _setup(client)
    r = client.post("/api/accounts", json={"name": "Savings Pot", "account_type": "savings"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Savings Pot"
    assert body["account_type"] == "savings"
    assert body["currency"] == "GBP"  # default base currency
    assert body["in_use"] is False
    assert _acct(client, None, body["id"]) is not None


def test_create_account_invalid_type(client):
    _setup(client)
    r = client.post("/api/accounts", json={"name": "X", "account_type": "bogus"})
    assert r.status_code == 400


def test_member_creates_own_private_account(client):
    ids = _setup(client)
    alice = _hdr("ha-alice", "Alice")
    r = client.post("/api/accounts", json={"name": "Alice Wallet", "account_type": "cash"}, headers=alice)
    assert r.status_code == 200
    # A non-admin's new account is owned by them (private) regardless of payload.
    assert r.json()["owner_user_id"] == ids["alice"]
    assert r.json()["is_private"] is True


def test_delete_empty_account(client):
    _setup(client)
    new_id = client.post("/api/accounts", json={"name": "Temp", "account_type": "other"}).json()["id"]
    r = client.delete(f"/api/accounts/{new_id}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert _acct(client, None, new_id) is None


def test_delete_account_with_data_409(client):
    ids = _setup(client)  # 'bobs' has a transaction
    r = client.delete(f"/api/accounts/{ids['bobs']}")
    assert r.status_code == 409
    assert "merge" in r.json()["detail"].lower()
    assert _acct(client, None, ids["bobs"]) is not None  # still there


def test_member_cannot_delete_account(client):
    _setup(client)
    new_id = client.post("/api/accounts", json={"name": "Temp", "account_type": "other"}).json()["id"]
    r = client.delete(f"/api/accounts/{new_id}", headers=_hdr("ha-alice", "Alice"))
    assert r.status_code == 403  # owner-only


def test_merge_account_repoints_transactions(client):
    ids = _setup(client)  # 'bobs' has the SECRET transaction; 'shared' is empty
    r = client.post(f"/api/accounts/{ids['bobs']}/merge", json={"target_id": ids["shared"]})
    assert r.status_code == 200
    assert r.json()["id"] == ids["shared"]
    assert r.json()["in_use"] is True  # the transaction moved onto the target
    assert _acct(client, None, ids["bobs"]) is None  # source deleted
    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.description_raw == "SECRET").one()
        assert txn.account_id == ids["shared"]


def test_merge_into_itself_400(client):
    ids = _setup(client)
    r = client.post(f"/api/accounts/{ids['shared']}/merge", json={"target_id": ids["shared"]})
    assert r.status_code == 400


def test_merge_unknown_account_404(client):
    ids = _setup(client)
    r = client.post(f"/api/accounts/{ids['shared']}/merge", json={"target_id": 999999})
    assert r.status_code == 404
