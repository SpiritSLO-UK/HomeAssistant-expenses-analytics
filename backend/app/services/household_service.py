"""Household / account bootstrap helpers.

The MVP is single-user but every entity hangs off a household + account, so we
lazily create sensible defaults on first use (spec §6, §12.3, §12.4).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Account, Household

# Data-driven account-type overrides keyed by institution name. Institutions
# absent from this map fall back to DEFAULT_ACCOUNT_TYPE. Keep this small and
# explicit rather than burying magic strings in the create logic.
INSTITUTION_ACCOUNT_TYPES: dict[str, str] = {
    "Curve": "credit_card",
}
DEFAULT_ACCOUNT_TYPE = "current_account"


def get_or_create_default_household(db: Session) -> Household:
    household = db.scalars(
        select(Household).order_by(Household.id).limit(1)
    ).first()
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
    # Match on household + name AND institution so a name collision (e.g. two
    # accounts sharing a display name but from different institutions) can't
    # attach transactions to the wrong account. order_by(id) keeps the choice
    # deterministic if legacy duplicates already exist.
    account = db.scalars(
        select(Account)
        .where(
            Account.household_id == household.id,
            Account.name == institution,
            Account.institution == institution,
        )
        .order_by(Account.id)
    ).first()
    if account is None:
        # Auto-created accounts are left OWNERLESS (owner_user_id = NULL) on
        # purpose. In the visibility model (see scope.py / auth_service.py) an
        # unowned account has a real id and is included in *every* member's
        # visible set — i.e. it is household-shared, not private. Setting an
        # owner here would instead make the account private to one member and
        # hide freshly-imported transactions from the rest of the household.
        #
        # MULTI-USER CAVEAT: when this app grows to real multi-user households,
        # an import triggered by member A will still surface A's imported
        # account to everyone. The safer per-user behaviour (attribute the
        # account to the importer) is deliberately NOT done here because the
        # import call chain carries no importing-user id: the /upload route has
        # no get_current_user dependency and create_import()/receipts/demo all
        # call this without a user. Threading an importer id through would be a
        # cross-file change; unowned-is-shared remains the intended single-
        # household default, and ownership is assigned as a separate explicit
        # step (e.g. account edit, or demo_service post-import) when needed.
        account = Account(
            household_id=household.id,
            name=institution,
            institution=institution,
            account_type=INSTITUTION_ACCOUNT_TYPES.get(institution, DEFAULT_ACCOUNT_TYPE),
            currency=household.currency,
        )
        db.add(account)
        db.flush()
    return account
