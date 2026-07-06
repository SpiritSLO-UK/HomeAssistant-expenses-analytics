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

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from statistics import median

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Transaction
from app.services import budget_service, dashboard_service, settings_service, subscription_service
from app.services.scope import account_scope_condition, archived_condition

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


# Symbols for the common world currencies, used to label money amounts in the
# heads-up details (an unknown code falls back to a "12.34 CODE" form).
_CURRENCY_SYMBOLS = {
    "GBP": "£", "USD": "$", "EUR": "€", "JPY": "¥", "CNY": "¥",
    "AUD": "A$", "CAD": "C$", "CHF": "CHF ", "HKD": "HK$", "SGD": "S$", "NZD": "NZ$",
}


def _money(value: Decimal | str | float, currency: str) -> str:
    """Format a money amount with the base-currency symbol (or code) for display."""
    amount = _two_dp(Decimal(str(value)))
    symbol = _CURRENCY_SYMBOLS.get(currency.upper())
    return f"{symbol}{amount}" if symbol else f"{amount} {currency.upper()}"


def _month_windows(ref: date, n: int) -> list[tuple[date, date]]:
    """The [start, end) windows for the n months ending with ref's month, oldest first."""
    # Defensive clamp: never build an unbounded series from a caller-supplied
    # count (5 years is well beyond any view). Routes already cap their inputs;
    # this guards the function regardless of caller.
    n = max(0, min(n, 60))
    windows: list[tuple[date, date]] = []
    year, month = ref.year, ref.month
    for _ in range(n):
        windows.append(dashboard_service.month_bounds(date(year, month, 1)))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return list(reversed(windows))


def _spendable(
    db: Session, start: date, end: date, *, debits_only: bool = False,
    account_ids: set[int] | None = None,
) -> list[Transaction]:
    conditions = [
        Transaction.transaction_date >= start,
        Transaction.transaction_date < end,
        Transaction.is_transfer.is_(False),
        Transaction.is_duplicate.is_(False),
        Transaction.base_amount.is_not(None),
        *account_scope_condition(account_ids),
        *archived_condition(),
    ]
    if debits_only:
        conditions.append(Transaction.base_amount < 0)
    return list(db.scalars(select(Transaction).where(*conditions)).all())


def _amt(txn: Transaction) -> Decimal:
    """A transaction's base amount as a non-optional Decimal. ``_spendable`` filters
    ``base_amount IS NOT NULL``, so this is always set; the ``or`` keeps the type
    checker happy (and is a harmless guard)."""
    return txn.base_amount or Decimal("0")


def _month_totals_by_key(
    db: Session, start: date, end: date, *, account_ids: set[int] | None = None
) -> dict[str, tuple[Decimal, Decimal]]:
    """One GROUP-BY pass over ``[start, end)`` returning per-month ``(spend,
    income)`` keyed by ``YYYY-MM``. Replaces a per-month query and matches the
    old row-by-row Decimal accumulation (transfers/duplicates/no-rate excluded,
    account-scoped and archived-excluded)."""
    month = func.strftime("%Y-%m", Transaction.transaction_date)
    spend_sum = func.sum(case((Transaction.base_amount < 0, -Transaction.base_amount), else_=0))
    income_sum = func.sum(case((Transaction.base_amount >= 0, Transaction.base_amount), else_=0))
    conditions = [
        Transaction.transaction_date >= start,
        Transaction.transaction_date < end,
        Transaction.is_transfer.is_(False),
        Transaction.is_duplicate.is_(False),
        Transaction.base_amount.is_not(None),
        *account_scope_condition(account_ids),
        *archived_condition(),
    ]
    rows = db.execute(
        select(month, spend_sum, income_sum).where(*conditions).group_by(month)
    ).all()
    return {r[0]: (_two_dp(Decimal(str(r[1] or 0))), _two_dp(Decimal(str(r[2] or 0)))) for r in rows}


# --- Trends -----------------------------------------------------------------

# A month-over-month change smaller than this (in %) reads as "flat" rather than
# up/down. The same rounded percentage drives both the arrow and the displayed
# figure, so they never disagree at the boundary.
TREND_FLAT_PCT = 1.0


def _trend_direction(delta: Decimal, pct: float | None) -> str:
    # No prior baseline to take a percentage against: only an exact-zero delta is
    # flat; any movement off a zero base is a genuine change.
    if pct is None:
        if delta > 0:
            return "up"
        if delta < 0:
            return "down"
        return "flat"
    if abs(pct) < TREND_FLAT_PCT:
        return "flat"
    return "up" if delta > 0 else "down"


def _trend_entry(current: dict, previous: dict, key: str) -> dict:
    cur = Decimal(current[key])
    prev = Decimal(previous[key])
    delta = cur - prev
    pct = round(float(delta / abs(prev) * 100), 1) if prev != 0 else None
    return {
        "current": str(cur),
        "previous": str(prev),
        "delta": str(delta),
        "pct": pct,
        "direction": _trend_direction(delta, pct),
    }


def monthly_series(db: Session, ref: date, months: int = 6, *, account_ids: set[int] | None = None) -> dict:
    windows = _month_windows(ref, months)
    series = []
    if windows:
        totals = _month_totals_by_key(db, windows[0][0], windows[-1][1], account_ids=account_ids)
        for start, _end in windows:
            key = start.isoformat()[:7]
            spend, income = totals.get(key, (Decimal("0.00"), Decimal("0.00")))
            series.append(
                {
                    "month": key,
                    "spend": str(spend),
                    "income": str(income),
                    "net": str(income - spend),
                }
            )

    trend: dict[str, dict] = {}
    if len(series) >= 2:
        current, previous = series[-1], series[-2]
        for key in ("spend", "income", "net"):
            trend[key] = _trend_entry(current, previous, key)

    return {"currency": settings_service.get_base_currency(db), "months": series, "trend": trend}


# --- Outliers ----------------------------------------------------------------


def _txn_label(txn: Transaction) -> str:
    label = (txn.merchant_raw or txn.description_raw or "").strip()
    return (label[:48] or "transaction")


_WHITESPACE_RUN = re.compile(r"\s+")


def _normalise_merchant(raw: str) -> str:
    """Fold trivial text variations (case + surrounding/repeated whitespace) so
    e.g. ``"Tesco"``, ``"TESCO "`` and ``"Tesco  "`` map to one key and don't
    each look like a brand-new merchant."""
    return _WHITESPACE_RUN.sub(" ", raw.strip()).casefold()


def _merchant_key(txn: Transaction) -> str | None:
    if txn.merchant_id is not None:
        return f"id:{txn.merchant_id}"
    if txn.merchant_raw:
        norm = _normalise_merchant(txn.merchant_raw)
        if norm:
            return f"raw:{norm}"
    return None


def _large_charges(
    db: Session, debits: list[Transaction], cur_start: date, cur_end: date, lb_start: date,
) -> list[dict]:
    """Flag unusually large charges. ``debits`` is a pre-fetched superset scan;
    the baseline window ``[lb_start, cur_end)`` is filtered in Python so we don't
    re-query a range that overlaps the other detectors."""
    window = [t for t in debits if lb_start <= t.transaction_date < cur_end]
    if len(window) < MIN_DEBITS_FOR_BASELINE:
        return []
    med = median(float(-_amt(t)) for t in window)
    if med <= 0:
        return []
    threshold = max(float(LARGE_CHARGE_FLOOR), med * LARGE_CHARGE_MULTIPLE)

    flagged = [
        (-_amt(t), t)
        for t in window
        if cur_start <= t.transaction_date < cur_end and float(-_amt(t)) >= threshold
    ]
    flagged.sort(key=lambda pair: pair[0], reverse=True)

    currency = settings_service.get_base_currency(db)
    items = []
    for amount, txn in flagged[:MAX_PER_DETECTOR]:
        items.append(
            {
                "type": "large_charge",
                "severity": "warn",
                "title": f"Large charge: {_txn_label(txn)}",
                "detail": f"{_money(amount, currency)} — about {float(amount) / med:.1f}× your typical charge",
                "amount": str(_two_dp(amount)),
                "transaction_id": txn.id,
            }
        )
    return items


def _prior_category_totals(
    db: Session, ref: date, history_months: int, *, account_ids: set[int] | None = None
) -> tuple[dict[int | None, list[Decimal]], int]:
    """Per-category prior-month totals (excluding the current month) and the
    number of prior months that had any data."""
    prior_totals: dict[int | None, list[Decimal]] = defaultdict(list)
    months_with_data = 0
    for start, _end in _month_windows(ref, history_months + 1)[:-1]:  # exclude current
        rows = dashboard_service.category_breakdown(db, start, account_ids=account_ids)
        if rows:
            months_with_data += 1
        for r in rows:
            prior_totals[r["category_id"]].append(Decimal(r["total"]))
    return prior_totals, months_with_data


def _category_spike_item(
    cid: int | None, name: str, cur_total: Decimal, prior: list[Decimal], currency: str
) -> dict | None:
    """One category-spike heads-up item, or None when this category isn't a spike."""
    if not prior:
        return None
    avg = sum(prior, Decimal("0")) / len(prior)
    if avg <= 0:
        return None
    if not (cur_total > avg * CATEGORY_SPIKE_MULTIPLE and (cur_total - avg) >= CATEGORY_SPIKE_FLOOR):
        return None
    pct = float((cur_total - avg) / avg * 100)
    return {
        "type": "category_spike",
        "severity": "warn" if cur_total > avg * 2 else "info",
        "title": f"{name} spending is up",
        "detail": (
            f"{_money(cur_total, currency)} this month vs "
            f"{_money(avg, currency)} average ({pct:.0f}% higher)"
        ),
        "amount": str(_two_dp(cur_total)),
        "category_id": cid,
    }


def _category_spikes(
    db: Session, ref: date, history_months: int, *, account_ids: set[int] | None = None
) -> list[dict]:
    current = {
        r["category_id"]: (r["name"], Decimal(r["total"]))
        for r in dashboard_service.category_breakdown(db, ref, account_ids=account_ids)
    }
    prior_totals, months_with_data = _prior_category_totals(
        db, ref, history_months, account_ids=account_ids
    )

    if months_with_data < 2:  # not enough history to call anything a spike
        return []

    currency = settings_service.get_base_currency(db)
    items = []
    for cid, (name, cur_total) in current.items():
        item = _category_spike_item(cid, name, cur_total, prior_totals.get(cid, []), currency)
        if item is not None:
            items.append(item)
    items.sort(key=lambda i: float(i["amount"]), reverse=True)
    return items[:MAX_PER_DETECTOR]


def _new_merchants(
    db: Session, debits: list[Transaction], cur_start: date, cur_end: date,
    prior_start: date, history_months: int,
) -> list[dict]:
    """Flag merchants seen this month but not in the prior window. ``debits`` is a
    pre-fetched superset scan; both windows are filtered in Python rather than
    re-queried."""
    prior = [t for t in debits if prior_start <= t.transaction_date < cur_start]
    if not prior:  # no history → everything would look "new"
        return []
    prior_keys = {_merchant_key(t) for t in prior}

    spend: dict[str, list] = defaultdict(lambda: [Decimal("0.00"), None])
    for txn in debits:
        if not (cur_start <= txn.transaction_date < cur_end):
            continue
        key = _merchant_key(txn)
        if key is None:
            continue
        spend[key][0] += -_amt(txn)
        if spend[key][1] is None:
            spend[key][1] = _txn_label(txn)

    currency = settings_service.get_base_currency(db)
    items = []
    for key, (total, label) in spend.items():
        if key in prior_keys or total < NEW_MERCHANT_FLOOR:
            continue
        items.append(
            {
                "type": "new_merchant",
                "severity": "info",
                "title": f"New merchant: {label}",
                "detail": f"{_money(total, currency)} this month, not seen in the prior {history_months} months",
                "amount": str(_two_dp(total)),
            }
        )
    items.sort(key=lambda i: float(i["amount"]), reverse=True)
    return items[:MAX_PER_DETECTOR]


def _budget_alerts(db: Session, ref: date, *, account_ids: set[int] | None = None) -> list[dict]:
    currency = settings_service.get_base_currency(db)
    items = []
    for b in budget_service.summary(db, ref, account_ids=account_ids):
        if b["status"] == "over":
            items.append(
                {
                    "type": "budget",
                    "severity": "warn",
                    "title": f"Budget over: {b['name']}",
                    "detail": f"{_money(b['spent'], currency)} of {_money(b['amount'], currency)} ({b['percent']}%)",
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
                    "detail": f"{_money(b['spent'], currency)} of {_money(b['amount'], currency)} ({b['percent']}%)",
                    "amount": b["spent"],
                    "budget_id": b["budget_id"],
                }
            )
    return items


def _subscription_alerts(db: Session, *, account_ids: set[int] | None = None) -> list[dict]:
    """Surface upcoming-renewal / missed-payment subscription alerts (spec §20.3)
    in the heads-up card. Always relative to *today* (a "now" concern), regardless
    of the month being viewed."""
    data = subscription_service.alerts(db, account_ids=account_ids)
    currency = settings_service.get_base_currency(db)
    items = []
    for sub in data["overdue"]:
        items.append(
            {
                "type": "subscription",
                "severity": "warn",
                "title": f"Subscription not seen: {sub['name']}",
                "detail": f"{_money(sub['amount'], currency)} was expected {sub['expected_date']} "
                f"({sub['days_overdue']} day(s) ago) — check it wasn't missed or cancelled",
                "amount": sub["amount"],
                "subscription_id": sub["id"],
            }
        )
    for sub in data["upcoming"]:
        when = "due now" if sub["days_until"] <= 0 else f"due in {sub['days_until']} day(s)"
        items.append(
            {
                "type": "subscription",
                "severity": "info",
                "title": f"Subscription {when}: {sub['name']}",
                "detail": f"{_money(sub['amount'], currency)} on {sub['next_expected_date']}",
                "amount": sub["amount"],
                "subscription_id": sub["id"],
            }
        )
    return items


def outliers(
    db: Session, ref: date, *, history_months: int = 3, lookback: int = 6,
    account_ids: set[int] | None = None,
) -> dict:
    cur_start, cur_end = dashboard_service.month_bounds(ref)
    # One debit scan over the widest window any detector needs; the large-charge
    # and new-merchant detectors then filter it in Python instead of each
    # re-querying overlapping date ranges.
    lb_start = _month_windows(ref, lookback)[0][0]
    prior_start = _month_windows(ref, history_months + 1)[0][0]
    debits = _spendable(
        db, min(lb_start, prior_start), cur_end, debits_only=True, account_ids=account_ids
    )

    items: list[dict] = []
    items += _large_charges(db, debits, cur_start, cur_end, lb_start)
    items += _category_spikes(db, ref, history_months, account_ids=account_ids)
    items += _new_merchants(db, debits, cur_start, cur_end, prior_start, history_months)
    items += _budget_alerts(db, ref, account_ids=account_ids)
    items += _subscription_alerts(db, account_ids=account_ids)

    severity_rank = {"warn": 0, "info": 1}
    items.sort(key=lambda i: (severity_rank.get(i["severity"], 2), -float(i.get("amount") or 0)))
    return {
        "month": cur_start.isoformat()[:7],
        "currency": settings_service.get_base_currency(db),
        "items": items,
    }
