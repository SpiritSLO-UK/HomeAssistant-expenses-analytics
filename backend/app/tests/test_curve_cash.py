"""Curve Cash (the CPT rewards programme) on import (user ask).

Earned Curve Cash ("Curve Cash: <merchant>", CPT only) is money in, forced to the
Cashback income category. Redeemed Curve Cash (a real merchant paid from the
wallet, with a GBP Foreign Spend) is a normal spend.
"""

from __future__ import annotations

EXPORT = (
    b"Export Format,Date (YYYY-MM-DD as UTC),Time (HH:MM:SS as UTC),Merchant,"
    b"Txn Amount (Funding Card),Txn Currency (Funding Card),"
    b"Txn Amount (Foreign Spend),Txn Currency (Foreign Spend),Card Name,"
    b"Card Last 4 Digits,Type,Category,Notes,Fees\n"
    b"CSV,2025-07-24,12:23:09,Curve Cash: Lidl,50,CPT,,,Curve Cash,,,,,\n"
    b"CSV,2025-07-23,11:44:54,Bexley Ringo Ecom,180,CPT,1.8,GBP,Curve Cash,,Personal,Travel,,\n"
    b"CSV,2025-07-20,21:14:46,Kwik Save,3.69,GBP,,,Credit Card,1006,Personal,Groceries,,\n"
)


def _import(client):
    acc = client.post("/api/accounts", json={"name": "Curve", "account_type": "credit_card"}).json()["id"]
    body = client.post(
        "/api/imports/upload",
        files={"file": ("curve.csv", EXPORT, "text/csv")},
        data={"account_id": str(acc)},
    ).json()
    client.post(f"/api/imports/{body['import_id']}/confirm")
    return acc


def test_curve_cash_import_categorisation(client):
    _import(client)
    cats = {c["name"]: c["id"] for c in client.get("/api/categories").json()}
    assert "Cashback" in cats  # category created on demand from the library

    items = {t["description_raw"]: t for t in client.get("/api/transactions").json()["items"]}

    earned = items["Curve Cash: Lidl"]
    assert earned["direction"] == "credit"
    assert earned["amount"] == "0.50"  # 50 CPT = £0.50
    assert earned["is_income"] is True
    assert earned["category_id"] == cats["Cashback"]

    redeemed = items["Bexley Ringo Ecom"]
    assert redeemed["direction"] == "debit"
    assert redeemed["amount"] == "-1.80"  # paid £1.80 from the wallet
    assert redeemed["is_income"] is False

    # The ordinary funding-card spend is unaffected.
    assert items["Kwik Save"]["amount"] == "-3.69"


def test_curve_cash_counts_as_income(client):
    """Earned cashback lands on the income side of the dashboard, not spend."""
    _import(client)
    # Any date in July 2025 selects that month's window.
    summary = client.get("/api/dashboard/summary", params={"month": "2025-07-15"}).json()
    assert float(summary["income_this_month"]) >= 0.50
