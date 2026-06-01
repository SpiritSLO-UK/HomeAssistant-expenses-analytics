"""Trends time-series + outlier detection (Stage 12; backlog #146, #150)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models import Budget, Category, Transaction
from app.services import analytics_service

REF = date(2026, 3, 15)  # tests bucket data into Jan/Feb/Mar 2026


def _debit(db, day, amount, *, merchant=None, category_id=None, desc="spend"):
    a = Decimal(amount)  # negative
    db.add(Transaction(description_raw=desc, merchant_raw=merchant, amount=a, base_amount=a,
                       currency="GBP", direction="debit", transaction_date=day,
                       category_id=category_id))


def _credit(db, day, amount):
    a = Decimal(amount)
    db.add(Transaction(description_raw="income", amount=a, base_amount=a, currency="GBP",
                       direction="credit", transaction_date=day))


def test_monthly_series_and_trend(db):
    for m, spend in ((1, "-500"), (2, "-600"), (3, "-900")):
        _credit(db, date(2026, m, 10), "2000")
        _debit(db, date(2026, m, 11), spend)
    db.commit()

    s = analytics_service.monthly_series(db, REF, months=3)
    assert [p["month"] for p in s["months"]] == ["2026-01", "2026-02", "2026-03"]
    assert Decimal(s["months"][-1]["spend"]) == Decimal("900")
    assert Decimal(s["months"][-1]["net"]) == Decimal("1100")  # 2000 - 900
    # March spend (900) is up vs February (600); net (1100) is down vs (1400).
    assert s["trend"]["spend"]["direction"] == "up"
    assert s["trend"]["net"]["direction"] == "down"
    assert s["trend"]["spend"]["pct"] == 50.0  # (900-600)/600


def test_large_charge_is_flagged(db):
    # Enough small charges to establish a "typical" baseline (median ~20)…
    for i in range(5):
        _debit(db, date(2026, 2, 5 + i), "-20")
    for i in range(4):
        _debit(db, date(2026, 3, 5 + i), "-20")
    _debit(db, date(2026, 3, 20), "-500")  # the outlier
    db.commit()

    res = analytics_service.outliers(db, REF)
    large = [i for i in res["items"] if i["type"] == "large_charge"]
    assert len(large) == 1
    assert Decimal(large[0]["amount"]) == Decimal("500")
    assert large[0]["severity"] == "warn"


def test_category_spike_is_flagged(db):
    cat = Category(name="Groceries")
    db.add(cat)
    db.flush()
    _debit(db, date(2026, 1, 9), "-100", category_id=cat.id)
    _debit(db, date(2026, 2, 9), "-100", category_id=cat.id)
    _debit(db, date(2026, 3, 9), "-300", category_id=cat.id)  # 3× the ~100 average
    db.commit()

    res = analytics_service.outliers(db, REF)
    spikes = [i for i in res["items"] if i["type"] == "category_spike"]
    assert len(spikes) == 1
    assert spikes[0]["category_id"] == cat.id
    assert "Groceries" in spikes[0]["title"]


def test_new_merchant_is_flagged(db):
    _debit(db, date(2026, 2, 9), "-50", merchant="Tesco")  # seen before
    _debit(db, date(2026, 3, 9), "-50", merchant="Tesco")  # not new
    _debit(db, date(2026, 3, 10), "-40", merchant="NewShop")  # new this month
    db.commit()

    res = analytics_service.outliers(db, REF)
    new = [i for i in res["items"] if i["type"] == "new_merchant"]
    assert len(new) == 1
    assert "NewShop" in new[0]["title"]


def test_budget_over_is_flagged(db):
    cat = Category(name="Food")
    db.add(cat)
    db.flush()
    db.add(Budget(name="Food", amount=Decimal("100"), period="monthly", category_id=cat.id))
    _debit(db, date(2026, 3, 9), "-150", category_id=cat.id)  # 150% of the £100 budget
    db.commit()

    res = analytics_service.outliers(db, REF)
    budgets = [i for i in res["items"] if i["type"] == "budget"]
    assert len(budgets) == 1
    assert budgets[0]["severity"] == "warn"  # over


def test_no_false_positives_without_history(db):
    # A single month with a handful of charges and no prior data → nothing flagged.
    for i in range(4):
        _debit(db, date(2026, 3, 5 + i), "-30", merchant=f"Shop{i}")
    db.commit()

    res = analytics_service.outliers(db, REF)
    assert res["items"] == []
