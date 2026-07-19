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
from app.models import Category, Rule, Subscription, Transaction
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
    "set_country",
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


def _matches_amount_equals(cv: str, txn: Transaction) -> bool:
    target = _to_decimal(cv)
    return target is not None and txn.amount == target


def _amount_bounds(cv: str) -> tuple[Decimal, Decimal] | None:
    """Parse an ``amount_between`` condition value into ``(lo, hi)``.

    The value is ``"lo,hi"`` (comma- or pipe-separated) on the signed amount.
    Returns ``None`` — so the condition simply never matches — when the input is
    malformed: wrong number of parts, an unparseable bound, or a mistyped
    locale/format (e.g. a decimal comma ``"10,5"`` reading as two ints, or
    ``"1.234,56"`` euro grouping). Bounds given out of order (``hi,lo``) are
    tolerated by swapping them so ``lo <= hi`` always holds."""
    parts = cv.replace("|", ",").split(",")
    if len(parts) != 2:
        return None
    lo, hi = _to_decimal(parts[0]), _to_decimal(parts[1])
    if lo is None or hi is None:
        return None
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _matches_amount_between(cv: str, txn: Transaction) -> bool:
    bounds = _amount_bounds(cv)
    if bounds is None:
        return False
    lo, hi = bounds
    return lo <= txn.amount <= hi


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
        return _matches_amount_equals(cv, txn)
    if ct == "amount_between":
        return _matches_amount_between(cv, txn)
    # recurring_payment / source_format are not supported yet (Stage 6 / needs
    # statement context) — they simply never match for now.
    return False


def _int_action_value(av: str | None) -> int | None:
    """Parse a numeric action value, or ``None`` when it isn't a plain integer."""
    return int(av) if av and av.isdigit() else None


def _apply_set_category(txn: Transaction, av: str | None) -> bool:
    # Don't override a manual choice (spec §15.1: manual > rule).
    if txn.confidence_score is not None and txn.confidence_score >= MANUAL_CONFIDENCE:
        return False
    value = _int_action_value(av)
    if value is None:
        return False
    txn.category_id = value
    txn.confidence_score = RULE_CONFIDENCE
    return True


def _apply_set_country(txn: Transaction, av: str | None) -> bool:
    """Tag the transaction's spend location (ISO alpha-2). Normalised exactly like
    the manual/bulk country edits (upper, first two chars; blank clears) so the
    spend-by-location map sees a consistent code (#79/#107/#109)."""
    code = (av or "").strip().upper()[:2]
    txn.country = code or None
    return True


def _apply_mark_subscription(db: Session, txn: Transaction) -> bool:
    """Record the transaction as a subscription via the existing Subscription
    mechanism (``subscription_service``). A single transaction can't establish a
    cadence the way the heuristic detector does, so we create/upsert a
    ``possible`` subscription keyed on the same vendor/name grouping the detector
    uses — a later :func:`subscription_service.detect` run promotes it to
    ``active`` with a real interval once enough occurrences are seen. Returns
    ``True`` when a subscription was created (a no-op if one already exists for
    this vendor/name, so the action doesn't consume its slot pointlessly)."""
    # Imported lazily to avoid a circular import at module load.
    from app.services import subscription_service

    vendor_id = txn.merchant_id
    name = subscription_service._label(txn)
    if subscription_service._find_existing(db, vendor_id, name) is not None:
        return False
    db.add(
        Subscription(
            household_id=get_or_create_default_household(db).id,
            vendor_id=vendor_id,
            category_id=txn.category_id,
            name=name,
            amount=abs(txn.amount),
            currency=txn.currency or "GBP",
            frequency="monthly",
            interval_days=subscription_service.FREQUENCY_INTERVALS["monthly"],
            last_seen_date=txn.transaction_date,
            occurrences=1,
            # User asked for it via a rule, but a single hit isn't proof of a
            # cadence — leave it "possible" for the detector to confirm.
            status="possible",
        )
    )
    return True


def apply_action(rule: Rule, txn: Transaction, db: Session | None = None) -> bool:
    """Apply a single rule's action. Returns ``True`` when the action actually
    took effect, ``False`` when it was a no-op (e.g. ``set_category`` skipped
    because a manual choice already wins, or a value-setting action with an
    unparseable value). The caller uses this so a no-op rule doesn't consume the
    one-per-action-type slot and block a lower-priority rule that *would* apply
    (SR-A4).

    ``db`` is only needed for actions that persist a related row
    (``mark_subscription``); when it's ``None`` those actions are treated as a
    no-op so callers with no session (e.g. previews) stay side-effect free."""
    at = rule.action_type
    av = rule.action_value

    if at == "set_category":
        return _apply_set_category(txn, av)
    elif at == "set_vendor":
        value = _int_action_value(av)
        if value is None:
            return False
        txn.merchant_id = value
        return True
    elif at == "set_project":
        value = _int_action_value(av)
        if value is None:
            return False
        txn.project_id = value
        return True
    elif at == "set_country":
        return _apply_set_country(txn, av)
    elif at == "mark_transfer":
        txn.is_transfer = True
        return True
    elif at == "mark_income":
        txn.is_income = True
        return True
    elif at == "require_review":
        txn.needs_review = True
        txn.review_reason = txn.review_reason or "rule"
        return True
    elif at == "mark_subscription":
        return _apply_mark_subscription(db, txn) if db is not None else False
    # block_cloud_ai is designed but not yet wired: the AI gateway only honours a
    # *category*-level never-cloud flag (Category.privacy_sensitivity); there is
    # no transaction-level lever it reads, and adding one needs a DB migration
    # (deliberately out of scope here). Marked as a no-op so it never claims the
    # action slot. Use per-category privacy to keep a category off cloud AI.
    return False


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
        if matches(rule, txn) and apply_action(rule, txn, db):
            # Only a rule that actually applied claims its action slot and is
            # reported as fired — a no-op leaves the slot open for the next rule.
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


def _reenable(db: Session, rule: Rule) -> Rule:
    """Return a learned rule, re-enabling it first if it was disabled. A disabled
    rule would report success while the correction silently never applies, so we
    flip it back on (SR-A4)."""
    if not rule.enabled:
        rule.enabled = True
        db.commit()
        db.refresh(rule)
    return rule


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
    target_action = str(category_id)

    # Find every learned rule for this exact condition value (same normalisation
    # as the stored condition). Lowest id first — that's the one apply_rules
    # grants the set_category slot to (priority desc, id asc).
    candidates = db.scalars(
        select(Rule)
        .where(
            Rule.condition_type == "description_contains",
            Rule.condition_value == value,
            Rule.action_type == "set_category",
        )
        .order_by(Rule.id)
    ).all()

    # Exact match (same condition + same category) is a no-op: return it instead
    # of piling up duplicates when the user clicks "+ rule" again (backlog bug).
    exact = next((r for r in candidates if r.action_value == target_action), None)
    if exact is not None:
        return _reenable(db, exact)

    # Re-teaching the same description to a DIFFERENT category: update the winning
    # rule in place rather than inserting a lower-precedence duplicate that
    # apply_rules would permanently shadow behind the older, lower-id rule (#8).
    if candidates:
        target = candidates[0]
        target.action_value = target_action
        target.name = f"{value} → {cat_name}"
        target.enabled = True
        db.commit()
        db.refresh(target)
        return target

    return create_rule(
        db,
        {
            "name": f"{value} → {cat_name}",
            # User rules sit above the seeded defaults.
            "priority": 200,
            "condition_type": "description_contains",
            "condition_value": value,
            "action_type": "set_category",
            "action_value": target_action,
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
