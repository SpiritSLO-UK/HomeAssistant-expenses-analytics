"""Tag management (spec §18.3, §12.13).

Tags are flexible, free-form labels (reimbursable, work, warranty, gift, …) in a
many-to-many relationship with transactions. Names are matched case-insensitively
so "Work" and "work" don't both get created.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Tag, Transaction, transaction_tags
from app.services.household_service import get_or_create_default_household


def list_tags(db: Session) -> list[Tag]:
    return list(db.scalars(select(Tag).order_by(Tag.name)).all())


def _find_ci(db: Session, name: str, household_id: int | None) -> Tag | None:
    """Case-insensitive lookup scoped to a household (SR-B8)."""
    return db.scalars(
        select(Tag).where(
            func.lower(Tag.name) == name.lower(),
            func.coalesce(Tag.household_id, -1) == (household_id if household_id is not None else -1),
        )
    ).first()


def get_or_create(db: Session, name: str, colour: str | None = None) -> Tag:
    name = name.strip()
    if not name:
        raise ValueError("Tag name cannot be empty")
    household = get_or_create_default_household(db)
    existing = _find_ci(db, name, household.id)
    if existing is not None:
        return existing
    tag = Tag(name=name, colour=colour, household_id=household.id)
    db.add(tag)
    try:
        db.flush()
    except IntegrityError:
        # A concurrent caller inserted the same case-insensitive name first and the
        # unique index (ix_tags_household_lower_name) rejected our row. Roll our
        # pending insert back and return the winner (SR-B8).
        db.rollback()
        winner = _find_ci(db, name, household.id)
        if winner is None:  # pragma: no cover - only if the row vanished mid-race
            raise
        return winner
    return tag


def update_tag(db: Session, tag: Tag, *, name: str | None = None, colour: str | None = None) -> Tag:
    if name is not None:
        new_name = name.strip()
        if not new_name:
            raise ValueError("Tag name cannot be empty")
        # Honour the same case-insensitive uniqueness as get_or_create — renaming
        # onto another tag's name (any case) would otherwise create a duplicate
        # the matcher can't tell apart (SR-B8).
        clash = db.scalars(
            select(Tag).where(func.lower(Tag.name) == new_name.lower(), Tag.id != tag.id)
        ).first()
        if clash is not None:
            raise ValueError(f"A tag named {new_name!r} already exists")
        tag.name = new_name
    if colour is not None:
        tag.colour = colour
    db.commit()
    db.refresh(tag)
    return tag


def delete_tag(db: Session, tag: Tag) -> None:
    db.delete(tag)
    db.commit()


def merge_tags(db: Session, source_id: int, target_id: int) -> Tag:
    """Move every transaction of ``source`` onto ``target`` then delete ``source``.

    Associations are re-pointed at the association-table level (no per-row Python
    loop) and de-duped so a transaction already tagged with both ends up with a
    single association. Both tags must live in the same household to keep scoping
    consistent with the rest of the service (SR-B8). Merging a tag into itself is a
    no-op, so the operation is idempotent-safe.
    """
    target = db.get(Tag, target_id)
    if target is None:
        raise ValueError(f"Target tag {target_id} not found")
    if source_id == target_id:
        return target
    source = db.get(Tag, source_id)
    if source is None:
        raise ValueError(f"Source tag {source_id} not found")
    if source.household_id != target.household_id:
        raise ValueError("Cannot merge tags from different households")

    tt = transaction_tags
    # Drop the source associations that would collide with an existing target one,
    # then re-point whatever remains. Order matters: dedupe before the UPDATE so the
    # composite (transaction_id, tag_id) primary key is never violated.
    already_tagged = select(tt.c.transaction_id).where(tt.c.tag_id == target_id)
    db.execute(
        delete(tt).where(tt.c.tag_id == source_id, tt.c.transaction_id.in_(already_tagged))
    )
    db.execute(update(tt).where(tt.c.tag_id == source_id).values(tag_id=target_id))
    db.delete(source)
    db.commit()
    db.refresh(target)
    return target


def usage_counts(db: Session) -> dict[int, int]:
    """Return ``{tag_id: transaction_count}`` for every tag in one grouped query.

    Tags with no associations report ``0`` (LEFT JOIN), so this doubles as the data
    source for surfacing unused tags without an N+1 count-per-tag.
    """
    tt = transaction_tags
    rows = db.execute(
        select(Tag.id, func.count(tt.c.transaction_id))
        .select_from(Tag)
        .outerjoin(tt, tt.c.tag_id == Tag.id)
        .group_by(Tag.id)
    ).all()
    return {tag_id: count for tag_id, count in rows}


def delete_unused(db: Session) -> int:
    """Delete every tag with zero transaction associations; return how many went."""
    used = select(transaction_tags.c.tag_id).distinct()
    result = db.execute(delete(Tag).where(Tag.id.not_in(used)))
    db.commit()
    return result.rowcount


def set_transaction_tags(db: Session, txn: Transaction, names: list[str]) -> Transaction:
    """Replace a transaction's tags with the given names (creating any new ones)."""
    # Normalise + dedupe case-insensitively, keeping the first-seen display form.
    wanted: dict[str, str] = {}
    for raw in names:
        name = raw.strip()
        if name and name.lower() not in wanted:
            wanted[name.lower()] = name

    # One query for every existing tag instead of a SELECT per name (SR-B8).
    existing = {
        t.name.lower(): t
        for t in db.scalars(select(Tag).where(func.lower(Tag.name).in_(list(wanted)))).all()
    } if wanted else {}

    household = None
    tags: list[Tag] = []
    for low, display in wanted.items():
        tag = existing.get(low)
        if tag is None:
            if household is None:
                household = get_or_create_default_household(db)
            tag = Tag(name=display, household_id=household.id)
            db.add(tag)
            db.flush()
            existing[low] = tag
        tags.append(tag)

    txn.tags = tags
    db.commit()
    db.refresh(txn)
    return txn
