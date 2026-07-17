"""Business / VAT expense analytics (backlog: corporate receipts).

A lens over transactions flagged ``is_business``: total business spend (in the
household base currency), reclaimable **VAT**, and breakdowns by category and
month — for expense claiming. Account-scoped + archived-excluded like every other
aggregate. Read-only (no new model beyond the two transaction columns).
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Transaction
from app.services import settings_service
from app.services.scope import account_scope_condition, archived_condition

_ZERO = Decimal("0.00")
PERIODS = ("day", "week", "month", "year")


def _period_bucket(d: date, period: str) -> tuple[str, str, date, date]:
    """Map a date to its (key, label, start, end) for the requested granularity.
    The start/end let the UI drill straight into the transactions list by date."""
    if period == "day":
        return d.isoformat(), d.isoformat(), d, d
    if period == "week":
        start = d - timedelta(days=d.weekday())  # Monday
        end = start + timedelta(days=6)
        iso_year, iso_week, _ = start.isocalendar()
        return f"{iso_year}-W{iso_week:02d}", f"{start.isoformat()} → {end.isoformat()}", start, end
    if period == "year":
        return str(d.year), str(d.year), date(d.year, 1, 1), date(d.year, 12, 31)
    # month (default)
    start = d.replace(day=1)
    end = d.replace(day=calendar.monthrange(d.year, d.month)[1])
    key = d.isoformat()[:7]
    return key, key, start, end


def _vat_base(txn: Transaction) -> Decimal:
    """The transaction's VAT converted to the base currency via its FX rate.

    Uses the app-wide FX convention ``base_amount = amount * fx_rate`` — so VAT
    (which is stored in the transaction's *original* currency, like ``amount``)
    is likewise **multiplied** by the rate to reach the base currency. Dividing
    here would corrupt reclaimable VAT for foreign-currency business receipts.

    A VAT amount that exceeds the transaction's own (original-currency) spend is
    nonsensical — VAT is a component of the price paid, never larger than it —
    and usually a receipt-entry typo. We clamp VAT to the transaction's spend
    magnitude (in the original currency, *before* FX conversion) so an inflated
    figure can never make reclaimable VAT exceed the spend it belongs to and the
    Business summary stays coherent.
    """
    if txn.vat_amount is None:
        return _ZERO
    vat = Decimal(txn.vat_amount)
    # Compare in the original currency (both amount + vat are pre-FX). ``amount``
    # is negative for money-out; use its magnitude as the ceiling.
    spend = abs(Decimal(txn.amount)) if txn.amount is not None else None
    if spend is not None and vat > spend:
        vat = spend
    rate = txn.fx_rate if txn.fx_rate is not None else Decimal("1")
    return (vat * Decimal(rate)).quantize(Decimal("0.01"))


def _business_spend(
    db: Session, account_ids: set[int] | None, year: int | None = None
) -> list[Transaction]:
    """Business money-out transactions (excludes transfers/duplicates/archived).

    ``year`` (when given) is filtered in SQL via an index-friendly date range on
    ``transaction_date`` so the DB — not Python — narrows the rows to that calendar
    year."""
    year_conditions = (
        (
            Transaction.transaction_date >= date(year, 1, 1),
            Transaction.transaction_date <= date(year, 12, 31),
        )
        if year is not None
        else ()
    )
    return list(
        db.scalars(
            select(Transaction).where(
                Transaction.is_business.is_(True),
                Transaction.base_amount.is_not(None),
                Transaction.base_amount < 0,  # money out
                Transaction.is_transfer.is_(False),
                Transaction.is_duplicate.is_(False),
                *year_conditions,
                *account_scope_condition(account_ids),
                *archived_condition(),
            )
        ).all()
    )


def summary(
    db: Session, *, account_ids: set[int] | None = None, period: str = "month", year: int | None = None
) -> dict:
    """Totals + reclaimable VAT, by category and by period, for business expenses.

    ``period`` is one of ``day|week|month|year`` (default month); each period
    bucket carries its ``start``/``end`` so the UI can expand it straight into the
    transactions list (date range + ``is_business``). ``year`` scopes the view to a
    single calendar year (a Budgets-style date scope); ``None`` is all-time."""
    if period not in PERIODS:
        period = "month"
    txns = _business_spend(db, account_ids, year)
    total = _ZERO
    vat_total = _ZERO
    by_cat: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    by_cat_vat: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    buckets: dict[str, dict] = {}
    dates = []

    for t in txns:
        amt = -(t.base_amount or Decimal("0"))
        v = _vat_base(t)
        total += amt
        vat_total += v
        by_cat[t.category_id] += amt
        by_cat_vat[t.category_id] += v
        key, label, start, end = _period_bucket(t.transaction_date, period)
        b = buckets.get(key)
        if b is None:
            b = buckets[key] = {
                "period": key, "label": label, "start": start, "end": end,
                "total": Decimal("0.00"), "vat": Decimal("0.00"), "count": 0,
            }
        b["total"] += amt
        b["vat"] += v
        b["count"] += 1
        dates.append(t.transaction_date)

    # Only the categories actually referenced by these business rows are needed
    # for the labels (identical output, avoids loading the whole category table).
    cat_ids = {cid for cid in by_cat if cid is not None}
    cats: dict[int | None, str] = (
        {c.id: c.name for c in db.scalars(select(Category).where(Category.id.in_(cat_ids))).all()}
        if cat_ids
        else {}
    )
    by_category = sorted(
        (
            {
                "category_id": cid,
                "name": cats.get(cid, "Uncategorised"),
                "total": str(by_cat[cid]),
                "vat": str(by_cat_vat[cid]),
            }
            for cid in by_cat
        ),
        key=lambda r: Decimal(r["total"]),
        reverse=True,
    )
    by_period = sorted(
        (
            {
                "period": b["period"],
                "label": b["label"],
                "start": b["start"].isoformat(),
                "end": b["end"].isoformat(),
                "total": str(b["total"]),
                "vat": str(b["vat"]),
                "count": b["count"],
            }
            for b in buckets.values()
        ),
        key=lambda r: r["start"],
        reverse=True,  # newest period first
    )

    return {
        "currency": settings_service.get_base_currency(db),
        "period": period,
        "total": str(total),
        "vat": str(vat_total),
        "transaction_count": len(txns),
        "first": min(dates).isoformat() if dates else None,
        "last": max(dates).isoformat() if dates else None,
        "by_category": by_category,
        "by_period": by_period,
    }
