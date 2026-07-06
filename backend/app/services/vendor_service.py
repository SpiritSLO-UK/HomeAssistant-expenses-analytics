"""Vendor service (spec §16).

Vendor normalisation maps a raw bank description to a canonical vendor via
aliases (exact / contains / regex / fuzzy), and can apply the vendor's default
category to a transaction.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from difflib import SequenceMatcher

from sqlalchemy import func, literal, select, update
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models import Receipt, Subscription, Transaction, Vendor, VendorAlias
from app.services.household_service import get_or_create_default_household

logger = get_logger(__name__)

# Precedence for alias match types (higher wins) and the confidence each implies.
_MATCH_PRECEDENCE = {"exact": 4, "regex": 3, "contains": 2, "fuzzy": 1}
_MATCH_CONFIDENCE = {"exact": 0.98, "regex": 0.95, "contains": 0.90, "fuzzy": 0.80}
_FUZZY_THRESHOLD = 0.85


def _alias_matches(alias: VendorAlias, description: str) -> bool:
    text = description.lower()
    needle = alias.alias.lower()
    match_type = alias.match_type or "contains"
    if match_type == "exact":
        return text == needle
    if match_type == "contains":
        return needle in text
    if match_type == "regex":
        try:
            return re.search(alias.alias, description, re.IGNORECASE) is not None
        except re.error:
            return False
    if match_type == "fuzzy":
        return SequenceMatcher(None, needle, text).ratio() >= _FUZZY_THRESHOLD
    return False


def _consider(
    best: tuple[int, int, Vendor, str] | None,
    alias: VendorAlias,
    vendor: Vendor,
) -> tuple[int, int, Vendor, str] | None:
    """Fold one matching alias into the running best.

    Ranks by match-type precedence, breaking ties on the longer (more specific)
    alias rather than DB insertion order (SR-A3 §3)."""
    rank = _MATCH_PRECEDENCE.get(alias.match_type or "contains", 0)
    specificity = len(alias.alias or "")
    if best is None or (rank, specificity) > (best[0], best[1]):
        return (rank, specificity, vendor, alias.match_type or "contains")
    return best


def match_vendor(db: Session, description: str) -> tuple[Vendor | None, str | None]:
    """Return (vendor, match_type) for the best alias match, or (None, None).

    Performance (SR-A3 §1): exact/contains aliases are the common case and are
    now resolved by a SQL prefilter (``LIKE`` on the lowered alias) instead of a
    full Python scan over every alias. Because ``exact`` (rank 4) and
    ``contains`` (rank 2) outrank ``regex`` (3) only partially, we still fall
    back to a Python pass — but only over ``regex``/``fuzzy`` aliases (which SQL
    can't express) — and only when needed. The best match is chosen by the same
    precedence/tie-break rules as before, so results are unchanged."""
    if not description:
        return None, None

    text = description.lower()
    best: tuple[int, int, Vendor, str] | None = None

    # 1) SQL prefilter for the string match types (exact / contains). ``exact``
    #    means the whole description equals the alias; ``contains`` means the
    #    (lowered) alias appears somewhere in the (lowered) description. Both are
    #    expressed against the lowered alias, mirroring ``_alias_matches``. The
    #    ``contains`` test is a substring check with the *description* as the
    #    haystack, so we pass the literal description to ``like`` and match rows
    #    whose lowered alias is a substring of it via a bound-literal LIKE.
    lowered_alias = func.lower(VendorAlias.alias)
    # ``:text LIKE '%' || lower(alias) || '%'`` — the alias is the pattern needle,
    # the (constant) description is the haystack. This compiles portably (``||``
    # on SQLite, ``concat``/``||`` elsewhere). The prefilter may be slightly loose
    # if an alias itself contains LIKE wildcards, but every returned row is
    # re-verified below by ``_alias_matches``, so results stay exact — the SQL is
    # purely a candidate-narrowing step (no false negatives for real substrings).
    contains_expr = literal(text).like(literal("%").concat(lowered_alias).concat("%"))
    string_rows = db.execute(
        select(VendorAlias, Vendor)
        .join(Vendor, VendorAlias.vendor_id == Vendor.id)
        .where(
            ((VendorAlias.match_type == "exact") & (lowered_alias == text))
            | ((VendorAlias.match_type == "contains") & contains_expr)
        )
    ).all()

    for alias, vendor in string_rows:
        # Re-verify in Python so the exact/contains semantics are identical to
        # the previous implementation (belt-and-braces against LIKE wildcards in
        # the alias text).
        if _alias_matches(alias, description):
            best = _consider(best, alias, vendor)

    # 2) Fall back to a Python pass over regex/fuzzy aliases only — these can't
    #    be expressed in portable SQL. This is the small remainder, not the whole
    #    table, so the per-transaction cost is bounded by how many regex/fuzzy
    #    aliases exist rather than the total alias count.
    fuzzy_rows = db.execute(
        select(VendorAlias, Vendor)
        .join(Vendor, VendorAlias.vendor_id == Vendor.id)
        .where(VendorAlias.match_type.in_(("regex", "fuzzy")))
    ).all()
    for alias, vendor in fuzzy_rows:
        if _alias_matches(alias, description):
            best = _consider(best, alias, vendor)

    if best is None:
        return None, None
    return best[2], best[3]


def normalise_transaction(db: Session, txn: Transaction) -> bool:
    """Match a vendor for the transaction and apply it.

    Sets ``merchant_id`` and bumps the vendor's ``last_seen_at``. If the
    transaction has no category yet and the vendor has a default, applies it
    (confidence per spec §15.2 vendor default = 0.90). Returns True if a vendor
    was matched.
    """
    vendor, match_type = match_vendor(db, txn.description_raw)
    if vendor is None:
        return False
    # Don't overwrite a vendor already set explicitly (e.g. by a rule).
    if txn.merchant_id is None:
        txn.merchant_id = vendor.id
    vendor.last_seen_at = datetime.now(UTC)
    if txn.category_id is None and vendor.default_category_id is not None:
        txn.category_id = vendor.default_category_id
        txn.confidence_score = _MATCH_CONFIDENCE.get(match_type or "contains", 0.90)
    return True


# --- CRUD ---

def list_vendors(db: Session) -> list[Vendor]:
    return list(db.scalars(select(Vendor).order_by(Vendor.canonical_name)).all())


def get_vendor(db: Session, vendor_id: int) -> Vendor | None:
    return db.get(Vendor, vendor_id)


def vendor_stats(db: Session, vendor_id: int) -> dict:
    """Transaction count and total spend for a vendor, in base currency.

    Sums each transaction's converted ``base_amount`` (SR-3) rather than the raw
    ``amount``, so a vendor billed in several currencies gets a meaningful total
    instead of figures added 1:1. Transactions still awaiting a rate (``base_amount``
    NULL) are left out of the sum, like every other base-currency total."""
    count = db.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.merchant_id == vendor_id)
    ) or 0
    total = db.scalar(
        select(func.coalesce(func.sum(Transaction.base_amount), 0)).where(
            Transaction.merchant_id == vendor_id
        )
    ) or 0
    return {"transaction_count": int(count), "total_amount": str(total)}


def create_vendor(db: Session, data: dict) -> Vendor:
    household = get_or_create_default_household(db)
    vendor = Vendor(
        household_id=household.id,
        canonical_name=data["canonical_name"],
        display_name=data.get("display_name") or data["canonical_name"],
        default_category_id=data.get("default_category_id"),
        service_type=data.get("service_type"),
        website=data.get("website"),
        notes=data.get("notes"),
        created_by=data.get("created_by", "user"),
    )
    db.add(vendor)
    db.flush()
    # Optionally create an initial alias.
    if data.get("alias"):
        db.add(
            VendorAlias(
                vendor_id=vendor.id,
                alias=data["alias"],
                match_type=data.get("match_type", "contains"),
                source="user",
            )
        )
    db.commit()
    db.refresh(vendor)
    return vendor


def update_vendor(db: Session, vendor_id: int, data: dict) -> Vendor | None:
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        return None
    for field, value in data.items():
        setattr(vendor, field, value)
    db.commit()
    db.refresh(vendor)
    return vendor


def add_alias(db: Session, vendor_id: int, alias: str, match_type: str = "contains") -> VendorAlias | None:
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        return None
    row = VendorAlias(vendor_id=vendor_id, alias=alias, match_type=match_type, source="user")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def set_default_category(db: Session, vendor_id: int, category_id: int | None) -> Vendor | None:
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        return None
    vendor.default_category_id = category_id
    db.commit()
    db.refresh(vendor)
    return vendor


def delete_vendor(db: Session, vendor_id: int) -> bool:
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        return False
    db.delete(vendor)
    db.commit()
    return True


def _move_aliases(db: Session, source: Vendor, target: Vendor) -> None:
    """Move the source vendor's aliases onto the target, dropping exact-duplicate
    alias strings (case-insensitive). Uses the relationship so ``delete-orphan``
    won't re-delete the moved rows when the source vendor is deleted."""
    target_aliases = {a.alias.lower() for a in target.aliases}
    for alias in list(source.aliases):
        key = (alias.alias or "").lower()
        if key in target_aliases:
            db.delete(alias)  # exact-duplicate alias string → drop it
        else:
            target.aliases.append(alias)  # back_populates detaches it from source
            target_aliases.add(key)


def merge_vendor(db: Session, source_id: int, target_id: int) -> Vendor | None:
    """Merge ``source_id`` into ``target_id``: re-point every reference from the
    source to the target, fold the source's aliases / default category /
    ``last_seen_at`` onto the target, then delete the source. Returns the target,
    ``None`` if either id is unknown. Raises ``ValueError`` on a self-merge.

    Mirrors ``category_service.merge_category``: bulk ``update`` with
    ``synchronize_session=False`` for the foreign-key re-points."""
    if source_id == target_id:
        raise ValueError("Cannot merge a vendor into itself.")
    source = db.get(Vendor, source_id)
    target = db.get(Vendor, target_id)
    if source is None or target is None:
        return None

    opts = {"synchronize_session": False}
    # Transactions point at a vendor via ``merchant_id``; receipts/subscriptions
    # via ``vendor_id``. Re-point all of them from source to target.
    db.execute(
        update(Transaction).where(Transaction.merchant_id == source_id)
        .values(merchant_id=target_id).execution_options(**opts)
    )
    for model in (Receipt, Subscription):
        db.execute(
            update(model).where(model.vendor_id == source_id)
            .values(vendor_id=target_id).execution_options(**opts)
        )

    _move_aliases(db, source, target)

    # Fold the source's default category onto the target only if the target lacks one.
    if target.default_category_id is None and source.default_category_id is not None:
        target.default_category_id = source.default_category_id
    # Keep the more recent ``last_seen_at``.
    if source.last_seen_at is not None and (
        target.last_seen_at is None or source.last_seen_at > target.last_seen_at
    ):
        target.last_seen_at = source.last_seen_at

    db.delete(source)
    db.commit()
    db.refresh(target)
    return target


def derive_vendor_signature(text: str) -> str:
    """Best-effort merchant signature: keep leading tokens until one contains a
    digit (drops store numbers/locations). 'TESCO STORES 3142 DARTFORD' -> 'TESCO STORES'."""
    tokens = text.split()
    sig: list[str] = []
    for token in tokens:
        if any(ch.isdigit() for ch in token):
            break
        sig.append(token)
    return " ".join(sig).strip() or text.strip()


def create_from_transaction(db: Session, txn: Transaction, *, name: str | None = None) -> Vendor:
    """Create (or reuse) a vendor for a transaction that has none, and link it.

    The recommended name defaults to the OCR/parsed merchant signature
    (``derive_vendor_signature``) — our deterministic recommendation — unless the
    caller passes an explicit ``name`` (e.g. an AI-suggested vendor). Adds a
    ``contains`` alias for the signature so future imports of the same merchant
    match automatically, and reuses an existing vendor with the same canonical
    name instead of duplicating it. Caller is the 'suggest & confirm' UI, so this
    never fires on its own.
    """
    signature = derive_vendor_signature(txn.merchant_raw or txn.description_raw or "")
    canonical = (name or signature).strip()
    if canonical.isupper():
        canonical = canonical.title()
    if not canonical:
        canonical = (txn.description_raw or "Vendor")[:60]

    vendor = db.scalars(
        select(Vendor).where(func.lower(Vendor.canonical_name) == canonical.lower())
    ).first()
    if vendor is None:
        vendor = Vendor(
            household_id=get_or_create_default_household(db).id,
            canonical_name=canonical,
            display_name=canonical,
            created_by="user",
        )
        db.add(vendor)
        db.flush()

    alias_text = (signature or canonical).strip()
    if alias_text:
        existing_alias = db.scalars(
            select(VendorAlias).where(
                VendorAlias.vendor_id == vendor.id,
                func.lower(VendorAlias.alias) == alias_text.lower(),
            )
        ).first()
        if existing_alias is None:
            db.add(VendorAlias(vendor_id=vendor.id, alias=alias_text, match_type="contains", source="user"))

    txn.merchant_id = vendor.id
    vendor.last_seen_at = datetime.now(UTC)
    db.commit()
    db.refresh(vendor)
    return vendor


def learn_vendor_category(
    db: Session, description: str, merchant_raw: str | None, category_id: int
) -> Vendor:
    """Manual-correction learning (spec §15.3): remember a vendor's category.

    Finds an existing vendor for the description or creates one (with a
    ``contains`` alias derived from the merchant signature), then sets its
    default category. Rule-based learning proper arrives in Stage 3.
    """
    vendor, _ = match_vendor(db, description)
    if vendor is None:
        signature = (merchant_raw or derive_vendor_signature(description)).strip()
        canonical = (signature.title() if signature.isupper() else signature) or description[:60]
        # Reuse an existing vendor with this canonical name before creating a new one,
        # so a manual correction can't spawn a duplicate when the alias just didn't match
        # (mirrors create_from_transaction; previously this always created a new vendor).
        vendor = db.scalars(
            select(Vendor).where(func.lower(Vendor.canonical_name) == canonical.lower())
        ).first()
        if vendor is None:
            vendor = Vendor(
                household_id=get_or_create_default_household(db).id,
                canonical_name=canonical,
                display_name=canonical,
                created_by="user",
            )
            db.add(vendor)
            db.flush()
        # Add a contains alias for this description if the vendor lacks one, so the next
        # import of the same merchant matches it.
        alias_text = (signature or description).strip()
        if alias_text and not db.scalars(
            select(VendorAlias).where(
                VendorAlias.vendor_id == vendor.id,
                func.lower(VendorAlias.alias) == alias_text.lower(),
            )
        ).first():
            db.add(VendorAlias(vendor_id=vendor.id, alias=alias_text, match_type="contains", source="user"))
    vendor.default_category_id = category_id
    db.commit()
    db.refresh(vendor)
    return vendor
