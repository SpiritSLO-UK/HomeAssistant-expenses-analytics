"""Manual transaction entry (POST /api/transactions).

The third way a transaction can come into existence, alongside a statement import
and a receipt. Covers the sign convention (a debit is money out, a credit is money
in), the shared "Cash & receipts" fallback account, FX + auto-categorisation
running the same way they do for an imported row, and the account-scope guard.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Account, Transaction


def _cat(client, name: str) -> int:
    return next(c["id"] for c in client.get("/api/categories").json() if c["name"] == name)


def _post(client, **payload):
    body = {"description": "CASH COFFEE", "amount": "3.50", "new_account": True}
    body.update(payload)
    return client.post("/api/transactions", json=body)


def _member(client, uid: str, name: str) -> int:
    client.get("/api/users/me", headers={"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name})
    row = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == uid)
    client.patch(f"/api/users/{row}", json={"role": "member", "status": "approved"})
    return row


# --- the happy paths ---


def test_manual_expense_is_money_out(client):
    """A debit is stored negative so it counts as spend, in the shared cash account."""
    res = _post(client, description="GROCERIES", amount="120.25", transaction_date="2026-05-04")
    assert res.status_code == 201, res.text
    txn = res.json()
    assert Decimal(txn["amount"]) == Decimal("-120.25")
    assert txn["direction"] == "debit"
    assert txn["is_income"] is False
    assert txn["description_raw"] == "GROCERIES"
    assert txn["transaction_date"] == "2026-05-04"
    # Base currency defaults to GBP, so it converts 1:1 rather than awaiting a rate.
    assert Decimal(txn["base_amount"]) == Decimal("-120.25")
    assert txn["needs_rate"] is False
    # ...into the dedicated account the receipt path also uses.
    account = next(a for a in client.get("/api/accounts").json() if a["id"] == txn["account_id"])
    assert account["name"] == "Cash & receipts"
    # ...and it's an ordinary transaction from here on.
    assert any(t["id"] == txn["id"] for t in client.get("/api/transactions").json()["items"])


def test_manual_income_is_money_in(client):
    """A credit is stored positive + flagged, so it lands in income and not spend."""
    res = _post(client, description="SALARY", amount="1500.00", direction="credit",
                transaction_date="2026-05-04")
    assert res.status_code == 201, res.text
    txn = res.json()
    assert Decimal(txn["amount"]) == Decimal("1500.00")
    assert txn["direction"] == "credit"
    assert txn["is_income"] is True

    summary = client.get("/api/dashboard/summary", params={"month": "2026-05-01"}).json()
    assert summary["income_this_month"] == "1500.00"
    assert summary["spend_this_month"] == "0"


def test_expense_and_income_net_off_in_the_summary(client):
    """The dashboard reads the sign, so a manual pair nets out like imported rows."""
    _post(client, description="SALARY", amount="1000.00", direction="credit", transaction_date="2026-05-04")
    _post(client, description="RENT", amount="400.00", transaction_date="2026-05-05")
    summary = client.get("/api/dashboard/summary", params={"month": "2026-05-01"}).json()
    assert summary["income_this_month"] == "1000.00"
    assert summary["spend_this_month"] == "400.00"
    assert summary["net_this_month"] == "600.00"


def test_chosen_category_is_kept_as_a_manual_choice(client):
    groceries = _cat(client, "Groceries")
    txn = _post(client, description="MARKET STALL", category_id=groceries).json()
    assert txn["category_id"] == groceries
    # Manual picks are full-confidence (spec §15.2) so a later re-apply leaves them
    # alone unless the user opts into overriding manual choices.
    assert txn["confidence_score"] == 1.0


def test_no_category_falls_back_to_the_rules_engine(client):
    """Omitting the category runs the same auto-categorisation an import would."""
    groceries = _cat(client, "Groceries")
    client.post("/api/rules", json={
        "condition_type": "description_contains",
        "condition_value": "ZZQ",
        "action_type": "set_category",
        "action_value": str(groceries),
    })
    txn = _post(client, description="ZZQ CORNER SHOP").json()
    assert txn["category_id"] == groceries


def test_no_category_and_no_match_stays_uncategorised(client):
    txn = _post(client, description="QQXZ UNKNOWN THING").json()
    assert txn["category_id"] is None


def test_date_defaults_to_today(client):
    txn = _post(client).json()
    assert txn["transaction_date"] == datetime.now(UTC).date().isoformat()


def test_existing_account_can_be_chosen(client):
    with SessionLocal() as db:
        account = Account(name="Wallet", account_type="current_account", currency="GBP")
        db.add(account)
        db.commit()
        account_id = account.id
    txn = _post(client, new_account=False, account_id=account_id).json()
    assert txn["account_id"] == account_id


def test_identical_entries_both_land(client):
    """Two of the same coffee on one day are two transactions, not a duplicate.

    Guards the dedup-key choice: the import hash is derived from
    account|date|amount|currency|description, so a content-derived hash here would
    have collapsed these two (and could later mask a real statement row).
    """
    first = _post(client, transaction_date="2026-05-04").json()
    second = _post(client, transaction_date="2026-05-04").json()
    assert first["id"] != second["id"]
    assert first["is_duplicate"] is False and second["is_duplicate"] is False
    items = client.get("/api/transactions").json()["items"]
    assert len([t for t in items if t["description_raw"] == "CASH COFFEE"]) == 2


def test_foreign_currency_awaits_a_rate(client):
    """A non-base currency goes through the same FX path as an imported row."""
    txn = _post(client, description="TAPAS MADRID", amount="30.00", currency="eur").json()
    assert txn["currency"] == "EUR"  # normalised
    assert txn["needs_rate"] is True
    assert txn["base_amount"] is None


# --- validation ---


def test_account_must_be_chosen(client):
    res = client.post("/api/transactions", json={"description": "X", "amount": "1.00"})
    assert res.status_code == 400
    assert "account" in res.json()["detail"].lower()


def test_blank_description_is_rejected(client):
    assert _post(client, description="   ").status_code == 400


def test_non_positive_amount_is_rejected(client):
    """Amount is a magnitude; the sign comes from direction, so 0 and -x are errors."""
    assert _post(client, amount="0").status_code == 422
    assert _post(client, amount="-5.00").status_code == 422


def test_unknown_direction_is_rejected(client):
    assert _post(client, direction="sideways").status_code == 422


def test_unknown_category_is_rejected(client):
    res = _post(client, category_id=999999)
    assert res.status_code == 400
    assert res.json()["detail"] == "Unknown category"
    # Nothing was written for the rejected request.
    assert client.get("/api/transactions").json()["total"] == 0


# --- account scope (#18) ---


def test_rejects_out_of_scope_account(client):
    """A member must not inject a transaction into another member's PRIVATE account.

    Mirrors the receipt path's guard: validated before any write, 404 rather than
    403 so the account's existence isn't leaked.
    """
    client.get("/api/users/me")  # owner bootstrap
    _member(client, "ha-alice", "Alice")
    bob = _member(client, "ha-bob", "Bob")
    with SessionLocal() as db:
        bob_priv = Account(name="Bob Private", account_type="current_account", currency="GBP",
                           owner_user_id=bob, is_shared=False)
        db.add(bob_priv)
        db.commit()
        bob_priv_id = bob_priv.id

    alice = {"X-Remote-User-Id": "ha-alice", "X-Remote-User-Display-Name": "Alice"}
    res = client.post(
        "/api/transactions",
        json={"description": "SNEAKY", "amount": "5.00", "account_id": bob_priv_id},
        headers=alice,
    )
    assert res.status_code == 404
    with SessionLocal() as db:
        assert db.scalars(select(Transaction).where(Transaction.account_id == bob_priv_id)).first() is None


def test_owner_can_target_any_account(client):
    """Regression guard: the guard narrows members only, never the owner."""
    with SessionLocal() as db:
        account = Account(name="Joint", account_type="current_account", currency="GBP")
        db.add(account)
        db.commit()
        account_id = account.id
    res = client.post(
        "/api/transactions",
        json={"description": "GROCERIES", "amount": "9.99", "account_id": account_id},
    )
    assert res.status_code == 201, res.text
    assert res.json()["account_id"] == account_id
