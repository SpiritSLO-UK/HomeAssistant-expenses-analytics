"""Household / account bootstrap helper tests (SR-E9).

Covers the tightened account lookup (name + institution, deterministic order),
the data-driven institution -> account_type mapping, and the deterministic
default-household selection.
"""

from __future__ import annotations

from app.models import Account, Household
from app.services.household_service import (
    DEFAULT_ACCOUNT_TYPE,
    INSTITUTION_ACCOUNT_TYPES,
    get_or_create_account,
    get_or_create_default_household,
)


def test_default_household_is_lowest_id_when_multiple(db):
    first = get_or_create_default_household(db)
    # A second household (e.g. created via some other flow) must not shadow the
    # original: the "default" is the deterministically-lowest id.
    second = Household(name="Other", currency="GBP", mode=first.mode)
    db.add(second)
    db.flush()
    assert get_or_create_default_household(db).id == first.id


def test_create_account_maps_known_institution_type(db):
    household = get_or_create_default_household(db)
    account = get_or_create_account(db, household, "Curve")
    assert account.account_type == INSTITUTION_ACCOUNT_TYPES["Curve"]
    assert account.account_type == "credit_card"
    assert account.name == "Curve"
    assert account.institution == "Curve"


def test_create_account_defaults_unknown_institution_type(db):
    household = get_or_create_default_household(db)
    account = get_or_create_account(db, household, "Barclays")
    assert account.account_type == DEFAULT_ACCOUNT_TYPE


def test_get_account_reuses_existing_match(db):
    household = get_or_create_default_household(db)
    first = get_or_create_account(db, household, "Curve")
    again = get_or_create_account(db, household, "Curve")
    assert again.id == first.id


def test_match_requires_institution_not_just_name(db):
    """A same-named account from a different institution must not be reused;
    matching purely by name could attach transactions to the wrong account."""
    household = get_or_create_default_household(db)
    manual = Account(
        household_id=household.id,
        name="Curve",
        institution="Manually Entered",
        account_type=DEFAULT_ACCOUNT_TYPE,
        currency=household.currency,
    )
    db.add(manual)
    db.flush()

    account = get_or_create_account(db, household, "Curve")
    assert account.id != manual.id
    assert account.institution == "Curve"
    assert account.account_type == "credit_card"


def test_match_is_deterministic_with_duplicates(db):
    """Legacy duplicates (name + institution) resolve to the lowest id, not an
    arbitrary row."""
    household = get_or_create_default_household(db)
    dup_a = Account(
        household_id=household.id,
        name="Monzo",
        institution="Monzo",
        account_type=DEFAULT_ACCOUNT_TYPE,
        currency=household.currency,
    )
    dup_b = Account(
        household_id=household.id,
        name="Monzo",
        institution="Monzo",
        account_type=DEFAULT_ACCOUNT_TYPE,
        currency=household.currency,
    )
    db.add_all([dup_a, dup_b])
    db.flush()

    account = get_or_create_account(db, household, "Monzo")
    assert account.id == min(dup_a.id, dup_b.id)


def test_auto_created_account_is_unowned_and_household_shared(db):
    """An auto-created import account is left OWNERLESS (owner_user_id = NULL) on
    purpose: in the visibility model an unowned account is included in every
    member's visible set, i.e. household-shared rather than private to the
    importer. This locks the documented single-household default (see the
    multi-user caveat in household_service.get_or_create_account)."""
    household = get_or_create_default_household(db)
    account = get_or_create_account(db, household, "Curve")
    assert account.owner_user_id is None
    assert account.is_shared is False


def test_account_id_lookup_returns_that_account(db):
    household = get_or_create_default_household(db)
    existing = get_or_create_account(db, household, "Curve")
    fetched = get_or_create_account(db, household, "ignored", account_id=existing.id)
    assert fetched.id == existing.id
