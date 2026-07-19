"""Manual-correction rule learning — the Transactions "+ rule" path (spec §15.3).

Regression for the "+ rule does nothing / piles up duplicates" report: creating a
learned rule twice for the same merchant→category is idempotent.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.models import Category, Rule, Transaction
from app.services import rule_service


def _seed(db) -> tuple[Category, Transaction]:
    cat = Category(name="Coffee", path="Coffee")
    db.add(cat)
    db.commit()
    db.refresh(cat)
    txn = Transaction(
        transaction_date=date.today(),
        description_raw="STARBUCKS LONDON 123",
        merchant_raw="STARBUCKS",
        amount=Decimal("-3.50"),
        currency="GBP",
        direction="debit",
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return cat, txn


def test_create_rule_from_correction_creates_a_rule(db):
    cat, txn = _seed(db)
    rule = rule_service.create_rule_from_correction(db, txn, cat.id)
    assert rule.id is not None
    assert rule.condition_type == "description_contains"
    assert rule.action_type == "set_category"
    assert rule.action_value == str(cat.id)


def test_create_rule_from_correction_is_idempotent(db):
    cat, txn = _seed(db)
    r1 = rule_service.create_rule_from_correction(db, txn, cat.id)
    r2 = rule_service.create_rule_from_correction(db, txn, cat.id)
    assert r1.id == r2.id  # same rule returned, not a duplicate
    count = db.scalar(
        select(func.count())
        .select_from(Rule)
        .where(Rule.condition_value == r1.condition_value, Rule.action_value == str(cat.id))
    )
    assert count == 1


def _seed_amazon(db) -> Transaction:
    txn = Transaction(
        transaction_date=date.today(),
        description_raw="AMAZON MARKETPLACE 987",
        merchant_raw="AMAZON",
        amount=Decimal("-19.99"),
        currency="GBP",
        direction="debit",
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def test_reteach_updates_rule_in_place(db):
    """Re-teaching the same description to a DIFFERENT category updates the
    existing rule in place instead of creating a permanently-shadowed duplicate
    (#8)."""
    shopping = Category(name="Shopping", path="Shopping")
    subscriptions = Category(name="Subscriptions", path="Subscriptions")
    db.add_all([shopping, subscriptions])
    db.commit()
    db.refresh(shopping)
    db.refresh(subscriptions)
    txn = _seed_amazon(db)

    r1 = rule_service.create_rule_from_correction(db, txn, shopping.id)
    r2 = rule_service.create_rule_from_correction(db, txn, subscriptions.id)

    # Same rule row, re-taught in place — not a second one.
    assert r1.id == r2.id
    assert r2.action_value == str(subscriptions.id)
    total = db.scalar(
        select(func.count())
        .select_from(Rule)
        .where(Rule.condition_value == r1.condition_value)
    )
    assert total == 1

    # A fresh AMAZON transaction now categorises to the re-taught category.
    other = _seed_amazon(db)
    fired = rule_service.apply_rules(db, other)
    assert r2.id in fired
    assert other.category_id == subscriptions.id


def test_reteach_then_repeat_is_still_idempotent(db):
    """After a re-teach, teaching the same condition→category again is a no-op."""
    shopping = Category(name="Shopping", path="Shopping")
    subscriptions = Category(name="Subscriptions", path="Subscriptions")
    db.add_all([shopping, subscriptions])
    db.commit()
    db.refresh(shopping)
    db.refresh(subscriptions)
    txn = _seed_amazon(db)

    rule_service.create_rule_from_correction(db, txn, shopping.id)
    r2 = rule_service.create_rule_from_correction(db, txn, subscriptions.id)
    r3 = rule_service.create_rule_from_correction(db, txn, subscriptions.id)

    assert r2.id == r3.id
    total = db.scalar(
        select(func.count())
        .select_from(Rule)
        .where(Rule.condition_value == r2.condition_value)
    )
    assert total == 1
