"""Trends and outlier detection for the dashboard (backlog #146, #150, #34).

Two read-only analytics over existing data, both in the household **base
currency** and consistent with the dashboard (transfers/duplicates excluded,
split-aware category figures):

- ``monthly_series`` — spend/income/net for the last N months plus a
  month-over-month trend summary (for sparklines + arrows).
- ``outliers`` — a "heads-up" list: unusually large charges, category spend well
  above its recent average, brand-new merchants, and budgets near/over.

Outlier heuristics are deliberately conservative and **gated on having enough
history**, so a fresh install (or first import) doesn't light up with false
positives.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Transaction
from app.services import budget_service, dashboard_service, settings_service

# Outlier thresholds (base-currency units / multipliers).
LARGE_CHARGE_FLOOR = Decimal("50")       # ignore anything below this, however rare
LARGE_CHARGE_MULTIPLE = 3.0              # ... and only flag >= N× the median charge
MIN_DEBITS_FOR_BASELINE = 8             # need this many charges before "typical" means anything
CATEGORY_SPIKE_MULTIPLE = Decimal("1.5")
CATEGORY_SPIKE_FLOOR = Decimal("30")     # the increase must be at least this much money
NEW_MERCHANT_FLOOR = Decimal("20")
MAX_PER_DETECTOR = 3


def _two_dp(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _month_windows(ref: date, n: int) -> list[tuple[date, date]]:
    """The [start, end) windows for the n months ending with ref's month, oldest first."""
    windows: list[tuple[date, date]] = []
    year, month = ref.year, ref.month
    for _ in range(n):
        windows.append(dashboard_service.month_bounds(date(year, month, 1)))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return list(reversed(windows))


def _spendable(db: Session, start: date, end: date, *, debits_only: bool = False) -> list[Transaction]:
    conditions = [
        Transaction.transaction_date >= start,
        Transaction.transaction_date < end,
        Transaction.is_transfer.is_(False),
        Transaction.is_duplicate.is_(False),
        Transaction.base_amount.is_not(None),
    ]
    if debits_only:
        conditions.append(Transaction.base_amount < 0)
    return list(db.scalars(select(Transaction).where(*conditions)).all())


def _month_totals(db: Session, start: date, end: date) -> tuple[Decimal, Decimal]:
    spend = Decimal("0.00")
    income = Decimal("0.00")
    for txn in _spendable(db, start, end):
        if txn.base_amount < 0:
            spend += -txn.base_amount
        else:
            income += txn.base_amount
    return spend, income


# --- Trends -----------------------------------------------------------------


def monthly_series(db: Session, ref: date, months: int = 6) -> dict:
    series = []
    for start, end in _month_windows(ref, months):
        spend, income = _month_totals(db, start, end)
        series.append(
            {
                "month": start.isoformat()[:7],
                "spend": str(spend),
                "income": str(income),
                "net": str(income - spend),
            }
        )

    trend: dict[str, dict] = {}
    if len(series) >= 2:
        current, previous = series[-1], series[-2]
        for key in ("spend", "income", "net"):
            cur = Decimal(current[key])
            prev = Decimal(previous[key])
            delta = cur - prev
            pct = float(delta / abs(prev) * 100) if prev != 0 else None
            if pct is None:
                direction = "up" if delta > 0 else ("down" if delta < 0 else "flat")
            elif abs(pct) < 1:
                direction = "flat"
            else:
                direction = "up" if delta > 0 else "down"
            trend[key] = {
                "current": str(cur),
                "previous": str(prev),
                "delta": str(delta),
                "pct": round(pct, 1) if pct is not None else None,
                "direction": direction,
            }

    return {"currency": settings_service.get_base_currency(db), "months": series, "trend": trend}


# --- Outliers ----------------------------------------------------------------


def _txn_label(txn: Transaction) -> str:
    label = (txn.merchant_raw or txn.description_raw or "").strip()
    return (label[:48] or "transaction")


def _merchant_key(txn: Transaction) -> str | None:
    if txn.merchant_id is not None:
        return f"id:{txn.merchant_id}"
    if txn.merchant_raw:
        return f"raw:{txn.merchant_raw.strip().lower()}"
    return None


def _large_charges(db: Session, cur_start: date, cur_end: date, ref: date, lookback: int) -> list[dict]:
    lb_start = _month_windows(ref, lookback)[0][0]
    debits = _spendable(db, lb_start, cur_end, debits_only=True)
    if len(debits) < MIN_DEBITS_FOR_BASELINE:
        return []
    med = median(float(-t.base_amount) for t in debits)
    if med <= 0:
        return []
    threshold = max(float(LARGE_CHARGE_FLOOR), med * LARGE_CHARGE_MULTIPLE)

    flagged = [
        (-t.base_amount, t)
        for t in debits
        if cur_start <= t.transaction_date < cur_end and float(-t.base_amount) >= threshold
    ]
    flagged.sort(key=lambda pair: pair[0], reverse=True)

    items = []
    for amount, txn in flagged[:MAX_PER_DETECTOR]:
        items.append(
            {
                "type": "large_charge",
                "severity": "warn",
                "title": f"Large charge: {_txn_label(txn)}",
                "detail": f"{_two_dp(amount)} — about {float(amount) / med:.1f}× your typical charge",
                "amount": str(_two_dp(amount)),
                "transaction_id": txn.id,
            }
        )
    return items


def _category_spikes(db: Session, ref: date, history_months: int) -> list[dict]:
    current = {
        r["category_id"]: (r["name"], Decimal(r["total"]))
        for r in dashboard_service.category_breakdown(db, ref)
    }
    prior_totals: dict[int | None, list[Decimal]] = defaultdict(list)
    months_with_data = 0
    for start, _end in _month_windows(ref, history_months + 1)[:-1]:  # exclude current
        rows = dashboard_service.category_breakdown(db, start)
        if rows:
            months_with_data += 1
        for r in rows:
            prior_totals[r["category_id"]].append(Decimal(r["total"]))

    if months_with_data < 2:  # not enough history to call anything a spike
        return []

    items = []
    for cid, (name, cur_total) in current.items():
        prior = prior_totals.get(cid, [])
        if not prior:
            continue
        avg = sum(prior, Decimal("0")) / len(prior)
        if avg <= 0:
            continue
        if cur_total > avg * CATEGORY_SPIKE_MULTIPLE and (cur_total - avg) >= CATEGORY_SPIKE_FLOOR:
            pct = float((cur_total - avg) / avg * 100)
            items.append(
                {
                    "type": "category_spike",
                    "severity": "warn" if cur_total > avg * 2 else "info",
                    "title": f"{name} spending is up",
                    "detail": f"{_two_dp(cur_total)} this month vs {_two_dp(avg)} average ({pct:.0f}% higher)",
                    "amount": str(_two_dp(cur_total)),
                    "category_id": cid,
                }
            )
    items.sort(key=lambda i: float(i["amount"]), reverse=True)
    return items[:MAX_PER_DETECTOR]


def _new_merchants(db: Session, cur_start: date, cur_end: date, ref: date, history_months: int) -> list[dict]:
    prior_start = _month_windows(ref, history_months + 1)[0][0]
    prior = _spendable(db, prior_start, cur_start, debits_only=True)
    if not prior:  # no history → everything would look "new"
        return []
    prior_keys = {_merchant_key(t) for t in prior}

    spend: dict[str, list] = defaultdict(lambda: [Decimal("0.00"), None])
    for txn in _spendable(db, cur_start, cur_end, debits_only=True):
        key = _merchant_key(txn)
        if key is None:
            continue
        spend[key][0] += -txn.base_amount
        if spend[key][1] is None:
            spend[key][1] = _txn_label(txn)

    items = []
    for key, (total, label) in spend.items():
        if key in prior_keys or total < NEW_MERCHANT_FLOOR:
            continue
        items.append(
            {
                "type": "new_merchant",
                "severity": "info",
                "title": f"New merchant: {label}",
                "detail": f"{_two_dp(total)} this month, not seen in the prior {history_months} months",
                "amount": str(_two_dp(total)),
            }
        )
    items.sort(key=lambda i: float(i["amount"]), reverse=True)
    return items[:MAX_PER_DETECTOR]


def _budget_alerts(db: Session, ref: date) -> list[dict]:
    items = []
    for b in budget_service.summary(db, ref):
        if b["status"] == "over":
            items.append(
                {
                    "type": "budget",
                    "severity": "warn",
                    "title": f"Budget over: {b['name']}",
                    "detail": f"{b['spent']} of {b['amount']} ({b['percent']}%)",
                    "amount": b["spent"],
                    "budget_id": b["budget_id"],
                }
            )
        elif b["status"] == "warn":
            items.append(
                {
                    "type": "budget",
                    "severity": "info",
                    "title": f"Budget near limit: {b['name']}",
                    "detail": f"{b['spent']} of {b['amount']} ({b['percent']}%)",
                    "amount": b["spent"],
                    "budget_id": b["budget_id"],
                }
            )
    return items


def outliers(db: Session, ref: date, *, history_months: int = 3, lookback: int = 6) -> dict:
    cur_start, cur_end = dashboard_service.month_bounds(ref)
    items: list[dict] = []
    items += _large_charges(db, cur_start, cur_end, ref, lookback)
    items += _category_spikes(db, ref, history_months)
    items += _new_merchants(db, cur_start, cur_end, ref, history_months)
    items += _budget_alerts(db, ref)

    severity_rank = {"warn": 0, "info": 1}
    items.sort(key=lambda i: (severity_rank.get(i["severity"], 2), -float(i.get("amount") or 0)))
    return {
        "month": cur_start.isoformat()[:7],
        "currency": settings_service.get_base_currency(db),
        "items": items,
    }
