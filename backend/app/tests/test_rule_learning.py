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
