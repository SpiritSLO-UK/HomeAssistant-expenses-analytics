"""Account management: create / delete / merge (backlog #112).

Renames and privacy/owner edits live in ``routes_accounts`` (PATCH). Here we add
the rest of account management: creating a manual account, deleting an *empty*
account, and **merging** one account into another — re-pointing every reference
from the source to the target then deleting the source, mirroring
``category_service.merge_category``.

Why delete-only-when-empty + merge: an account is referenced by transactions,
statements, savings/investment snapshots and goals. Blindly deleting would orphan
or silently cascade-drop that data. So a non-empty account must be *merged* (its
references re-pointed) rather than deleted.
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AccountValue,
    Holding,
    SavingsBalance,
    SavingsGoal,
    Statement,
    Transaction,
)
from app.services import settings_service
from app.services.household_service import get_or_create_default_household

# Every table keyed on ``accounts.id`` — what a delete would orphan/cascade and
# what a merge re-points from source → target. Each column is literally named
# ``account_id`` so the merge can set it uniformly. (label, model, column).
# No explicit annotation: let the type checker infer the real column type so the
# ``col == account_id`` / ``col.is_not(...)`` expressions stay SQL, not bool.
_ACCOUNT_REFS = (
    ("transactions", Transaction, Transaction.account_id),
    ("statements", Statement, Statement.account_id),
    ("savings_balances", SavingsBalance, SavingsBalance.account_id),
    ("savings_goals", SavingsGoal, SavingsGoal.account_id),
    ("investment_values", AccountValue, AccountValue.account_id),
    ("holdings", Holding, Holding.account_id),
)


def create_account(
    db: Session,
    *,
    name: str,
    account_type: str,
    currency: str | None = None,
    institution: str | None = None,
    owner_user_id: int | None = None,
    is_shared: bool = False,
) -> Account:
    """Create a manual account under the (single) household. Currency defaults to
    the configured base currency when not given."""
    household = get_or_create_default_household(db)
    account = Account(
        household_id=household.id,
        name=name.strip(),
        institution=((institution or "").strip() or None),
        account_type=account_type,
        currency=(currency or settings_service.get_base_currency(db)).upper(),
        owner_user_id=owner_user_id,
        is_shared=is_shared,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def account_usage(db: Session, account_id: int) -> dict[str, int]:
    """Row counts tied to this account, by kind. All-zero ⇒ safe to delete."""
    return {
        label: (db.scalar(select(func.count()).select_from(model).where(col == account_id)) or 0)
        for label, model, col in _ACCOUNT_REFS
    }


def accounts_in_use(db: Session) -> set[int]:
    """Ids of accounts that have any referencing rows (so the UI can offer Delete
    only on empty accounts and Merge on the rest). Six grouped queries total,
    independent of how many accounts exist."""
    used: set[int] = set()
    for _label, _model, col in _ACCOUNT_REFS:
        rows = db.scalars(select(col).where(col.is_not(None)).distinct()).all()
        used.update(row for row in rows if row is not None)
    return used


def delete_account(db: Session, account_id: int) -> Account | None:
    """Delete an **empty** account. Returns the deleted account; ``None`` if the id
    is unknown; raises ``ValueError`` if it still has data (the caller should merge
    it into another account instead)."""
    account = db.get(Account, account_id)
    if account is None:
        return None
    usage = account_usage(db, account_id)
    if any(usage.values()):
        detail = ", ".join(f"{count} {label.replace('_', ' ')}" for label, count in usage.items() if count)
        raise ValueError(
            f"This account still has data ({detail}). Merge it into another account instead of deleting."
        )
    db.delete(account)
    db.commit()
    return account


def merge_account(db: Session, source_id: int, target_id: int) -> Account | None:
    """Merge ``source_id`` into ``target_id``: re-point every reference from the
    source to the target, then delete the source. Returns the target; ``None`` if
    either id is unknown; raises ``ValueError`` on a self-merge."""
    if source_id == target_id:
        raise ValueError("Cannot merge an account into itself.")
    source = db.get(Account, source_id)
    target = db.get(Account, target_id)
    if source is None or target is None:
        return None

    opts = {"synchronize_session": False}
    for _label, model, col in _ACCOUNT_REFS:
        db.execute(
            update(model).where(col == source_id).values(account_id=target_id).execution_options(**opts)
        )
    db.delete(source)
    db.commit()
    db.refresh(target)
    return target
