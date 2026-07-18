"""Review queue (spec §23, §12.18) — the safety net.

A central list of things the app wasn't sure about: unknown vendors, low-
confidence categorisation, possible duplicates, unmatched receipts, etc. Other
services call :func:`add` to file an item; the user resolves/ignores them on the
Review Queue page. The dashboard's review count comes from the open items here.

Commit contract
---------------
The queue-filing mutators (:func:`add`, :func:`resolve_for`) are *transaction
participants*: they never commit, so the calling service/route commits the whole
unit of work atomically (that's why ``add`` flushes rather than commits — see its
docstring). :func:`set_status` is the exception on purpose: it's the terminal,
user-driven action behind ``PATCH /review/{id}`` and owns its commit so the thin
route doesn't have to. Read helpers (:func:`list_items`, :func:`open_count`)
never write.
"""

from __future__ import annotations

from datetime import UTC, datetime

from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import ReviewItem
from app.services.household_service import get_or_create_default_household

# The only statuses a review item may hold. A typo'd status would be invisible
# to list_items(status="open") / open_count, so reject it at the service boundary
# (SR-F9). The API schema re-exports this so the two can't drift.
VALID_STATUSES = {"open", "resolved", "ignored"}

# Cap for list_items() so a large backlog can't return an unbounded result set.
# Generous (the Review Queue page shows far fewer) but finite; callers may widen.
DEFAULT_LIST_LIMIT = 500


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
    same (item_type, item_id, reason). Caller commits.

    Dedup is *per-call*, not merely per-flush: the session runs with
    ``autoflush=False``, so the existence query below wouldn't see uncommitted
    inserts on its own — but each ``add`` flushes its new row before returning
    (see the ``db.flush()`` at the end), so a second ``add`` in the same unit of
    work sees the first and returns it instead of inserting a duplicate."""
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


def list_items(
    db: Session,
    status: str | None = "open",
    *,
    item_type: str | None = None,
    severity: str | None = None,
    limit: int | None = DEFAULT_LIST_LIMIT,
) -> list[ReviewItem]:
    """Review items newest-first, bounded to ``limit`` rows so a huge backlog
    can't return an unbounded result set. ``limit`` defaults to
    :data:`DEFAULT_LIST_LIMIT`; pass a larger value to widen the window, or
    ``None`` for no bound (use sparingly).

    Optionally narrow by ``item_type`` and/or ``severity`` (both default to
    ``None`` = no filter, so existing callers are unaffected)."""
    stmt = select(ReviewItem).order_by(ReviewItem.created_at.desc())
    if status is not None:
        stmt = stmt.where(ReviewItem.status == status)
    if item_type is not None:
        stmt = stmt.where(ReviewItem.item_type == item_type)
    if severity is not None:
        stmt = stmt.where(ReviewItem.severity == severity)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def bulk_resolve(db: Session, ids: Sequence[int], status: str = "resolved") -> int:
    """Set ``status`` on many review items in a single UPDATE (not a per-id
    loop). Terminal, user-driven action behind ``POST /review/bulk-resolve``, so
    it owns its commit like :func:`set_status`. Validates ``status`` (SR-F9);
    ids not present are silently skipped. Returns the number of rows updated."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Unknown review status {status!r}. One of: {sorted(VALID_STATUSES)}")
    if not ids:
        return 0
    resolved_at = datetime.now(UTC) if status != "open" else None
    result = db.execute(
        update(ReviewItem)
        .where(ReviewItem.id.in_(ids))
        .values(status=status, resolved_at=resolved_at)
    )
    db.commit()
    return int(result.rowcount or 0)


def set_status(db: Session, item: ReviewItem, status: str) -> ReviewItem:
    """Terminal user action (PATCH /review/{id}); commits itself — see the
    module's commit-contract note."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Unknown review status {status!r}. One of: {sorted(VALID_STATUSES)}")
    item.status = status
    item.resolved_at = datetime.now(UTC) if status != "open" else None
    db.commit()
    db.refresh(item)
    return item


def open_count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(ReviewItem).where(ReviewItem.status == "open")) or 0)
