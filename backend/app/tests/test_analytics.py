"""Trends time-series + outlier detection (Stage 12; backlog #146, #150)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

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
    assert s["trend"]["spend"]["pct"] == pytest.approx(50.0)  # (900-600)/600


def test_trend_direction_flat_band_is_symmetric():
    # The flat band must be symmetric around zero: a rise and an equal-magnitude
    # fall are classified consistently (both flat inside the band, up/down once
    # they clear it). Boundary is |pct| < TREND_FLAT_PCT → flat.
    band = analytics_service.TREND_FLAT_PCT

    # Within the band (equal magnitude either side) → both flat.
    assert analytics_service._trend_direction(Decimal("5"), band - 0.1) == "flat"
    assert analytics_service._trend_direction(Decimal("-5"), -(band - 0.1)) == "flat"

    # Exactly at the band edge is no longer flat (strict <) — up vs down mirror.
    assert analytics_service._trend_direction(Decimal("5"), band) == "up"
    assert analytics_service._trend_direction(Decimal("-5"), -band) == "down"

    # Clearly outside the band → up vs down mirror.
    assert analytics_service._trend_direction(Decimal("50"), band + 5) == "up"
    assert analytics_service._trend_direction(Decimal("-50"), -(band + 5)) == "down"

    # No baseline (pct is None): only an exact-zero delta is flat, any move is real.
    assert analytics_service._trend_direction(Decimal("1"), None) == "up"
    assert analytics_service._trend_direction(Decimal("-1"), None) == "down"
    assert analytics_service._trend_direction(Decimal("0"), None) == "flat"


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
    # The detail labels the amount with the base-currency symbol (was bare before).
    assert "£500.00" in large[0]["detail"]


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


def _reference_series(db, ref, months):
    """Old row-by-row per-month accumulation, kept here so the GROUP BY path can
    be proven equivalent to the pre-optimisation behaviour."""
    out = []
    for start, end in analytics_service._month_windows(ref, months):
        spend = Decimal("0.00")
        income = Decimal("0.00")
        for txn in analytics_service._spendable(db, start, end):
            amt = txn.base_amount or Decimal("0")
            if amt < 0:
                spend += -amt
            else:
                income += amt
        out.append((start.isoformat()[:7], spend, income))
    return out


def test_monthly_series_groupby_matches_per_month_with_empty_month(db):
    # Dataset spanning four months with January left completely empty, plus
    # fractional amounts to exercise 2-dp handling.
    _credit(db, date(2025, 12, 3), "1000")
    _debit(db, date(2025, 12, 4), "-200")
    _debit(db, date(2025, 12, 20), "-50")
    # January 2026: no transactions at all (the empty month).
    _credit(db, date(2026, 2, 5), "1500")
    _debit(db, date(2026, 2, 6), "-300.25")
    _debit(db, date(2026, 3, 9), "-125.50")
    _credit(db, date(2026, 3, 10), "800")
    db.commit()

    result = analytics_service.monthly_series(db, REF, months=4)
    months = result["months"]
    assert [m["month"] for m in months] == ["2025-12", "2026-01", "2026-02", "2026-03"]

    # The GROUP BY pass must equal the naive per-month accumulation, month for
    # month (Decimal compares — no float ==).
    reference = {ym: (sp, inc) for ym, sp, inc in _reference_series(db, REF, months=4)}
    for m in months:
        exp_spend, exp_income = reference[m["month"]]
        assert Decimal(m["spend"]) == exp_spend
        assert Decimal(m["income"]) == exp_income
        assert Decimal(m["net"]) == exp_income - exp_spend

    empty = next(m for m in months if m["month"] == "2026-01")
    assert Decimal(empty["spend"]) == Decimal("0")
    assert Decimal(empty["income"]) == Decimal("0")


def test_new_merchant_normalises_trivial_text_variations(db):
    # Prior month has "Tesco"; the current-month "  TESCO " (case + surrounding
    # whitespace) must fold to the same key and not read as brand-new, while a
    # genuinely new merchant still surfaces.
    _debit(db, date(2026, 2, 9), "-50", merchant="Tesco")
    _debit(db, date(2026, 3, 9), "-50", merchant="  TESCO ")
    _debit(db, date(2026, 3, 10), "-40", merchant="NewShop")
    db.commit()

    res = analytics_service.outliers(db, REF)
    new = [i for i in res["items"] if i["type"] == "new_merchant"]
    assert len(new) == 1
    assert "NewShop" in new[0]["title"]


def test_outliers_single_pass_across_lookback_window(db):
    # A baseline of typical small charges spread across the lookback window, one
    # large outlier this month, and a merchant seen only in the prior window.
    for m in (1, 2, 3):
        for i in range(3):
            _debit(db, date(2026, m, 3 + i), "-20")
    _debit(db, date(2026, 3, 25), "-500")                       # the outlier
    _debit(db, date(2025, 12, 5), "-15", merchant="OldMerch")   # prior-window merchant
    _debit(db, date(2026, 3, 26), "-30", merchant="OldMerch")   # seen before → not new
    _debit(db, date(2026, 3, 27), "-45", merchant="FreshCo")    # genuinely new
    db.commit()

    res = analytics_service.outliers(db, REF)
    large = [i for i in res["items"] if i["type"] == "large_charge"]
    new = [i for i in res["items"] if i["type"] == "new_merchant"]
    assert len(large) == 1
    assert Decimal(large[0]["amount"]) == Decimal("500")
    assert [i["title"] for i in new if "FreshCo" in i["title"]]
    assert not [i for i in new if "OldMerch" in i["title"]]


def test_no_false_positives_without_history(db):
    # A single month with a handful of charges and no prior data → nothing flagged.
    for i in range(4):
        _debit(db, date(2026, 3, 5 + i), "-30", merchant=f"Shop{i}")
    db.commit()

    res = analytics_service.outliers(db, REF)
    assert res["items"] == []
