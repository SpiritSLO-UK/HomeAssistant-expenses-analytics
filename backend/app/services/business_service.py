"""Business / VAT expense analytics (backlog: corporate receipts).

A lens over transactions flagged ``is_business``: total business spend (in the
household base currency), reclaimable **VAT**, and breakdowns by category and
month — for expense claiming. Account-scoped + archived-excluded like every other
aggregate. Read-only (no new model beyond the two transaction columns).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Transaction
from app.services import settings_service
from app.services.scope import account_scope_condition, archived_condition

_ZERO = Decimal("0.00")


def _vat_base(txn: Transaction) -> Decimal:
    """The transaction's VAT converted to the base currency via its FX rate."""
    if txn.vat_amount is None:
        return _ZERO
    rate = txn.fx_rate if txn.fx_rate is not None else Decimal("1")
    return (Decimal(txn.vat_amount) * Decimal(rate)).quantize(Decimal("0.01"))


def _business_spend(db: Session, account_ids: set[int] | None) -> list[Transaction]:
    """Business money-out transactions (excludes transfers/duplicates/archived)."""
    return list(
        db.scalars(
            select(Transaction).where(
                Transaction.is_business.is_(True),
                Transaction.base_amount.is_not(None),
                Transaction.base_amount < 0,  # money out
                Transaction.is_transfer.is_(False),
                Transaction.is_duplicate.is_(False),
                *account_scope_condition(account_ids),
                *archived_condition(),
            )
        ).all()
    )


def summary(db: Session, *, account_ids: set[int] | None = None) -> dict:
    """Totals + reclaimable VAT, by category and month, for business expenses."""
    txns = _business_spend(db, account_ids)
    total = _ZERO
    vat_total = _ZERO
    by_cat: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    by_cat_vat: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    by_month: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    dates = []

    for t in txns:
        amt = -t.base_amount
        v = _vat_base(t)
        total += amt
        vat_total += v
        by_cat[t.category_id] += amt
        by_cat_vat[t.category_id] += v
        by_month[t.transaction_date.isoformat()[:7]] += amt
        dates.append(t.transaction_date)

    cats = {c.id: c.name for c in db.scalars(select(Category)).all()}
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
    months = [{"month": m, "total": str(by_month[m])} for m in sorted(by_month)]

    return {
        "currency": settings_service.get_base_currency(db),
        "total": str(total),
        "vat": str(vat_total),
        "transaction_count": len(txns),
        "first": min(dates).isoformat() if dates else None,
        "last": max(dates).isoformat() if dates else None,
        "by_category": by_category,
        "by_month": months,
    }
