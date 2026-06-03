"""Global search across transactions, vendors, categories and projects.

Transactions are scoped to the caller's visible accounts (and exclude archived
rows), so search can never reveal a private transaction the user couldn't already
see. Library entities (vendors/categories/projects) are household-wide.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Category, Project, Transaction, Vendor
from app.services.scope import account_scope_condition, archived_condition

MIN_QUERY = 2


def _amount(q: str) -> Decimal | None:
    try:
        return abs(Decimal(q.replace(",", "").replace("£", "").strip()))
    except (InvalidOperation, ValueError):
        return None


def search(db: Session, query: str, *, account_ids: set[int] | None, limit: int = 8) -> dict:
    q = (query or "").strip()
    result: dict = {"query": q, "transactions": [], "vendors": [], "categories": [], "projects": []}
    if len(q) < MIN_QUERY:
        return result

    like = f"%{q}%"
    txn_match = [Transaction.description_raw.ilike(like), Transaction.merchant_raw.ilike(like)]
    amount = _amount(q)
    if amount is not None:
        txn_match.append(func.abs(Transaction.amount) == amount)

    txns = db.scalars(
        select(Transaction)
        .where(or_(*txn_match), *account_scope_condition(account_ids), *archived_condition())
        .order_by(Transaction.transaction_date.desc())
        .limit(limit)
    ).all()
    result["transactions"] = [
        {
            "id": t.id,
            "transaction_date": t.transaction_date.isoformat(),
            "description": t.description_raw,
            "amount": str(t.amount),
            "currency": t.currency,
        }
        for t in txns
    ]

    result["vendors"] = [
        {"id": v.id, "name": v.canonical_name}
        for v in db.scalars(
            select(Vendor).where(Vendor.canonical_name.ilike(like)).order_by(Vendor.canonical_name).limit(limit)
        ).all()
    ]
    result["categories"] = [
        {"id": c.id, "name": c.name, "colour": c.colour}
        for c in db.scalars(
            select(Category)
            .where(Category.name.ilike(like), Category.is_active.is_(True))
            .order_by(Category.name)
            .limit(limit)
        ).all()
    ]
    result["projects"] = [
        {"id": p.id, "name": p.name, "status": p.status}
        for p in db.scalars(
            select(Project).where(Project.name.ilike(like)).order_by(Project.name).limit(limit)
        ).all()
    ]
    return result
