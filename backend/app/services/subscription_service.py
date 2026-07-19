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
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Category, Subscription, Transaction
from app.services import settings_service
from app.services.household_service import get_or_create_default_household
from app.services.scope import account_scope_condition, archived_condition

FREQUENCY_INTERVALS = {
    "weekly": 7,
    "fortnightly": 14,
    "monthly": 30,
    "bi_monthly": 61,
    "quarterly": 91,
    "yearly": 365,
}
# (low, high, frequency) bands for the median gap between occurrences, in days.
# Bands are contiguous over the common cadences so a ~14d (fortnightly / bi-weekly),
# ~42d (6-weekly, nearest band = monthly), or ~60d (bi-monthly) cadence isn't lost
# in a gap between bands. A clean 7d/30d/91d/365d cadence still lands in its band.
_BANDS = [
    (5, 9, "weekly"),
    (10, 19, "fortnightly"),
    (20, 48, "monthly"),
    (49, 74, "bi_monthly"),
    (75, 135, "quarterly"),
    (300, 430, "yearly"),
]
# Detection never flips a status the user set deliberately.
USER_LOCKED = {"cancelled", "ignored"}
# How many months a frequency works out to (for "monthly equivalent" cost).
_PER_MONTH = {
    "weekly": Decimal("52") / Decimal("12"),
    "fortnightly": Decimal("26") / Decimal("12"),
    "monthly": Decimal("1"),
    "bi_monthly": Decimal("1") / Decimal("2"),
    "quarterly": Decimal("1") / Decimal("3"),
    "yearly": Decimal("1") / Decimal("12"),
}
TWO_DP = Decimal("0.01")
# Detection only needs enough *recent* history to establish the longest cadence it
# recognises (yearly — up to ~430d between charges, band above) across at least
# min_occurrences charges. Bounding the scan to a window anchored on the most
# recent transaction avoids re-reading an unbounded all-time history every run,
# while still detecting every recurrence a full scan would for realistic data:
# ~3 years comfortably spans 3+ yearly charges (and dozens of monthly/weekly ones).
_DETECT_WINDOW_DAYS = 366 * 3


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


def _positive_gaps(dates: list[date]) -> list[int]:
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    return [g for g in gaps if g > 0]


def _is_monotonic_increase(amounts: list[Decimal]) -> bool:
    """True when charges only ever rise (or hold) over time — a legitimate price
    increase / step-up rather than erratic noise. Requires at least one real rise
    so a flat series doesn't trivially qualify (it's already handled as consistent)."""
    if len(amounts) < 2:
        return False
    rose = False
    for prev, cur in zip(amounts, amounts[1:], strict=False):
        if cur < prev:
            return False
        if cur > prev:
            rose = True
    return rose


def _confidence(
    db: Session, gaps: list[int], max_dev: Decimal, latest: Transaction, amounts: list[Decimal]
) -> float:
    gap_mean = statistics.mean(gaps)
    gap_cov = (statistics.pstdev(gaps) / gap_mean) if gap_mean else 0.0
    confidence = 0.5
    confidence += max(0.0, 0.3 * (1 - min(gap_cov, 1.0)))       # regular interval
    # A monotonic price rise is a legit increase, not noise, so it shouldn't be
    # penalised the way an erratic (up-and-down) amount is. Give such a series the
    # full "consistent amount" credit; genuine volatility still costs confidence.
    if _is_monotonic_increase(amounts):
        confidence += 0.2
    else:
        confidence += max(0.0, 0.2 * (1 - float(max_dev) / 0.35))   # consistent amount
    if _is_subscription_category(db, latest.category_id):
        confidence += 0.1
    return round(min(1.0, confidence), 2)


def _detect_group(db: Session, items: list[Transaction], min_occurrences: int) -> dict | None:
    """Validate one vendor/name group and, if it looks like a subscription,
    return the field values to upsert; otherwise None (group skipped)."""
    if len(items) < min_occurrences:
        return None
    items.sort(key=lambda t: t.transaction_date)
    dates = [t.transaction_date for t in items]
    gaps = _positive_gaps(dates)
    if len(gaps) < min_occurrences - 1:
        return None

    freq = _classify(statistics.median(gaps))
    if freq is None:
        return None

    amounts = [abs(Decimal(t.base_amount or 0)) for t in items]
    mean_amt = sum(amounts, Decimal("0")) / len(amounts)
    if mean_amt == 0:
        return None
    max_dev = max(abs(a - mean_amt) for a in amounts) / mean_amt
    if max_dev > Decimal("0.35"):
        return None  # too variable to be a fixed subscription

    latest = items[-1]
    confidence = _confidence(db, gaps, max_dev, latest, amounts)
    interval = FREQUENCY_INTERVALS[freq]
    last_seen = dates[-1]
    return {
        "category_id": latest.category_id,
        "name": _label(latest),
        "amount": amounts[-1].quantize(TWO_DP),  # current price = most recent charge
        "frequency": freq,
        "interval_days": interval,
        "last_seen_date": last_seen,
        "next_expected_date": last_seen + timedelta(days=interval),
        "confidence_score": confidence,
        "occurrences": len(items),
        "status": "active" if confidence >= 0.6 else "possible",
    }


def _upsert_subscription(db: Session, vendor_id: int | None, base_currency: str, fields: dict) -> str:
    """Create or update the subscription for this vendor/name. Returns
    ``"created"`` or ``"updated"``."""
    existing = _find_existing(db, vendor_id, fields["name"])
    if existing is None:
        db.add(
            Subscription(
                household_id=get_or_create_default_household(db).id,
                vendor_id=vendor_id,
                currency=base_currency,
                **fields,
            )
        )
        return "created"
    existing.category_id = fields["category_id"]
    existing.name = fields["name"]
    existing.amount = fields["amount"]
    existing.currency = base_currency
    existing.frequency = fields["frequency"]
    existing.interval_days = fields["interval_days"]
    existing.last_seen_date = fields["last_seen_date"]
    existing.next_expected_date = fields["next_expected_date"]
    existing.confidence_score = fields["confidence_score"]
    existing.occurrences = fields["occurrences"]
    if existing.status not in USER_LOCKED:
        existing.status = fields["status"]
    return "updated"


def _money_out_conditions() -> list:
    """Filters for the spend rows detection groups over (money out, not a transfer
    or duplicate, with a known base-currency amount)."""
    return [
        Transaction.is_transfer.is_(False),
        Transaction.is_duplicate.is_(False),
        Transaction.base_amount.is_not(None),
        Transaction.base_amount < 0,  # money out
    ]


def _recent_spend(db: Session) -> list[Transaction]:
    """Money-out transactions within the detection window (§20.1), anchored on the
    most recent spend so a large all-time history isn't re-read every run. Empty
    when there is no spend at all."""
    conds = _money_out_conditions()
    latest = db.scalar(select(func.max(Transaction.transaction_date)).where(*conds))
    if latest is None:
        return []
    cutoff = latest - timedelta(days=_DETECT_WINDOW_DAYS)
    return list(
        db.scalars(
            select(Transaction)
            .where(*conds, Transaction.transaction_date >= cutoff)
            .order_by(Transaction.transaction_date)
        ).all()
    )


def detect(db: Session, min_occurrences: int = 3) -> dict:
    """Scan transactions and upsert detected subscriptions. Returns counts."""
    base_currency = settings_service.get_base_currency(db)
    txns = _recent_spend(db)

    groups: dict[tuple, list[Transaction]] = defaultdict(list)
    for txn in txns:
        groups[_group_key(txn)].append(txn)

    created = updated = 0
    for key, items in groups.items():
        fields = _detect_group(db, items, min_occurrences)
        if fields is None:
            continue
        vendor_id = key[1] if key[0] == "v" else None
        if _upsert_subscription(db, vendor_id, base_currency, fields) == "created":
            created += 1
        else:
            updated += 1

    db.commit()
    total = db.scalar(select(func.count()).select_from(Subscription)) or 0
    return {"created": created, "updated": updated, "total": int(total)}


def visible_subscription_ids(db: Session, account_ids: set[int] | None) -> set[int] | None:
    """Subscription ids supported by at least one transaction in a visible account
    (shared vs private; #66/#82). ``None`` = unrestricted (owner/admin). Detection
    runs over all data, but a subscription is only *shown* to a non-admin if it has
    a backing transaction they can see — so a sub seen only on someone else's
    private account stays hidden."""
    if account_ids is None:
        return None
    # Only the grouping columns are needed, deduped in SQL, so we don't materialise
    # every visible transaction as an ORM object each call. Vendor-matched rows
    # contribute a vendor id; the rest contribute a normalised merchant/name key —
    # the exact same partition ``_group_key`` produces.
    scope = [
        Transaction.is_transfer.is_(False),
        Transaction.is_duplicate.is_(False),
        *account_scope_condition(account_ids),
        *archived_condition(),
    ]
    vendor_ids: set[int] = set(
        db.scalars(
            select(Transaction.merchant_id)
            .where(*scope, Transaction.merchant_id.is_not(None))
            .distinct()
        ).all()
    )
    name_keys: set[str] = {
        (merchant_raw or description_raw or "").strip().lower()
        for merchant_raw, description_raw in db.execute(
            select(Transaction.merchant_raw, Transaction.description_raw)
            .where(*scope, Transaction.merchant_id.is_(None))
            .distinct()
        ).all()
    }
    out: set[int] = set()
    for sub_id, sub_vendor_id, sub_name in db.execute(
        select(Subscription.id, Subscription.vendor_id, Subscription.name)
    ).all():
        if sub_vendor_id is not None:
            if sub_vendor_id in vendor_ids:
                out.add(sub_id)
        elif (sub_name or "").strip().lower() in name_keys:
            out.add(sub_id)
    return out


def _active_visible(db: Session, account_ids: set[int] | None) -> list[Subscription]:
    visible = visible_subscription_ids(db, account_ids)
    subs = db.scalars(
        select(Subscription).where(Subscription.status == "active").order_by(Subscription.name)
    ).all()
    if visible is None:
        return list(subs)
    return [s for s in subs if s.id in visible]


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


def monthly_total(db: Session, *, account_ids: set[int] | None = None) -> Decimal:
    """Monthly-equivalent cost of all **active** subscriptions (base currency)."""
    total = Decimal("0.00")
    for sub in _active_visible(db, account_ids):
        total += monthly_equivalent(sub.amount, sub.frequency)
    return total


def dashboard_summary(db: Session, *, account_ids: set[int] | None = None) -> dict:
    # Compute the active-visible set ONCE and derive both the items and the monthly
    # total from it (#27). Calling monthly_total() here would re-run _active_visible
    # -> visible_subscription_ids (two DISTINCT scans + a full Subscription scan) a
    # second time. Summing monthly_equivalent inline matches monthly_total exactly.
    active = _active_visible(db, account_ids)
    total = sum((monthly_equivalent(s.amount, s.frequency) for s in active), Decimal("0.00"))
    return {
        "currency": settings_service.get_base_currency(db),
        "monthly_total": str(total),
        "count": len(active),
        "subscriptions": [to_dict(s) for s in active],
    }


def alerts(
    db: Session,
    ref: date | None = None,
    *,
    within_days: int = 7,
    overdue_grace: int = 3,
    account_ids: set[int] | None = None,
) -> dict:
    """Renewal reminders + missed-payment warnings for **active** subscriptions
    (spec §20.3). ``upcoming`` = next charge due within ``within_days`` (or just
    passed, within the grace window); ``overdue`` = expected more than
    ``overdue_grace`` days ago and not seen since (a missed payment, or a sub the
    user forgot to cancel). ``ref`` defaults to today."""
    ref = ref or date.today()
    upcoming: list[dict] = []
    overdue: list[dict] = []
    for sub in _active_visible(db, account_ids):
        if sub.next_expected_date is None:
            continue
        days = (sub.next_expected_date - ref).days
        item = to_dict(sub)
        if days < -overdue_grace:
            overdue.append({**item, "days_overdue": -days, "expected_date": sub.next_expected_date.isoformat()})
        elif days <= within_days:
            upcoming.append({**item, "days_until": days})

    upcoming.sort(key=lambda x: x["days_until"])
    overdue.sort(key=lambda x: x["days_overdue"], reverse=True)
    return {
        "currency": settings_service.get_base_currency(db),
        "ref": ref.isoformat(),
        "within_days": within_days,
        "upcoming": upcoming,
        "overdue": overdue,
    }
