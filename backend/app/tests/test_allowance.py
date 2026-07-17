"""Child allowance view (Stage 12; backlog #82).

Covers the non-destructive overlay (parent expense unchanged), whole/manual/split
attribution, child budgets fed from allocations, the household budgets view
excluding child budgets, child savings scoping, and the child-role API gate.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Account, Household, Transaction
from app.services import allowance_service, split_service
from app.services.split_service import SplitInput


def _hdr(uid: str, name: str | None = None) -> dict[str, str]:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name or uid}


def _make_child(client, uid="ha-kid", name="Kiddo") -> int:
    client.get("/api/users/me")  # owner bootstraps (headerless)
    client.get("/api/users/me", headers=_hdr(uid, name))  # appears pending
    kid_id = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == uid)
    client.patch(f"/api/users/{kid_id}", json={"role": "child", "status": "approved"})
    return kid_id


def _import_txn(client, desc="ZZQ MARKET", amt="-12.00", d="2026-05-15") -> int:
    head = "Date,Description,Amount,Currency,Card,Category\n"
    csv = (head + f"{d},{desc},{amt},GBP,Visa,\n").encode()
    up = client.post("/api/imports/upload", files={"file": ("a.csv", csv, "text/csv")},
                     data={"parser_id": "curve_csv"}).json()
    client.post(f"/api/imports/{up['import_id']}/confirm")
    return client.get("/api/transactions").json()["items"][0]["id"]


def _a_category(client) -> int:
    return client.get("/api/categories").json()[0]["id"]


def test_attribution_is_non_destructive(client):
    kid = _make_child(client)
    txn_id = _import_txn(client)  # the parent owner's purchase, dated May
    before = client.get("/api/dashboard/summary", params={"month": "2026-05-01"}).json()

    client.post("/api/allowance/allocations", json={"child_id": kid, "transaction_id": txn_id})

    after = client.get("/api/dashboard/summary", params={"month": "2026-05-01"}).json()
    # Parent's books are untouched — same spend, same transaction count.
    assert after["spend_this_month"] == before["spend_this_month"]
    assert after["total_transactions"] == before["total_transactions"]
    # …but it shows on the kid.
    items = client.get("/api/allowance/summary", headers=_hdr("ha-kid", "Kiddo")).json()["items"]
    assert len(items) == 1
    assert items[0]["transaction_id"] == txn_id
    assert items[0]["amount"] == "12.00"  # stored positive money-out


def test_manual_allocation(client):
    kid = _make_child(client)
    cat = _a_category(client)
    r = client.post("/api/allowance/allocations", json={
        "child_id": kid, "category_id": cat, "amount": "2.50", "description": "Candy",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["amount"] == "2.50"
    assert body["transaction_id"] is None
    assert body["description"] == "Candy"


def test_manual_allocation_requires_amount(client):
    kid = _make_child(client)
    r = client.post("/api/allowance/allocations", json={"child_id": kid, "description": "x"})
    assert r.status_code == 400


def test_split_attribution_uses_split_base_amount(client):
    kid = _make_child(client)
    cat = _a_category(client)
    txn_id = _import_txn(client, desc="BIG SHOP", amt="-60.00")
    with SessionLocal() as db:
        txn = db.get(Transaction, txn_id)
        split_service.set_splits(db, txn, [
            SplitInput(amount=Decimal("-5.00"), category_id=cat, description="candy"),
            SplitInput(amount=Decimal("-55.00"), category_id=cat, description="groceries"),
        ])
        candy_split = next(s for s in txn.splits if s.description == "candy")
        alloc = allowance_service.create_allocation(
            db, child_id=kid, transaction_id=txn_id, split_id=candy_split.id
        )
        assert alloc.amount == Decimal("5.00")  # the split line, not the whole shop
        assert alloc.category_id == cat


def test_whole_txn_allocation_uses_base_not_original_amount(client):
    """A foreign-currency transaction's allocation carries the base-converted
    amount, never the original-currency amount (SR-C7)."""
    kid = _make_child(client)
    txn_id = _import_txn(client, desc="PARIS CAFE", amt="-20.00")
    with SessionLocal() as db:
        txn = db.get(Transaction, txn_id)
        txn.currency = "EUR"
        txn.fx_rate = Decimal("0.85")
        txn.base_amount = Decimal("-17.00")  # 20 EUR -> 17 GBP
        txn.needs_rate = False
        db.commit()
        alloc = allowance_service.create_allocation(db, child_id=kid, transaction_id=txn_id)
        assert alloc.amount == Decimal("17.00")  # base GBP, not the 20.00 original


def test_needs_rate_txn_allocation_is_rejected(client):
    """A needs-rate transaction has no base amount, so allocating it (without an
    explicit amount) is refused rather than silently mislabelling the original."""
    kid = _make_child(client)
    txn_id = _import_txn(client, desc="TOKYO SHOP", amt="-3000.00")
    with SessionLocal() as db:
        txn = db.get(Transaction, txn_id)
        txn.currency = "JPY"
        txn.fx_rate = None
        txn.base_amount = None
        txn.needs_rate = True
        db.commit()
        with pytest.raises(ValueError):
            allowance_service.create_allocation(db, child_id=kid, transaction_id=txn_id)
        # …but an explicit amount override still works.
        alloc = allowance_service.create_allocation(
            db, child_id=kid, transaction_id=txn_id, amount=Decimal("18.50")
        )
        assert alloc.amount == Decimal("18.50")


def test_zero_amount_allocation_is_rejected(client):
    """A zero (or effectively-zero) manual amount is refused before ``abs`` (SR-C7)."""
    kid = _make_child(client)
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            allowance_service.create_allocation(db, child_id=kid, amount=Decimal("0"))


def test_txn_outside_child_household_is_rejected(client):
    """A transaction from another household is not in the child's visible scope,
    so it cannot be attributed to the child (SR-C7)."""
    kid = _make_child(client)
    txn_id = _import_txn(client, desc="ELSEWHERE", amt="-9.00")
    with SessionLocal() as db:
        other = Household(name="Neighbours", currency="GBP")
        db.add(other)
        db.flush()
        txn = db.get(Transaction, txn_id)
        txn.household_id = other.id  # a real, but foreign, household
        db.commit()
        with pytest.raises(ValueError):
            allowance_service.create_allocation(db, child_id=kid, transaction_id=txn_id)


def test_child_budget_spend_comes_from_allocations(client):
    kid = _make_child(client)
    cat = _a_category(client)
    # A kid's candy budget (owner_user_id = the child) + a manual spend today.
    client.post("/api/budgets", json={
        "name": "Candy", "amount": "10.00", "period": "monthly",
        "category_id": cat, "owner_user_id": kid,
    })
    client.post("/api/allowance/allocations", json={
        "child_id": kid, "category_id": cat, "amount": "4.00", "description": "sweets",
        "as_of": date.today().isoformat(),
    })
    summary = client.get("/api/allowance/summary", headers=_hdr("ha-kid", "Kiddo")).json()
    assert len(summary["budgets"]) == 1
    b = summary["budgets"][0]
    assert b["spent"] == "4.00"
    assert round(b["percent"]) == 40
    assert b["status"] == "ok"


def test_budgets_summary_excludes_child_budgets(client):
    kid = _make_child(client)
    cat = _a_category(client)
    client.post("/api/budgets", json={"name": "Household", "amount": "100", "period": "monthly", "category_id": cat})
    client.post("/api/budgets", json={"name": "Candy", "amount": "10", "period": "monthly",
                                      "category_id": cat, "owner_user_id": kid})
    names = [b["name"] for b in client.get("/api/budgets/summary").json()]
    assert "Household" in names
    assert "Candy" not in names  # child budget hidden from the household view


def test_child_savings_scoped_to_owned_accounts(client):
    kid = _make_child(client)
    # Two savings accounts; assign one to the kid (Accounts UI is Stage B, so set it directly).
    client.post("/api/savings/accounts", json={"name": "Kid Piggy"})
    client.post("/api/savings/accounts", json={"name": "Parent Pot"})
    with SessionLocal() as db:
        piggy = db.scalars(select(Account).where(Account.name == "Kid Piggy")).one()
        piggy.owner_user_id = kid
        db.commit()
    accts = client.get("/api/allowance/summary", headers=_hdr("ha-kid", "Kiddo")).json()["savings"]["accounts"]
    names = {a["name"] for a in accts}
    assert names == {"Kid Piggy"}  # only the kid's own savings


def test_child_role_gate(client):
    _make_child(client)
    h = _hdr("ha-kid", "Kiddo")
    # Allowed: own allowance summary, and the gate-exempt identity/security endpoints.
    assert client.get("/api/allowance/summary", headers=h).status_code == 200
    assert client.get("/api/users/me", headers=h).status_code == 200
    assert client.get("/api/security/status", headers=h).status_code == 200
    # Blocked everywhere else.
    for path in ["/api/transactions", "/api/budgets/summary", "/api/dashboard/summary",
                 "/api/savings/summary", "/api/categories", "/api/logs/activity",
                 "/api/allowance/allocations?user_id=1"]:
        assert client.get(path, headers=h).status_code == 403, path
    # And cannot write (read-only role + child gate).
    assert client.post("/api/allowance/allocations", json={"child_id": 1, "amount": "1"}, headers=h).status_code == 403


def _make_member(client, uid="ha-mem", name="Mem") -> int:
    """An approved, non-admin adult member."""
    client.get("/api/users/me", headers=_hdr(uid, name))
    mid = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == uid)
    client.patch(f"/api/users/{mid}", json={"role": "member", "status": "approved"})
    return mid


def test_child_budget_is_admin_only(client):
    """A budget targeting a child (an allowance budget) may only be created, edited
    or removed by an owner/admin — a plain member is blocked (parent/admin gate).
    A shared (non-child) budget stays open to members."""
    kid = _make_child(client)
    mhdr = _hdr("ha-mem", "Mem")
    _make_member(client)

    body = {"name": "Sweets", "amount": "10", "period": "monthly", "owner_user_id": kid}
    # Non-admin member cannot create a child's budget…
    assert client.post("/api/budgets", json=body, headers=mhdr).status_code == 403
    # …but the owner can.
    bid = client.post("/api/budgets", json=body).json()["id"]
    # Member cannot edit or delete it; the owner can.
    assert client.patch(f"/api/budgets/{bid}", json={"amount": "20"}, headers=mhdr).status_code == 403
    assert client.delete(f"/api/budgets/{bid}", headers=mhdr).status_code == 403
    assert client.patch(f"/api/budgets/{bid}", json={"amount": "20"}).status_code == 200
    assert client.delete(f"/api/budgets/{bid}").status_code == 204
    # A shared (no owner) budget is NOT gated — a member can still create one.
    shared = {"name": "Food", "amount": "100", "period": "monthly"}
    assert client.post("/api/budgets", json=shared, headers=mhdr).status_code == 201
