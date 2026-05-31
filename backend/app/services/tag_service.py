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
        tag.name = name.strip()
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
    seen: dict[str, Tag] = {}
    for raw in names:
        name = raw.strip()
        if not name or name.lower() in seen:
            continue
        seen[name.lower()] = get_or_create(db, name)
    txn.tags = list(seen.values())
    db.commit()
    db.refresh(txn)
    return txn
