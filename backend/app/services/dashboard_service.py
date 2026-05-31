"""Dashboard calculations (spec §37).

Monthly summary and category/vendor breakdowns. Totals are in the household
**base currency** using each transaction's ``base_amount`` (backlog #29);
foreign transactions without an FX rate yet (``base_amount IS NULL``) are
excluded until a rate is supplied. Split-aware calculations (spec §37.4) arrive
with Stage 4. Transfers and duplicates are excluded from spend/income.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Category, Transaction, Vendor
from app.services import settings_service


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


def summary(db: Session, ref: date) -> dict:
    start, end = month_bounds(ref)
    window = [
        Transaction.transaction_date >= start,
        Transaction.transaction_date < end,
        *_spendable_conditions(),
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

    total_txns = db.scalar(select(func.count()).select_from(Transaction)) or 0
    uncategorised = db.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.category_id.is_(None))
    ) or 0
    review_count = db.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.needs_review.is_(True))
    ) or 0
    needs_rate = db.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.needs_rate.is_(True))
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


def category_breakdown(db: Session, ref: date) -> list[dict]:
    """Spend per category for the month, in base currency (positive = money out)."""
    start, end = month_bounds(ref)
    rows = db.execute(
        select(
            Transaction.category_id,
            Category.name,
            Category.colour,
            func.sum(-Transaction.base_amount).label("total"),
            func.count().label("count"),
        )
        .join(Category, Category.id == Transaction.category_id, isouter=True)
        .where(
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
            Transaction.base_amount < 0,
            *_spendable_conditions(),
        )
        .group_by(Transaction.category_id, Category.name, Category.colour)
        .order_by(func.sum(-Transaction.base_amount).desc())
    ).all()

    return [
        {
            "category_id": r.category_id,
            "name": r.name or "Uncategorised",
            "colour": r.colour,
            "total": str(r.total),
            "count": int(r.count),
        }
        for r in rows
    ]


def vendor_breakdown(db: Session, ref: date, limit: int = 10) -> list[dict]:
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
