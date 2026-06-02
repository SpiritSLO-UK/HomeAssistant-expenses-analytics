"""Rule engine (spec §12.11, §36, §3.3, §15.1).

Rules are condition -> action pairs applied during import and on demand. They
sit just below manual user choices in the categorisation order (spec §15.1):
manual > **rule** > vendor default > keyword. A manually-confirmed category
(confidence 1.0) is never overridden by a rule.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models import Category, Rule, Transaction
from app.services.household_service import get_or_create_default_household
from app.services.vendor_service import derive_vendor_signature

logger = get_logger(__name__)

# Confidence assigned by an exact rule match (spec §15.2).
RULE_CONFIDENCE = 0.98
MANUAL_CONFIDENCE = 1.0

CONDITION_TYPES = {
    "description_contains",
    "merchant_contains",
    "vendor_equals",
    "account_equals",
    "category_equals",
    "amount_equals",
    "amount_between",
}
ACTION_TYPES = {
    "set_category",
    "set_vendor",
    "set_project",
    "mark_transfer",
    "mark_income",
    "mark_subscription",
    "require_review",
    "block_cloud_ai",
}


def _to_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def matches(rule: Rule, txn: Transaction) -> bool:
    ct = rule.condition_type
    cv = (rule.condition_value or "").strip()
    description = txn.description_raw or ""
    merchant = txn.merchant_raw or description

    if ct == "description_contains":
        return cv.lower() in description.lower()
    if ct == "merchant_contains":
        return cv.lower() in merchant.lower()
    if ct == "vendor_equals":
        return txn.merchant_id is not None and str(txn.merchant_id) == cv
    if ct == "account_equals":
        return txn.account_id is not None and str(txn.account_id) == cv
    if ct == "category_equals":
        return txn.category_id is not None and str(txn.category_id) == cv
    if ct == "amount_equals":
        target = _to_decimal(cv)
        return target is not None and txn.amount == target
    if ct == "amount_between":
        # "lo,hi" on the signed amount.
        parts = cv.replace("|", ",").split(",")
        if len(parts) == 2:
            lo, hi = _to_decimal(parts[0]), _to_decimal(parts[1])
            if lo is not None and hi is not None:
                return lo <= txn.amount <= hi
        return False
    # recurring_payment / source_format are not supported yet (Stage 6 / needs
    # statement context) — they simply never match for now.
    return False


def apply_action(rule: Rule, txn: Transaction) -> None:
    at = rule.action_type
    av = rule.action_value

    if at == "set_category":
        # Don't override a manual choice (spec §15.1: manual > rule).
        if txn.confidence_score is not None and txn.confidence_score >= MANUAL_CONFIDENCE:
            return
        if av and av.isdigit():
            txn.category_id = int(av)
            txn.confidence_score = RULE_CONFIDENCE
    elif at == "set_vendor":
        if av and av.isdigit():
            txn.merchant_id = int(av)
    elif at == "set_project":
        if av and av.isdigit():
            txn.project_id = int(av)
    elif at == "mark_transfer":
        txn.is_transfer = True
    elif at == "mark_income":
        txn.is_income = True
    elif at == "require_review":
        txn.needs_review = True
        txn.review_reason = txn.review_reason or "rule"
    # mark_subscription and block_cloud_ai are recorded intent honoured by later
    # stages (Stage 6 subscriptions / Stage 10 AI gateway).


def apply_rules(db: Session, txn: Transaction) -> list[int]:
    """Apply all enabled rules to a transaction in priority order (highest
    first). For each action type, the highest-priority matching rule wins.
    Returns the ids of rules that fired."""
    rules = db.scalars(
        select(Rule).where(Rule.enabled.is_(True)).order_by(Rule.priority.desc(), Rule.id)
    ).all()
    fired: list[int] = []
    used_actions: set[str] = set()
    for rule in rules:
        if rule.action_type in used_actions:
            continue
        if matches(rule, txn):
            apply_action(rule, txn)
            used_actions.add(rule.action_type)
            fired.append(rule.id)
    return fired


# --- CRUD ---

def list_rules(db: Session) -> list[Rule]:
    return list(db.scalars(select(Rule).order_by(Rule.priority.desc(), Rule.id)).all())


def get_rule(db: Session, rule_id: int) -> Rule | None:
    return db.get(Rule, rule_id)


def create_rule(db: Session, data: dict) -> Rule:
    household = get_or_create_default_household(db)
    rule = Rule(
        household_id=household.id,
        name=data.get("name") or f"{data['condition_type']}:{data['condition_value']}",
        priority=data.get("priority", 100),
        enabled=data.get("enabled", True),
        condition_type=data["condition_type"],
        condition_value=data["condition_value"],
        action_type=data["action_type"],
        action_value=data.get("action_value"),
        created_from=data.get("created_from", "user"),
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def update_rule(db: Session, rule_id: int, data: dict) -> Rule | None:
    rule = db.get(Rule, rule_id)
    if rule is None:
        return None
    for field, value in data.items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    return rule


def delete_rule(db: Session, rule_id: int) -> bool:
    rule = db.get(Rule, rule_id)
    if rule is None:
        return False
    db.delete(rule)
    db.commit()
    return True


def create_rule_from_correction(
    db: Session, txn: Transaction, category_id: int, match_value: str | None = None
) -> Rule:
    """Manual-correction learning (spec §15.3): make a high-priority
    description rule from a corrected transaction so similar future ones are
    categorised automatically."""
    # Default match: the merchant brand (first token of the merchant signature).
    base_text = txn.merchant_raw or derive_vendor_signature(txn.description_raw or "")
    value = (match_value or base_text).strip()
    if not match_value:
        # Use the leading token as a broad brand match (user can edit later).
        value = value.split()[0] if value.split() else value
    category = db.get(Category, category_id)
    cat_name = category.name if category else category_id

    # Idempotent: if an identical learned rule already exists, return it instead
    # of piling up duplicates when the user clicks "+ rule" again (backlog bug).
    existing = db.scalars(
        select(Rule).where(
            Rule.condition_type == "description_contains",
            Rule.condition_value == value,
            Rule.action_type == "set_category",
            Rule.action_value == str(category_id),
        )
    ).first()
    if existing is not None:
        return existing

    return create_rule(
        db,
        {
            "name": f"{value} → {cat_name}",
            # User rules sit above the seeded defaults.
            "priority": 200,
            "condition_type": "description_contains",
            "condition_value": value,
            "action_type": "set_category",
            "action_value": str(category_id),
            "created_from": "manual_correction",
        },
    )


def test_rule(db: Session, condition_type: str, condition_value: str, limit: int = 10) -> dict:
    """Preview which stored transactions a condition would match (rule builder)."""
    probe = Rule(condition_type=condition_type, condition_value=condition_value)
    txns = db.scalars(select(Transaction)).all()
    matched = [t for t in txns if matches(probe, t)]
    return {
        "match_count": len(matched),
        "total": len(txns),
        "sample": [
            {
                "id": t.id,
                "transaction_date": t.transaction_date.isoformat(),
                "description_raw": t.description_raw,
                "amount": str(t.amount),
            }
            for t in matched[:limit]
        ],
    }


def count_rules(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Rule)) or 0
