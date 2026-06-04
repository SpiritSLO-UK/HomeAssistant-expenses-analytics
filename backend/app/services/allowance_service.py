"""Child allowance overlay (backlog #82; spec §6, §19).

Parents attribute their own spend to a child so it appears on the child's
allowance view — **without** changing the parent's books. Nothing here is read
by dashboards, household budgets, or analytics, so the originating transaction
stays fully on the parent's expenses ("remain on parent's expense, show on kid").

Amounts are stored as positive money-out in the household **base currency**, to
line up with budgets (which use ``base_amount``).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Budget, Category, ChildAllocation, Transaction, TransactionSplit, User
from app.services import budget_service, savings_service, settings_service, split_service

TWO_DP = Decimal("0.01")


def _out(value: Decimal) -> Decimal:
    """Store spend as a positive number, 2dp."""
    return abs(Decimal(value)).quantize(TWO_DP)


def _status(spent: Decimal, amount: Decimal, threshold: int | None) -> str:
    if amount <= 0:
        return "ok"
    if spent > amount:
        return "over"
    if threshold is not None and (spent / amount) * 100 >= threshold:
        return "warn"
    return "ok"


def _resolve_split_fields(
    txn: Transaction,
    split: TransactionSplit,
    *,
    category_id: int | None,
    amount: Decimal | None,
    description: str | None,
    as_of: date | None,
) -> tuple[Decimal, int | None, date, str | None]:
    """Resolve (amount, category, date, description) for a split allocation."""
    derived = split_service.split_base_amount(txn, split)
    fallback = derived if derived is not None else split.amount
    amt = _out(amount if amount is not None else fallback)
    cat = category_id if category_id is not None else split.category_id
    when = as_of or txn.transaction_date
    desc = description or split.description or txn.description_raw
    return amt, cat, when, desc


def _resolve_txn_fields(
    txn: Transaction,
    *,
    category_id: int | None,
    amount: Decimal | None,
    description: str | None,
    as_of: date | None,
) -> tuple[Decimal, int | None, date, str | None]:
    """Resolve (amount, category, date, description) for a whole-transaction allocation."""
    fallback = txn.base_amount if txn.base_amount is not None else txn.amount
    amt = _out(amount if amount is not None else fallback)
    cat = category_id if category_id is not None else txn.category_id
    when = as_of or txn.transaction_date
    desc = description or txn.description_raw
    return amt, cat, when, desc


def _resolve_manual_fields(
    *,
    category_id: int | None,
    amount: Decimal | None,
    description: str | None,
    as_of: date | None,
) -> tuple[Decimal, int | None, date, str | None]:
    """Resolve (amount, category, date, description) for a manual allocation."""
    if amount is None:
        raise ValueError("A manual allocation needs an amount")
    return _out(amount), category_id, as_of or date.today(), description


def create_allocation(
    db: Session,
    *,
    child_id: int,
    transaction_id: int | None = None,
    split_id: int | None = None,
    category_id: int | None = None,
    amount: Decimal | None = None,
    description: str | None = None,
    as_of: date | None = None,
) -> ChildAllocation:
    """Attribute spend to a child. whole = transaction_id; split = + split_id;
    manual = neither (amount required). Source fields are copied where present and
    can be overridden by the explicit args."""
    child = db.get(User, child_id)
    if child is None:
        raise ValueError("Unknown user")
    txn = db.get(Transaction, transaction_id) if transaction_id else None
    if transaction_id and txn is None:
        raise ValueError("Unknown transaction")
    split = db.get(TransactionSplit, split_id) if split_id else None
    if split_id and split is None:
        raise ValueError("Unknown split")

    base_currency = settings_service.get_base_currency(db)
    if split is not None and txn is not None:
        amt, cat, when, desc = _resolve_split_fields(
            txn, split, category_id=category_id, amount=amount, description=description, as_of=as_of
        )
    elif txn is not None:
        amt, cat, when, desc = _resolve_txn_fields(
            txn, category_id=category_id, amount=amount, description=description, as_of=as_of
        )
    else:  # manual
        amt, cat, when, desc = _resolve_manual_fields(
            category_id=category_id, amount=amount, description=description, as_of=as_of
        )

    row = ChildAllocation(
        household_id=child.household_id,
        user_id=child.id,
        transaction_id=txn.id if txn else None,
        transaction_split_id=split.id if split else None,
        category_id=cat,
        amount=amt,
        currency=base_currency,
        description=desc,
        as_of_date=when,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_allocations(db: Session, child_id: int) -> list[ChildAllocation]:
    return list(
        db.scalars(
            select(ChildAllocation)
            .where(ChildAllocation.user_id == child_id)
            .order_by(ChildAllocation.as_of_date.desc(), ChildAllocation.id.desc())
        ).all()
    )


def delete_allocation(db: Session, allocation_id: int) -> bool:
    row = db.get(ChildAllocation, allocation_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def _allocation_spend(db: Session, child_id: int | None, category_id: int | None, start: date, end: date) -> Decimal:
    stmt = select(ChildAllocation).where(
        ChildAllocation.user_id == child_id,
        ChildAllocation.as_of_date >= start,
        ChildAllocation.as_of_date < end,
    )
    if category_id is not None:  # a category budget; None = a total child budget
        stmt = stmt.where(ChildAllocation.category_id == category_id)
    rows = db.scalars(stmt).all()
    return sum((Decimal(r.amount) for r in rows), Decimal("0.00"))


def child_budget_status(db: Session, budget: Budget, ref: date) -> dict:
    """A child budget's progress, with spend drawn from the child's allocations."""
    start, end = budget_service.period_bounds(budget, ref)
    spent = _allocation_spend(db, budget.owner_user_id, budget.category_id, start, end)
    amount = Decimal(budget.amount)
    return {
        "budget_id": budget.id,
        "name": budget.name,
        "category_id": budget.category_id,
        "period": budget.period,
        "currency": settings_service.get_base_currency(db),
        "amount": str(amount),
        "spent": str(spent),
        "remaining": str(amount - spent),
        "percent": round(float((spent / amount) * 100) if amount > 0 else 0.0, 1),
        "status": _status(spent, amount, budget.alert_threshold_percent),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
    }


def child_budgets(db: Session, child_id: int) -> list[Budget]:
    return list(
        db.scalars(
            select(Budget).where(Budget.owner_user_id == child_id).order_by(Budget.name)
        ).all()
    )


def allocation_to_dict(db: Session, alloc: ChildAllocation) -> dict:
    """Serialise a single allocation (resolves its category name)."""
    cat = db.get(Category, alloc.category_id) if alloc.category_id else None
    return _allocation_to_dict(alloc, {cat.id: cat.name} if cat else {})


def _allocation_to_dict(alloc: ChildAllocation, cat_names: dict[int, str]) -> dict:
    return {
        "id": alloc.id,
        "as_of_date": alloc.as_of_date.isoformat(),
        "description": alloc.description,
        "category_id": alloc.category_id,
        "category_name": cat_names.get(alloc.category_id) if alloc.category_id else None,
        "amount": str(alloc.amount),
        "currency": alloc.currency,
        "transaction_id": alloc.transaction_id,
    }


def summary(db: Session, user: User) -> dict:
    """The child's whole world: their budgets, their savings, their item list."""
    ref = date.today()
    budgets = [child_budget_status(db, b, ref) for b in child_budgets(db, user.id)]

    accounts = [
        savings_service.account_to_dict(db, a)
        for a in savings_service.list_accounts(db, owner_user_id=user.id)
    ]
    account_ids = {a["id"] for a in accounts}
    goals = [
        savings_service.goal_to_dict(db, g)
        for g in savings_service.list_goals(db)
        if g.account_id in account_ids
    ]

    cat_names = {c.id: c.name for c in db.scalars(select(Category)).all()}
    items = [_allocation_to_dict(a, cat_names) for a in list_allocations(db, user.id)]

    return {
        "user_id": user.id,
        "display_name": user.display_name,
        "currency": settings_service.get_base_currency(db),
        "budgets": budgets,
        "savings": {
            "total_savings": str(savings_service.total_savings(db, owner_user_id=user.id)),
            "accounts": accounts,
            "goals": goals,
        },
        "items": items,
    }
