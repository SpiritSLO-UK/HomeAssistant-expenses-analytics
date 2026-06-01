"""Dashboard calculations (spec §37).

Monthly summary and category/vendor breakdowns. Totals are in the household
**base currency** using each transaction's ``base_amount`` (backlog #29);
foreign transactions without an FX rate yet (``base_amount IS NULL``) are
excluded until a rate is supplied. Transfers and duplicates are excluded from
spend/income.

Splits (spec §37.4): when a transaction ``is_split`` its split parts drive the
**category** breakdown instead of the transaction's own category. The monthly
spend/income totals are unchanged by splitting because the split parts sum to
the transaction total by validation (spec §17.2).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Category, Transaction, Vendor
from app.services import settings_service, split_service
from app.services.scope import account_scope_condition


def month_bounds(ref: date) -> tuple[date, date]:
    """First day of ref's month and first day of the next month (exclusive end)."""
    start = ref.replace(day=1)
    end = date(ref.year + 1, 1, 1) if ref.month == 12 else date(ref.year, ref.month + 1, 1)
    return start, end


def _spendable_conditions():
    """Transactions that count toward totals: not transfers/duplicates and with
    a known base-currency amount (spec §37.1; backlog #29)."""
    return [
        Transaction.is_transfer.is_(False),
        Transaction.is_duplicate.is_(False),
        Transaction.base_amount.is_not(None),
    ]


def summary(db: Session, ref: date, *, account_ids: set[int] | None = None) -> dict:
    start, end = month_bounds(ref)
    scope = account_scope_condition(account_ids)
    window = [
        Transaction.transaction_date >= start,
        Transaction.transaction_date < end,
        *_spendable_conditions(),
        *scope,
    ]

    spend = db.scalar(
        select(func.coalesce(func.sum(-Transaction.base_amount), 0)).where(
            *window, Transaction.base_amount < 0
        )
    ) or Decimal("0")
    income = db.scalar(
        select(func.coalesce(func.sum(Transaction.base_amount), 0)).where(
            *window, Transaction.base_amount > 0
        )
    ) or Decimal("0")

    # Counts are scoped too, so a member can't infer the volume of private activity.
    total_txns = db.scalar(select(func.count()).select_from(Transaction).where(*scope)) or 0
    uncategorised = db.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.category_id.is_(None), *scope)
    ) or 0
    review_count = db.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.needs_review.is_(True), *scope)
    ) or 0
    needs_rate = db.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.needs_rate.is_(True), *scope)
    ) or 0

    return {
        "month": start.isoformat(),
        "currency": settings_service.get_base_currency(db),
        "spend_this_month": str(spend),
        "income_this_month": str(income),
        "net_this_month": str(income - spend),
        "total_transactions": int(total_txns),
        "uncategorised_transactions": int(uncategorised),
        "review_items": int(review_count),
        "needs_rate": int(needs_rate),
    }


def category_breakdown(db: Session, ref: date, *, account_ids: set[int] | None = None) -> list[dict]:
    """Spend per category for the month, in base currency (positive = money out).

    Split-aware (spec §37.4): a split transaction contributes each of its parts
    to that part's category; a non-split transaction contributes its whole
    ``base_amount`` to its own category. Computed in Python so split allocation
    and FX are handled in one place (data is local + modest in size).
    """
    start, end = month_bounds(ref)
    txns = db.scalars(
        select(Transaction).where(
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
            Transaction.base_amount < 0,  # spend only (money out)
            *_spendable_conditions(),
            *account_scope_condition(account_ids),
        )
    ).all()

    totals: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    counts: dict[int | None, int] = defaultdict(int)
    for txn in txns:
        if txn.is_split and txn.splits:
            for split in txn.splits:
                base = split_service.split_base_amount(txn, split)
                if base is None:
                    continue
                totals[split.category_id] += -base
                counts[split.category_id] += 1
        else:
            totals[txn.category_id] += -txn.base_amount
            counts[txn.category_id] += 1

    cats = {c.id: c for c in db.scalars(select(Category)).all()}
    rows = [
        {
            "category_id": cid,
            "name": cats[cid].name if cid in cats else "Uncategorised",
            "colour": cats[cid].colour if cid in cats else None,
            "total": str(total),
            "count": counts[cid],
        }
        for cid, total in totals.items()
    ]
    rows.sort(key=lambda r: Decimal(r["total"]), reverse=True)
    return rows


def vendor_breakdown(db: Session, ref: date, limit: int = 10, *, account_ids: set[int] | None = None) -> list[dict]:
    """Top vendors by spend for the month, in base currency."""
    start, end = month_bounds(ref)
    rows = db.execute(
        select(
            Transaction.merchant_id,
            Vendor.canonical_name,
            func.sum(-Transaction.base_amount).label("total"),
            func.count().label("count"),
        )
        .join(Vendor, Vendor.id == Transaction.merchant_id, isouter=True)
        .where(
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
            Transaction.base_amount < 0,
            Transaction.merchant_id.is_not(None),
            *_spendable_conditions(),
            *account_scope_condition(account_ids),
        )
        .group_by(Transaction.merchant_id, Vendor.canonical_name)
        .order_by(func.sum(-Transaction.base_amount).desc())
        .limit(limit)
    ).all()

    return [
        {
            "vendor_id": r.merchant_id,
            "name": r.canonical_name or "Unknown",
            "total": str(r.total),
            "count": int(r.count),
        }
        for r in rows
    ]
