"""Savings accounts, balance snapshots and goals (spec §12.4; backlog #96, #91).

All money is kept as ``Decimal``; totals assume a single (base) currency — a
mixed-currency savings total would need FX conversion, which is out of scope here
(noted for later).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, SavingsBalance, SavingsGoal
from app.services import settings_service
from app.services.household_service import get_or_create_default_household

SAVINGS_TYPE = "savings"
GOAL_STATUSES = {"active", "achieved", "archived"}
TWO_DP = Decimal("0.01")


# --- Accounts ----------------------------------------------------------------


def list_accounts(
    db: Session, *, owner_user_id: int | None = None, account_ids: set[int] | None = None
) -> list[Account]:
    stmt = select(Account).where(
        Account.account_type == SAVINGS_TYPE, Account.is_active.is_(True)
    )
    if owner_user_id is not None:
        stmt = stmt.where(Account.owner_user_id == owner_user_id)
    if account_ids is not None:  # visibility scope (shared vs private; #66/#82)
        stmt = stmt.where(Account.id.in_(account_ids))
    return list(db.scalars(stmt.order_by(Account.name)).all())


def create_account(db: Session, *, name: str, institution: str | None = None,
                    currency: str | None = None) -> Account:
    household = get_or_create_default_household(db)
    account = Account(
        household_id=household.id,
        name=name.strip(),
        institution=(institution or None),
        account_type=SAVINGS_TYPE,
        currency=(currency or settings_service.get_base_currency(db)).upper(),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def get_savings_account(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None or account.account_type != SAVINGS_TYPE:
        raise ValueError("Not a savings account")
    return account


# --- Balance snapshots -------------------------------------------------------


def record_balance(db: Session, account_id: int, *, as_of: date, balance: Decimal,
                   note: str | None = None) -> SavingsBalance:
    account = get_savings_account(db, account_id)
    row = SavingsBalance(
        account_id=account.id,
        as_of_date=as_of,
        balance=Decimal(balance).quantize(TWO_DP),
        currency=account.currency,
        note=(note or None),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def balance_history(db: Session, account_id: int) -> list[SavingsBalance]:
    return list(
        db.scalars(
            select(SavingsBalance)
            .where(SavingsBalance.account_id == account_id)
            .order_by(SavingsBalance.as_of_date, SavingsBalance.id)
        ).all()
    )


def latest_balance(db: Session, account_id: int) -> Decimal | None:
    row = db.scalars(
        select(SavingsBalance)
        .where(SavingsBalance.account_id == account_id)
        .order_by(SavingsBalance.as_of_date.desc(), SavingsBalance.id.desc())
        .limit(1)
    ).first()
    return Decimal(row.balance) if row else None


def total_savings(
    db: Session, *, owner_user_id: int | None = None, account_ids: set[int] | None = None
) -> Decimal:
    """Sum of the latest snapshot of every savings account (base currency assumed)."""
    total = Decimal("0.00")
    for account in list_accounts(db, owner_user_id=owner_user_id, account_ids=account_ids):
        bal = latest_balance(db, account.id)
        if bal is not None:
            total += bal
    return total


def account_to_dict(db: Session, account: Account) -> dict:
    history = balance_history(db, account.id)
    return {
        "id": account.id,
        "name": account.name,
        "institution": account.institution,
        "currency": account.currency,
        "latest_balance": str(latest_balance(db, account.id)) if history else None,
        "balance_count": len(history),
    }


# --- Goals -------------------------------------------------------------------


def goal_current(db: Session, goal: SavingsGoal) -> Decimal:
    """A linked goal tracks its account's latest balance; otherwise the manual
    ``current_amount``."""
    if goal.account_id is not None:
        bal = latest_balance(db, goal.account_id)
        if bal is not None:
            return bal
    return Decimal(goal.current_amount or 0)


def goal_to_dict(db: Session, goal: SavingsGoal) -> dict:
    current = goal_current(db, goal)
    target = Decimal(goal.target_amount)
    remaining = target - current
    percent = float(min(Decimal("100"), (current / target * 100))) if target > 0 else 0.0
    return {
        "id": goal.id,
        "name": goal.name,
        "target_amount": str(target),
        "target_date": goal.target_date.isoformat() if goal.target_date else None,
        "account_id": goal.account_id,
        "current_amount": str(goal.current_amount),
        "current": str(current.quantize(TWO_DP)),
        "remaining": str(remaining.quantize(TWO_DP)),
        "percent": round(percent, 1),
        "currency": goal.currency,
        "status": "achieved" if current >= target and target > 0 else goal.status,
    }


def create_goal(db: Session, *, name: str, target_amount: Decimal,
                target_date: date | None = None, account_id: int | None = None,
                current_amount: Decimal | None = None, currency: str | None = None) -> SavingsGoal:
    if account_id is not None:
        get_savings_account(db, account_id)  # validate
    goal = SavingsGoal(
        household_id=get_or_create_default_household(db).id,
        name=name.strip(),
        target_amount=Decimal(target_amount).quantize(TWO_DP),
        target_date=target_date,
        account_id=account_id,
        current_amount=Decimal(current_amount or 0).quantize(TWO_DP),
        currency=(currency or settings_service.get_base_currency(db)).upper(),
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


def update_goal(db: Session, goal: SavingsGoal, **fields) -> SavingsGoal:
    if "status" in fields and fields["status"] is not None and fields["status"] not in GOAL_STATUSES:
        raise ValueError(f"status must be one of {sorted(GOAL_STATUSES)}")
    if fields.get("account_id") is not None:
        get_savings_account(db, fields["account_id"])
    for key, value in fields.items():
        if value is not None:
            setattr(goal, key, value)
    db.commit()
    db.refresh(goal)
    return goal


def delete_goal(db: Session, goal: SavingsGoal) -> None:
    db.delete(goal)
    db.commit()


def list_goals(db: Session) -> list[SavingsGoal]:
    return list(db.scalars(select(SavingsGoal).order_by(SavingsGoal.name)).all())


def summary(db: Session, *, account_ids: set[int] | None = None) -> dict:
    accounts = [account_to_dict(db, a) for a in list_accounts(db, account_ids=account_ids)]
    visible_ids = {a["id"] for a in accounts}
    # Drop goals linked to a now-hidden private account (its balance would leak via
    # goal_current); manual/unlinked goals stay visible to everyone.
    goals = [
        goal_to_dict(db, g)
        for g in list_goals(db)
        if g.account_id is None or g.account_id in visible_ids
    ]
    return {
        "currency": settings_service.get_base_currency(db),
        "total_savings": str(total_savings(db, account_ids=account_ids)),
        "accounts": accounts,
        "goals": goals,
    }
