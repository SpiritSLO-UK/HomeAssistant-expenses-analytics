"""Category service (spec §15).

Loads the bundled category library into the database, provides CRUD, and a
keyword matcher used as a fallback step in auto-categorisation (spec §15.1).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models import Category
from app.services.household_service import get_or_create_default_household

logger = get_logger(__name__)

_LIBRARY_PATH = Path(__file__).resolve().parent.parent / "category_library" / "defaults.json"

# Keyword match confidence (spec §15.2).
KEYWORD_CONFIDENCE = 0.70

# Cloud-AI privacy levels (spec §22.4, §28): normal = send as-is (still redacted
# globally); sensitive = extra-redact before any cloud send; never_cloud = never
# sent to a cloud provider at all. User-selectable per category.
PRIVACY_LEVELS = ("normal", "sensitive", "never_cloud")


@lru_cache
def load_library() -> dict:
    """Load and cache the bundled category library JSON."""
    return json.loads(_LIBRARY_PATH.read_text(encoding="utf-8"))


def import_library(db: Session) -> int:
    """Idempotently load the default category library into the DB.

    Categories are keyed by ``library_id`` so re-running updates names/icons
    without creating duplicates. Returns the number of categories created.
    """
    household = get_or_create_default_household(db)
    library = load_library()
    categories: list[dict] = library.get("categories", [])

    existing = {
        c.library_id: c
        for c in db.scalars(select(Category).where(Category.library_id.is_not(None))).all()
    }

    created = 0
    lib_to_db: dict[str, Category] = {}
    # First pass: create/update the rows themselves.
    for entry in categories:
        lib_id = entry["id"]
        row = existing.get(lib_id)
        if row is None:
            row = Category(library_id=lib_id, household_id=household.id, is_system=True)
            db.add(row)
            created += 1
        row.name = entry["name"]
        row.path = entry["name"]
        row.icon = entry.get("icon")
        row.colour = entry.get("colour")
        row.privacy_sensitivity = entry.get("privacy_sensitivity", "normal")
        row.is_budgetable = entry.get("is_budgetable", True)
        lib_to_db[lib_id] = row

    db.flush()

    # Second pass: resolve parent_id (library uses library_id strings).
    for entry in categories:
        parent_lib = entry.get("parent_id")
        if parent_lib and parent_lib in lib_to_db:
            child = lib_to_db[entry["id"]]
            child.parent_id = lib_to_db[parent_lib].id

    db.commit()
    logger.info("Category library imported: %s created, %s total", created, len(categories))
    return created


def ensure_default_categories(db: Session) -> None:
    """Seed the library on first use if no categories exist."""
    if db.scalar(select(Category.id).limit(1)) is None:
        import_library(db)


def list_categories(db: Session, include_inactive: bool = False) -> list[Category]:
    stmt = select(Category)
    if not include_inactive:
        stmt = stmt.where(Category.is_active.is_(True))
    return list(db.scalars(stmt.order_by(Category.name)).all())


def get_category(db: Session, category_id: int) -> Category | None:
    return db.get(Category, category_id)


def create_category(db: Session, data: dict) -> Category:
    household = get_or_create_default_household(db)
    category = Category(
        household_id=household.id,
        name=data["name"],
        path=data["name"],
        parent_id=data.get("parent_id"),
        description=data.get("description"),
        icon=data.get("icon"),
        colour=data.get("colour"),
        is_budgetable=data.get("is_budgetable", True),
        privacy_sensitivity=data.get("privacy_sensitivity", "normal"),
        is_system=False,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def update_category(db: Session, category_id: int, data: dict) -> Category | None:
    category = db.get(Category, category_id)
    if category is None:
        return None
    for field, value in data.items():
        setattr(category, field, value)
    if "name" in data and category.path == category.name:
        category.path = data["name"]
    db.commit()
    db.refresh(category)
    return category


def delete_category(db: Session, category_id: int) -> bool:
    """Delete a category. Transactions referencing it have category_id set to
    NULL via the FK (spec keeps the transaction as source of truth)."""
    category = db.get(Category, category_id)
    if category is None:
        return False
    db.delete(category)
    db.commit()
    return True


def categorise_text(db: Session, description: str) -> tuple[int | None, float | None]:
    """Suggest a category id for a description via library keyword match.

    Returns (category_id, confidence) or (None, None) if nothing matched.
    """
    if not description:
        return None, None
    text = description.lower()
    library = load_library()

    # Map library_id -> db category id (only seeded library categories).
    lib_rows = {
        c.library_id: c.id
        for c in db.scalars(select(Category).where(Category.library_id.is_not(None))).all()
    }

    for entry in library.get("categories", []):
        for kw in entry.get("keywords") or []:
            # Match at a word boundary so short keywords like "tfl" don't match
            # mid-word (e.g. inside "neTFLix"). Allows prefixes so "sainsbury"
            # still matches "sainsburys".
            if re.search(r"\b" + re.escape(kw.lower()), text):
                db_id = lib_rows.get(entry["id"])
                if db_id is not None:
                    return db_id, KEYWORD_CONFIDENCE
                break
    return None, None
