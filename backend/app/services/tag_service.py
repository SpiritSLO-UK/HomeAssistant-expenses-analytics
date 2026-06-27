"""Tag management (spec §18.3, §12.13).

Tags are flexible, free-form labels (reimbursable, work, warranty, gift, …) in a
many-to-many relationship with transactions. Names are matched case-insensitively
so "Work" and "work" don't both get created.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Tag, Transaction
from app.services.household_service import get_or_create_default_household


def list_tags(db: Session) -> list[Tag]:
    return list(db.scalars(select(Tag).order_by(Tag.name)).all())


def get_or_create(db: Session, name: str, colour: str | None = None) -> Tag:
    name = name.strip()
    if not name:
        raise ValueError("Tag name cannot be empty")
    existing = db.scalars(
        select(Tag).where(func.lower(Tag.name) == name.lower())
    ).first()
    if existing is not None:
        return existing
    household = get_or_create_default_household(db)
    tag = Tag(name=name, colour=colour, household_id=household.id)
    db.add(tag)
    db.flush()
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
