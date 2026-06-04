"""Business / VAT expenses (backlog: corporate receipts).

The `is_business` flag + per-transaction VAT, the business summary (totals +
reclaimable VAT, base-currency converted), receipt→txn VAT propagation, the CSV
columns, and the list filter. Base currency forced to GBP.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.db import session as dbsession
from app.models import Transaction
from app.services import business_service, export_service, receipt_service, settings_service


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _txn(
    db,
    *,
    amount="-100.00",
    base=None,
    vat=None,
    business=False,
    currency="GBP",
    fx_rate=None,
    category_id=None,
    transfer=False,
    archived=False,
    txn_date=None,
) -> Transaction:
    amt = Decimal(amount)
    t = Transaction(
        transaction_date=txn_date or date.today(),
        description_raw="biz",
        amount=amt,
        currency=currency,
        direction="debit" if amt < 0 else "credit",
        base_amount=Decimal(base) if base is not None else amt,
        fx_rate=Decimal(fx_rate) if fx_rate is not None else None,
        is_business=business,
        vat_amount=Decimal(vat) if vat is not None else None,
        is_transfer=transfer,
        category_id=category_id,
        archived_at=_now() if archived else None,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_summary_totals_and_vat(db):
    settings_service.set_value(db, settings_service.BASE_CURRENCY, "GBP")
    _txn(db, business=True, amount="-100.00", vat="20.00")
    _txn(db, business=True, amount="-50.00", vat="10.00")
    _txn(db, business=False, amount="-30.00")  # personal → excluded
    s = business_service.summary(db)
    assert s["total"] == "150.00"
    assert s["vat"] == "30.00"
    assert s["transaction_count"] == 2


def test_summary_year_scope(db):
    """`year` scopes the business summary to one calendar year (Budgets-style date
    scope); omitting it is all-time."""
    settings_service.set_value(db, settings_service.BASE_CURRENCY, "GBP")
    _txn(db, business=True, amount="-100.00", txn_date=date(2024, 6, 1))
    _txn(db, business=True, amount="-40.00", txn_date=date(2026, 6, 1))
    assert business_service.summary(db)["total"] == "140.00"          # all-time
    assert business_service.summary(db, year=2026)["total"] == "40.00"
    assert business_service.summary(db, year=2024)["total"] == "100.00"
    assert business_service.summary(db, year=2030)["transaction_count"] == 0


def test_summary_groups_by_period_with_bounds(db):
    settings_service.set_value(db, settings_service.BASE_CURRENCY, "GBP")
    _txn(db, business=True, amount="-100.00", vat="20.00")  # today
    s = business_service.summary(db, period="month")
    assert s["period"] == "month"
    assert len(s["by_period"]) == 1
    b = s["by_period"][0]
    assert {"period", "label", "start", "end", "total", "vat", "count"} <= set(b)
    assert b["total"] == "100.00"
    assert b["vat"] == "20.00"
    assert b["count"] == 1
    # The bucket bounds bracket the transaction's date so the UI can drill into
    # the transactions list by date range.
    assert b["start"] <= date.today().isoformat() <= b["end"]
    # An unknown period falls back to month (never errors).
    assert business_service.summary(db, period="bogus")["period"] == "month"


def test_excludes_personal_transfer_and_archived(db):
    _txn(db, business=True, amount="-100.00", vat="20.00")   # counted
    _txn(db, business=True, amount="-200.00", transfer=True)  # transfer → excluded
    _txn(db, business=True, amount="-300.00", archived=True)  # archived → excluded
    _txn(db, business=False, amount="-40.00")                 # personal → excluded
    s = business_service.summary(db)
    assert s["total"] == "100.00"
    assert s["transaction_count"] == 1


def test_vat_converted_to_base_via_fx_rate(db):
    # EUR txn: 120 EUR spend, 20 EUR VAT, rate 0.85 → 102 GBP spend, 17 GBP VAT.
    _txn(db, business=True, currency="EUR", amount="-120.00", base="-102.00", fx_rate="0.85", vat="20.00")
    s = business_service.summary(db)
    assert s["total"] == "102.00"
    assert s["vat"] == "17.00"


def test_receipt_match_propagates_vat(db):
    r, _ = receipt_service.store_upload(db, "biz-receipt.jpg", b"biz-receipt-bytes")
    r.vat_amount = Decimal("4.20")
    db.commit()
    txn = _txn(db, business=True, amount="-25.00")
    assert txn.vat_amount is None
    receipt_service.confirm_match(db, r, txn.id)
    db.refresh(txn)
    assert txn.vat_amount == Decimal("4.20")


def test_csv_includes_business_and_vat_columns(db):
    _txn(db, business=True, amount="-100.00", vat="20.00")
    conditions = export_service.build_transaction_filters()
    csv_text = export_service.transactions_csv(db, conditions)
    header = csv_text.splitlines()[0]
    assert "is_business" in header
    assert "vat_amount" in header
    assert "20.00" in csv_text
    assert "True" in csv_text  # the business row


# --- API ------------------------------------------------------------------

def _seed_business() -> None:
    with dbsession.SessionLocal() as s:
        settings_service.set_value(s, settings_service.BASE_CURRENCY, "GBP")
        t = Transaction(
            transaction_date=date.today() - timedelta(days=1),
            description_raw="biz-api",
            amount=Decimal("-60.00"),
            currency="GBP",
            direction="debit",
            base_amount=Decimal("-60.00"),
            is_business=True,
            vat_amount=Decimal("10.00"),
        )
        s.add(t)
        s.commit()


def test_business_api_and_filter(client):
    client.get("/api/users/me")  # owner
    _seed_business()

    summary = client.get("/api/business/summary").json()
    assert summary["total"] == "60.00"
    assert summary["vat"] == "10.00"
    assert summary["transaction_count"] == 1

    # The list filter narrows to business rows.
    assert client.get("/api/transactions", params={"is_business": "true"}).json()["total"] == 1
