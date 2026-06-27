"""Transaction split tests (spec §17, §12.7, §37.4 — Stage 4)."""

from __future__ import annotations

from decimal import Decimal

from app.models import Transaction, TransactionSplit
from app.services import split_service


def test_split_base_amounts_sum_to_parent_base():
    # A foreign transaction whose per-part rounding would otherwise drift a cent:
    # -10.00 EUR @ 0.855 = -8.55 base, but -3.33/-3.33/-3.34 each round to -2.85/-2.85/-2.86
    # = -8.56. The distributed amounts must instead sum to the parent's -8.55 (SR-A5).
    txn = Transaction(amount=Decimal("-10.00"), fx_rate=Decimal("0.855"), base_amount=Decimal("-8.55"))
    txn.splits = [
        TransactionSplit(amount=Decimal("-3.33")),
        TransactionSplit(amount=Decimal("-3.33")),
        TransactionSplit(amount=Decimal("-3.34")),
    ]
    bases = [split_service.split_base_amount(txn, s) for s in txn.splits]
    assert sum(bases) == txn.base_amount
    assert all(b is not None for b in bases)


def test_split_base_amount_none_without_rate():
    txn = Transaction(amount=Decimal("-10.00"), fx_rate=None, base_amount=None)
    txn.splits = [TransactionSplit(amount=Decimal("-5.00")), TransactionSplit(amount=Decimal("-5.00"))]
    assert split_service.split_base_amount(txn, txn.splits[0]) is None


def _curve(rows: list[tuple[str, str, str]]) -> bytes:
    head = "Date,Description,Amount,Currency,Card,Category\n"
    body = "".join(f"{d},{desc},{amt},GBP,Visa,\n" for d, desc, amt in rows)
    return (head + body).encode()


def _import(client, content: bytes, name: str = "splits.csv"):
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


def _cat_ids(client, n: int = 3) -> list[int]:
    cats = client.get("/api/categories").json()
    return [c["id"] for c in cats[:n]]


# --- happy path ---

def test_split_amazon_across_three_categories(client):
    # Amazon £120 -> 40 / 50 / 30, the spec example (§17.1).
    _import(client, _curve([("2026-05-04", "AMAZON", "-120.00")]))
    txn = _txn(client, "AMAZON")
    home, pets, household = _cat_ids(client, 3)

    res = client.post(
        f"/api/transactions/{txn['id']}/split",
        json={
            "splits": [
                {"amount": "-40.00", "category_id": home},
                {"amount": "-50.00", "category_id": pets},
                {"amount": "-30.00", "category_id": household},
            ]
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_split"] is True
    assert len(body["splits"]) == 3

    # Reflected on the detail endpoint.
    detail = client.get(f"/api/transactions/{txn['id']}").json()
    assert detail["is_split"] is True
    assert sum(float(s["amount"]) for s in detail["splits"]) == -120.0


# --- validation (spec §17.2) ---

def test_split_total_must_match(client):
    _import(client, _curve([("2026-05-04", "TESCO STORES", "-80.00")]))
    txn = _txn(client, "TESCO STORES")
    a, b, _ = _cat_ids(client, 3)
    res = client.post(
        f"/api/transactions/{txn['id']}/split",
        json={"splits": [{"amount": "-60.00", "category_id": a},
                         {"amount": "-12.00", "category_id": b}]},  # totals -72, not -80
    )
    assert res.status_code == 400
    assert "total" in res.json()["detail"].lower()


def test_split_sign_must_be_consistent(client):
    _import(client, _curve([("2026-05-04", "AMAZON", "-120.00")]))
    txn = _txn(client, "AMAZON")
    a, b, _ = _cat_ids(client, 3)
    res = client.post(
        f"/api/transactions/{txn['id']}/split",
        json={"splits": [{"amount": "-140.00", "category_id": a},
                         {"amount": "20.00", "category_id": b}]},  # positive part on a debit
    )
    assert res.status_code == 400


def test_split_needs_category_or_project(client):
    _import(client, _curve([("2026-05-04", "AMAZON", "-120.00")]))
    txn = _txn(client, "AMAZON")
    a, _, _ = _cat_ids(client, 3)
    res = client.post(
        f"/api/transactions/{txn['id']}/split",
        json={"splits": [{"amount": "-60.00", "category_id": a},
                         {"amount": "-60.00"}]},  # second has neither
    )
    assert res.status_code == 400


def test_split_needs_at_least_two_parts(client):
    _import(client, _curve([("2026-05-04", "AMAZON", "-120.00")]))
    txn = _txn(client, "AMAZON")
    a, _, _ = _cat_ids(client, 3)
    res = client.post(
        f"/api/transactions/{txn['id']}/split",
        json={"splits": [{"amount": "-120.00", "category_id": a}]},
    )
    assert res.status_code == 400


# --- clear ---

def test_clear_splits(client):
    _import(client, _curve([("2026-05-04", "AMAZON", "-120.00")]))
    txn = _txn(client, "AMAZON")
    a, b, _ = _cat_ids(client, 3)
    client.post(
        f"/api/transactions/{txn['id']}/split",
        json={"splits": [{"amount": "-60.00", "category_id": a},
                         {"amount": "-60.00", "category_id": b}]},
    )
    res = client.delete(f"/api/transactions/{txn['id']}/split")
    assert res.status_code == 200
    assert res.json()["is_split"] is False
    assert client.get(f"/api/transactions/{txn['id']}").json()["splits"] == []


# --- re-splitting replaces, doesn't append ---

def test_resplit_replaces_previous(client):
    _import(client, _curve([("2026-05-04", "AMAZON", "-120.00")]))
    txn = _txn(client, "AMAZON")
    a, b, c = _cat_ids(client, 3)
    client.post(
        f"/api/transactions/{txn['id']}/split",
        json={"splits": [{"amount": "-60.00", "category_id": a},
                         {"amount": "-60.00", "category_id": b}]},
    )
    res = client.post(
        f"/api/transactions/{txn['id']}/split",
        json={"splits": [{"amount": "-30.00", "category_id": a},
                         {"amount": "-40.00", "category_id": b},
                         {"amount": "-50.00", "category_id": c}]},
    )
    assert len(res.json()["splits"]) == 3


# --- dashboard reflects splits (spec §37.4) ---

def test_dashboard_category_breakdown_uses_splits(client):
    _import(client, _curve([("2026-05-04", "AMAZON", "-120.00")]))
    txn = _txn(client, "AMAZON")
    home, pets, household = _cat_ids(client, 3)
    client.post(
        f"/api/transactions/{txn['id']}/split",
        json={
            "splits": [
                {"amount": "-40.00", "category_id": home},
                {"amount": "-50.00", "category_id": pets},
                {"amount": "-30.00", "category_id": household},
            ]
        },
    )
    rows = client.get("/api/dashboard/categories?month=2026-05-01").json()
    by_cat = {r["category_id"]: r for r in rows}
    assert by_cat[home]["total"] == "40.00"
    assert by_cat[pets]["total"] == "50.00"
    assert by_cat[household]["total"] == "30.00"

    # Monthly spend total is unchanged by splitting (parts sum to the whole).
    summary = client.get("/api/dashboard/summary?month=2026-05-01").json()
    assert summary["spend_this_month"] == "120.00"
