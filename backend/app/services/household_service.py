"""Household / account bootstrap helpers.

The MVP is single-user but every entity hangs off a household + account, so we
lazily create sensible defaults on first use (spec §6, §12.3, §12.4).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Account, Household


def get_or_create_default_household(db: Session) -> Household:
    household = db.scalars(select(Household).limit(1)).first()
    if household is None:
        household = Household(
            name="My Household",
            currency=settings.currency,
            mode=settings.setup_mode.value,
        )
        db.add(household)
        db.flush()
    return household


def get_or_create_account(
    db: Session,
    household: Household,
    institution: str,
    account_id: int | None = None,
) -> Account:
    if account_id is not None:
        account = db.get(Account, account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")
        return account
    account = db.scalars(
        select(Account).where(
            Account.household_id == household.id, Account.name == institution
        )
    ).first()
    if account is None:
        account = Account(
            household_id=household.id,
            name=institution,
            institution=institution,
            account_type="credit_card" if institution == "Curve" else "current_account",
            currency=household.currency,
        )
        db.add(account)
        db.flush()
    return account
