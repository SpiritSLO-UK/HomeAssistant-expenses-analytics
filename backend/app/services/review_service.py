"""Review queue (spec §23, §12.18) — the safety net.

A central list of things the app wasn't sure about: unknown vendors, low-
confidence categorisation, possible duplicates, unmatched receipts, etc. Other
services call :func:`add` to file an item; the user resolves/ignores them on the
Review Queue page. The dashboard's review count comes from the open items here.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ReviewItem
from app.services.household_service import get_or_create_default_household

# The only statuses a review item may hold. A typo'd status would be invisible
# to list_items(status="open") / open_count, so reject it at the service boundary
# (SR-F9). The API schema re-exports this so the two can't drift.
VALID_STATUSES = {"open", "resolved", "ignored"}


def add(
    db: Session,
    *,
    item_type: str,
    item_id: int | None,
    reason: str,
    severity: str = "info",
    suggested_action: str | None = None,
) -> ReviewItem:
    """File a review item, de-duplicating against an existing OPEN one for the
    same (item_type, item_id, reason). Caller commits."""
    existing = db.scalars(
        select(ReviewItem).where(
            ReviewItem.item_type == item_type,
            ReviewItem.item_id == item_id,
            ReviewItem.reason == reason,
            ReviewItem.status == "open",
        )
    ).first()
    if existing is not None:
        if suggested_action:
            existing.suggested_action = suggested_action
        return existing
    item = ReviewItem(
        household_id=get_or_create_default_household(db).id,
        item_type=item_type,
        item_id=item_id,
        reason=reason,
        severity=severity,
        suggested_action=suggested_action,
    )
    db.add(item)
    db.flush()
    return item


def resolve_for(db: Session, *, item_type: str, item_id: int, reason: str | None = None) -> int:
    """Resolve open items for a given subject (optionally a specific reason)."""
    conds = [
        ReviewItem.item_type == item_type,
        ReviewItem.item_id == item_id,
        ReviewItem.status == "open",
    ]
    if reason is not None:
        conds.append(ReviewItem.reason == reason)
    items = db.scalars(select(ReviewItem).where(*conds)).all()
    for item in items:
        item.status = "resolved"
        item.resolved_at = datetime.now(UTC)
    return len(items)


def list_items(db: Session, status: str | None = "open") -> list[ReviewItem]:
    stmt = select(ReviewItem).order_by(ReviewItem.created_at.desc())
    if status is not None:
        stmt = stmt.where(ReviewItem.status == status)
    return list(db.scalars(stmt).all())


def set_status(db: Session, item: ReviewItem, status: str) -> ReviewItem:
    if status not in VALID_STATUSES:
        raise ValueError(f"Unknown review status {status!r}. One of: {sorted(VALID_STATUSES)}")
    item.status = status
    item.resolved_at = datetime.now(UTC) if status != "open" else None
    db.commit()
    db.refresh(item)
    return item


def open_count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(ReviewItem).where(ReviewItem.status == "open")) or 0)
