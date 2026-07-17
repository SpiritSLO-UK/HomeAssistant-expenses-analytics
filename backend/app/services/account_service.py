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

from sqlalchemy import func, select, union, update
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AccountValue,
    CurveFundingLink,
    Holding,
    SavingsBalance,
    SavingsGoal,
    Statement,
    Transaction,
)
from app.services import audit_service, settings_service
from app.services.household_service import get_or_create_default_household

# Every table keyed on ``accounts.id`` — what a delete would orphan/cascade and
# what a merge re-points from source → target. Each column is literally named
# ``account_id`` so the merge can set it uniformly. (label, model, column).
# No explicit annotation: let the type checker infer the real column type so the
# ``col == account_id`` / ``col.is_not(...)`` expressions stay SQL, not bool.
#
# WARNING: this tuple MUST cover *every* foreign key that references
# ``accounts.id``. A missing entry is silently orphaned or — for an
# ``ON DELETE CASCADE`` FK (curve_funding_links, holdings, account_values,
# savings_balances) — hard-deleted when the source account is removed at the end
# of a merge, i.e. real data loss with no re-point. ``test_account_refs_cover_all_fks``
# reflects the schema and fails if a new account FK is added without extending this
# tuple, so keep the two in sync.
_ACCOUNT_REFS = (
    ("transactions", Transaction, Transaction.account_id),
    ("statements", Statement, Statement.account_id),
    ("savings_balances", SavingsBalance, SavingsBalance.account_id),
    ("savings_goals", SavingsGoal, SavingsGoal.account_id),
    ("investment_values", AccountValue, AccountValue.account_id),
    ("holdings", Holding, Holding.account_id),
    ("curve_funding_links", CurveFundingLink, CurveFundingLink.account_id),
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
    only on empty accounts and Merge on the rest). A single ``UNION`` over the
    referencing tables (which de-duplicates) replaces one query per table, so it is
    one round-trip regardless of how many tables/accounts exist."""
    combined = union(*(select(col).where(col.is_not(None)) for _label, _model, col in _ACCOUNT_REFS))
    return {row for row in db.scalars(combined).all() if row is not None}


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


def _is_private(account: Account) -> bool:
    """An account is *private* when it has an owner and is not shared — only that
    owner (and admins) may see it or its transactions (mirrors the ``is_private``
    the accounts API returns)."""
    return account.owner_user_id is not None and not account.is_shared


def _assert_merge_allowed(source: Account, target: Account, allow_cross_scope: bool) -> None:
    """Refuse a merge that would silently widen who can see the source's data.

    A merge re-points every referencing row from ``source`` onto ``target`` and
    then deletes the source, so the *target's* household / owner / shared-ness
    becomes the new visibility of all that moved data. Two crossings are blocked:

    * **Cross-household** — always refused; no legitimate merge moves one
      household's data into another.
    * **Private → wider scope** — a private source (owned, not shared) folded into
      a target that is shared, or owned by someone else, would expose that owner's
      transactions. Refused unless the caller explicitly passes
      ``allow_cross_scope`` to acknowledge it.
    """
    if source.household_id != target.household_id:
        raise ValueError("Cannot merge accounts across households.")
    if _is_private(source) and not allow_cross_scope:
        widens = target.is_shared or target.owner_user_id != source.owner_user_id
        if widens:
            raise ValueError(
                "This account is private; merging it into a shared or differently-owned "
                "account would expose its data. Pass allow_cross_scope to confirm."
            )


def merge_account(
    db: Session,
    source_id: int,
    target_id: int,
    *,
    actor: str | None = None,
    allow_cross_scope: bool = False,
) -> Account | None:
    """Merge ``source_id`` into ``target_id``: re-point every reference from the
    source to the target, write an audit record, then delete the source. Returns
    the target; ``None`` if either id is unknown; raises ``ValueError`` on a
    self-merge, a cross-household merge, or a privacy-widening merge that was not
    explicitly confirmed via ``allow_cross_scope``."""
    if source_id == target_id:
        raise ValueError("Cannot merge an account into itself.")
    source = db.get(Account, source_id)
    target = db.get(Account, target_id)
    if source is None or target is None:
        return None
    _assert_merge_allowed(source, target, allow_cross_scope)

    opts = {"synchronize_session": False}
    repointed: dict[str, int] = {}
    for label, model, col in _ACCOUNT_REFS:
        result = db.execute(
            update(model).where(col == source_id).values(account_id=target_id).execution_options(**opts)
        )
        repointed[label] = result.rowcount or 0

    # Audit before the source is deleted so the record survives even though the row
    # it describes will not (record() flushes into this same transaction).
    audit_service.record(
        db,
        action="account.merge",
        actor=actor,
        entity_type="account",
        entity_id=target_id,
        household_id=target.household_id,
        details={
            "source_id": source_id,
            "source_name": source.name,
            "target_id": target_id,
            "target_name": target.name,
            "source_was_private": _is_private(source),
            "allow_cross_scope": allow_cross_scope,
            "repointed": repointed,
        },
    )
    db.delete(source)
    db.commit()
    db.refresh(target)
    return target
