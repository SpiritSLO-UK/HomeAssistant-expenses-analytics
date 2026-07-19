"""Category service (spec §15).

Loads the bundled category library into the database, provides CRUD, and a
keyword matcher used as a fallback step in auto-categorisation (spec §15.1).
"""

from __future__ import annotations

import json
import re
from functools import cache, lru_cache
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.session import dml_rowcount
from app.logging import get_logger
from app.models import (
    Budget,
    Category,
    ChildAllocation,
    ReceiptItem,
    Rule,
    Subscription,
    Transaction,
    TransactionSplit,
    Vendor,
)
from app.services import settings_service
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


@cache
def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Compile (once) the word-boundary matcher for a keyword and cache it.

    Keyword regexes are otherwise recompiled on every ``categorise_text`` call —
    hundreds of times per import batch (SR-A2). Matches at a word boundary so a
    short keyword like "tfl" doesn't match mid-word ("neTFLix"), while allowing
    prefixes so "sainsbury" still matches "sainsburys".
    """
    return re.compile(r"\b" + re.escape(keyword.lower()))


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


def resolve_library_category(db: Session, library_id: str) -> int | None:
    """Return the DB id for a library category, creating it (and any missing
    parent) from the bundled defaults if it isn't seeded yet.

    Used to force a specific category on synthetic rows (e.g. earned Curve Cash →
    ``income.cashback``) without re-running the whole library import — so it also
    works on an existing DB that predates a newly-added library category.
    """
    existing = db.scalars(
        select(Category).where(Category.library_id == library_id)
    ).first()
    if existing is not None:
        return existing.id
    entry = next(
        (c for c in load_library().get("categories", []) if c["id"] == library_id), None
    )
    if entry is None:
        return None
    household = get_or_create_default_household(db)
    parent_id = None
    if entry.get("parent_id"):
        parent_id = resolve_library_category(db, entry["parent_id"])
    category = Category(
        library_id=library_id,
        household_id=household.id,
        is_system=True,
        name=entry["name"],
        path=entry["name"],
        parent_id=parent_id,
        icon=entry.get("icon"),
        colour=entry.get("colour"),
        privacy_sensitivity=entry.get("privacy_sensitivity", "normal"),
        is_budgetable=entry.get("is_budgetable", True),
    )
    db.add(category)
    db.flush()
    return category.id


def list_categories(db: Session, include_inactive: bool = False) -> list[Category]:
    stmt = select(Category)
    if not include_inactive:
        stmt = stmt.where(Category.is_active.is_(True))
    return list(db.scalars(stmt.order_by(Category.name)).all())


def get_category(db: Session, category_id: int) -> Category | None:
    return db.get(Category, category_id)


def get_privacy_default(db: Session) -> str:
    """The household's default cloud-AI privacy level (backlog #28)."""
    level = settings_service.get(db, settings_service.CLOUD_AI_PRIVACY_DEFAULT)
    return level if level in PRIVACY_LEVELS else "normal"


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
        # New categories inherit the household default unless one is given.
        privacy_sensitivity=data.get("privacy_sensitivity") or get_privacy_default(db),
        is_system=False,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def set_all_privacy(db: Session, level: str) -> int:
    """Set every category's cloud-AI privacy level at once and remember it as the
    household default (so new categories inherit it). Returns the count updated.
    Raises ``ValueError`` for an unknown level."""
    if level not in PRIVACY_LEVELS:
        raise ValueError(f"privacy level must be one of {list(PRIVACY_LEVELS)}")
    result = db.execute(
        update(Category).values(privacy_sensitivity=level).execution_options(synchronize_session=False)
    )
    settings_service.set_value(db, settings_service.CLOUD_AI_PRIVACY_DEFAULT, level)
    db.commit()
    return dml_rowcount(result) or 0


# Fields a PATCH may change. Excludes managed columns (id/household_id/is_system/
# path) so a blind setattr can't overwrite them (SR-A2).
_UPDATABLE_FIELDS = frozenset(
    {"name", "description", "icon", "colour", "is_budgetable", "parent_id", "privacy_sensitivity"}
)


def update_category(db: Session, category_id: int, data: dict) -> Category | None:
    category = db.get(Category, category_id)
    if category is None:
        return None
    old_name = category.name
    for field, value in data.items():
        if field in _UPDATABLE_FIELDS:
            setattr(category, field, value)
    # Keep the path in sync only when it mirrored the (old) name.
    if "name" in data and category.path == old_name:
        category.path = data["name"]
    db.commit()
    db.refresh(category)
    return category


def delete_category(db: Session, category_id: int) -> bool:
    """Delete a category (system or user). Anything referencing it — transactions,
    splits, budgets, vendor defaults, receipt items, allocations, child categories
    — has its link set to NULL via the FK, so the data survives as uncategorised
    (the transaction stays the source of truth)."""
    category = db.get(Category, category_id)
    if category is None:
        return False
    db.delete(category)
    db.commit()
    return True


def merge_category(db: Session, source_id: int, target_id: int) -> Category | None:
    """Merge ``source_id`` into ``target_id``: re-point every reference from the
    source to the target, then delete the source. Returns the target, ``None`` if
    either id is unknown. Raises ``ValueError`` if asked to merge into itself."""
    if source_id == target_id:
        raise ValueError("Cannot merge a category into itself.")
    source = db.get(Category, source_id)
    target = db.get(Category, target_id)
    if source is None or target is None:
        return None

    opts = {"synchronize_session": False}
    for model in (Transaction, TransactionSplit, Budget, ReceiptItem, ChildAllocation, Subscription):
        db.execute(
            update(model).where(model.category_id == source_id).values(category_id=target_id).execution_options(**opts)
        )
    db.execute(
        update(Vendor).where(Vendor.default_category_id == source_id)
        .values(default_category_id=target_id).execution_options(**opts)
    )
    # Re-parent any child categories of the source onto the target.
    db.execute(
        update(Category).where(Category.parent_id == source_id)
        .values(parent_id=target_id).execution_options(**opts)
    )
    # Rules that *set* this category hold the id as a string in ``action_value``.
    db.execute(
        update(Rule).where(Rule.action_type == "set_category", Rule.action_value == str(source_id))
        .values(action_value=str(target_id)).execution_options(**opts)
    )
    # ...and rules that *match on* this category (category_equals) hold the id in
    # ``condition_value``; re-point them too, or they'd silently stop matching once
    # the source category is deleted (SR-4).
    db.execute(
        update(Rule).where(Rule.condition_type == "category_equals", Rule.condition_value == str(source_id))
        .values(condition_value=str(target_id)).execution_options(**opts)
    )

    db.delete(source)
    db.commit()
    db.refresh(target)
    return target


# Process-global cache for the library_id -> db category-id map, plus the cheap
# version signal it was built at. The map is otherwise rebuilt (a full ORM query)
# on every categorise call (SR-A2). Production uses one long-lived engine with a
# fresh Session per request, so a session-scoped cache would never be reused —
# hence a process-global cache invalidated by a content signal instead.
_lib_map_cache: dict[str, int] | None = None
_lib_map_version: tuple | None = None


def invalidate_category_map_cache() -> None:
    """Drop the cached library_id -> id map (used by tests; the version signal
    already invalidates it automatically on any category change)."""
    global _lib_map_cache, _lib_map_version
    _lib_map_cache = None
    _lib_map_version = None


def _category_map_version(db: Session) -> tuple:
    """A cheap signal that changes whenever the library_id -> id mapping could
    have: (row count, max id, max updated_at) over library-linked categories.

    Adding/removing a library-linked category moves the count and/or max id;
    (re)assigning a ``library_id`` bumps ``updated_at`` via the model's onupdate.
    Computed as a single aggregate row — far cheaper than hydrating every row.
    """
    return tuple(
        db.execute(
            select(
                func.count(Category.id),
                func.max(Category.id),
                func.max(Category.updated_at),
            ).where(Category.library_id.is_not(None))
        ).one()
    )


def _library_category_map(db: Session) -> dict[str, int]:
    """library_id -> db category id for seeded library categories, cached and
    rebuilt only when the cheap version signal changes (SR-A2)."""
    global _lib_map_cache, _lib_map_version
    version = _category_map_version(db)
    if _lib_map_cache is None or version != _lib_map_version:
        _lib_map_cache = {
            c.library_id: c.id
            for c in db.scalars(
                select(Category).where(Category.library_id.is_not(None))
            ).all()
        }
        _lib_map_version = version
    return _lib_map_cache


def categorise_text(db: Session, description: str) -> tuple[int | None, float | None]:
    """Suggest a category id for a description via library keyword match.

    Returns (category_id, confidence) or (None, None) if nothing matched.
    """
    if not description:
        return None, None
    text = description.lower()
    library = load_library()

    # Map library_id -> db category id (only seeded library categories). Cached
    # and reused across calls; invalidated on any category change (SR-A2).
    lib_rows = _library_category_map(db)

    # Choose the best matching keyword deterministically, not the first in file order
    # (which let an arbitrary early keyword shadow a better one). Rank by (earliest
    # match position, then longest keyword): a bank description leads with the merchant
    # ("TfL TRAVEL CHARGE", "NETFLIX.COM"), so the earliest match is the strongest
    # signal — this keeps "tfl" → Transport rather than a later, longer generic word
    # ("travel") winning — and a tie at the same position prefers the more specific
    # keyword ("cafe nero" over "cafe").
    best_id: int | None = None
    # Rank by earliest match position, then longest keyword; lowest rank wins.
    best_rank: tuple[int, int] | None = None
    for entry in library.get("categories", []):
        db_id = lib_rows.get(entry["id"])
        if db_id is None:  # library category not seeded → can't be suggested
            continue
        for kw in entry.get("keywords") or []:
            # Word-boundary matcher, compiled once and cached (see _keyword_pattern).
            match = _keyword_pattern(kw).search(text)
            if match is None:
                continue
            rank = (match.start(), -len(kw))
            if best_rank is None or rank < best_rank:
                best_id, best_rank = db_id, rank
    if best_id is not None:
        return best_id, KEYWORD_CONFIDENCE
    return None, None
