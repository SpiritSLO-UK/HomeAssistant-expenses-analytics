"""Recurring-payment / subscription detection (spec §20).

Heuristic detector: group spend by vendor (or normalised merchant name when no
vendor is matched), and flag groups that recur at a regular interval with a
consistent amount (spec §20.1). Amounts and totals are in the household base
currency (only transactions with a known ``base_amount`` are considered).

Detection is idempotent — re-running upserts each subscription by
vendor/name. A user's explicit ``cancelled``/``ignored`` status is never
overwritten by re-detection.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Category, Subscription, Transaction
from app.services import settings_service
from app.services.household_service import get_or_create_default_household

FREQUENCY_INTERVALS = {"weekly": 7, "monthly": 30, "quarterly": 91, "yearly": 365}
# (low, high, frequency) bands for the median gap between occurrences, in days.
_BANDS = [(5, 9, "weekly"), (24, 38, "monthly"), (80, 100, "quarterly"), (330, 400, "yearly")]
# Detection never flips a status the user set deliberately.
USER_LOCKED = {"cancelled", "ignored"}
# How many months a frequency works out to (for "monthly equivalent" cost).
_PER_MONTH = {
    "weekly": Decimal("52") / Decimal("12"),
    "monthly": Decimal("1"),
    "quarterly": Decimal("1") / Decimal("3"),
    "yearly": Decimal("1") / Decimal("12"),
}
TWO_DP = Decimal("0.01")


def _classify(median_gap: float) -> str | None:
    for low, high, freq in _BANDS:
        if low <= median_gap <= high:
            return freq
    return None


def _group_key(txn: Transaction) -> tuple:
    if txn.merchant_id is not None:
        return ("v", txn.merchant_id)
    label = (txn.merchant_raw or txn.description_raw or "").strip().lower()
    return ("n", label)


def _label(txn: Transaction) -> str:
    return (txn.merchant_raw or txn.description_raw or "Unknown").strip()


def _is_subscription_category(db: Session, category_id: int | None) -> bool:
    if category_id is None:
        return False
    cat = db.get(Category, category_id)
    return bool(cat and "subscription" in (cat.name or "").lower())


def _find_existing(db: Session, vendor_id: int | None, name: str) -> Subscription | None:
    if vendor_id is not None:
        return db.scalars(select(Subscription).where(Subscription.vendor_id == vendor_id)).first()
    return db.scalars(
        select(Subscription).where(
            Subscription.vendor_id.is_(None), func.lower(Subscription.name) == name.lower()
        )
    ).first()


def detect(db: Session, min_occurrences: int = 3) -> dict:
    """Scan transactions and upsert detected subscriptions. Returns counts."""
    base_currency = settings_service.get_base_currency(db)
    txns = list(
        db.scalars(
            select(Transaction)
            .where(
                Transaction.is_transfer.is_(False),
                Transaction.is_duplicate.is_(False),
                Transaction.base_amount.is_not(None),
                Transaction.base_amount < 0,  # money out
            )
            .order_by(Transaction.transaction_date)
        ).all()
    )

    groups: dict[tuple, list[Transaction]] = defaultdict(list)
    for txn in txns:
        groups[_group_key(txn)].append(txn)

    created = updated = 0
    for key, items in groups.items():
        if len(items) < min_occurrences:
            continue
        items.sort(key=lambda t: t.transaction_date)
        dates = [t.transaction_date for t in items]
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        gaps = [g for g in gaps if g > 0]
        if len(gaps) < min_occurrences - 1:
            continue

        freq = _classify(statistics.median(gaps))
        if freq is None:
            continue

        amounts = [abs(Decimal(t.base_amount)) for t in items]
        mean_amt = sum(amounts) / len(amounts)
        if mean_amt == 0:
            continue
        max_dev = max(abs(a - mean_amt) for a in amounts) / mean_amt
        if max_dev > Decimal("0.35"):
            continue  # too variable to be a fixed subscription

        gap_mean = statistics.mean(gaps)
        gap_cov = (statistics.pstdev(gaps) / gap_mean) if gap_mean else 0.0
        confidence = 0.5
        confidence += max(0.0, 0.3 * (1 - min(gap_cov, 1.0)))       # regular interval
        confidence += max(0.0, 0.2 * (1 - float(max_dev) / 0.35))   # consistent amount
        latest = items[-1]
        if _is_subscription_category(db, latest.category_id):
            confidence += 0.1
        confidence = round(min(1.0, confidence), 2)

        interval = FREQUENCY_INTERVALS[freq]
        last_seen = dates[-1]
        amount = amounts[-1].quantize(TWO_DP)  # current price = most recent charge
        vendor_id = key[1] if key[0] == "v" else None
        name = _label(latest)
        status = "active" if confidence >= 0.6 else "possible"

        existing = _find_existing(db, vendor_id, name)
        if existing is None:
            db.add(
                Subscription(
                    household_id=get_or_create_default_household(db).id,
                    vendor_id=vendor_id,
                    category_id=latest.category_id,
                    name=name,
                    amount=amount,
                    currency=base_currency,
                    frequency=freq,
                    interval_days=interval,
                    last_seen_date=last_seen,
                    next_expected_date=last_seen + timedelta(days=interval),
                    confidence_score=confidence,
                    occurrences=len(items),
                    status=status,
                )
            )
            created += 1
        else:
            existing.category_id = latest.category_id
            existing.name = name
            existing.amount = amount
            existing.currency = base_currency
            existing.frequency = freq
            existing.interval_days = interval
            existing.last_seen_date = last_seen
            existing.next_expected_date = last_seen + timedelta(days=interval)
            existing.confidence_score = confidence
            existing.occurrences = len(items)
            if existing.status not in USER_LOCKED:
                existing.status = status
            updated += 1

    db.commit()
    total = db.scalar(select(func.count()).select_from(Subscription)) or 0
    return {"created": created, "updated": updated, "total": int(total)}


def monthly_equivalent(amount: Decimal, frequency: str) -> Decimal:
    return (Decimal(amount) * _PER_MONTH.get(frequency, Decimal("1"))).quantize(TWO_DP)


def to_dict(sub: Subscription) -> dict:
    return {
        "id": sub.id,
        "vendor_id": sub.vendor_id,
        "category_id": sub.category_id,
        "name": sub.name,
        "amount": str(sub.amount),
        "currency": sub.currency,
        "frequency": sub.frequency,
        "monthly_amount": str(monthly_equivalent(sub.amount, sub.frequency)),
        "interval_days": sub.interval_days,
        "next_expected_date": sub.next_expected_date.isoformat() if sub.next_expected_date else None,
        "last_seen_date": sub.last_seen_date.isoformat() if sub.last_seen_date else None,
        "confidence_score": sub.confidence_score,
        "occurrences": sub.occurrences,
        "status": sub.status,
    }


def monthly_total(db: Session) -> Decimal:
    """Monthly-equivalent cost of all **active** subscriptions (base currency)."""
    total = Decimal("0.00")
    for sub in db.scalars(select(Subscription).where(Subscription.status == "active")).all():
        total += monthly_equivalent(sub.amount, sub.frequency)
    return total


def dashboard_summary(db: Session) -> dict:
    subs = db.scalars(
        select(Subscription).where(Subscription.status == "active").order_by(Subscription.name)
    ).all()
    items = [to_dict(s) for s in subs]
    return {
        "currency": settings_service.get_base_currency(db),
        "monthly_total": str(monthly_total(db)),
        "count": len(items),
        "subscriptions": items,
    }
