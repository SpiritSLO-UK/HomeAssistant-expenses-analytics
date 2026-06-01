"""Vendor service (spec §16).

Vendor normalisation maps a raw bank description to a canonical vendor via
aliases (exact / contains / regex / fuzzy), and can apply the vendor's default
category to a transaction.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models import Transaction, Vendor, VendorAlias
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


def match_vendor(db: Session, description: str) -> tuple[Vendor | None, str | None]:
    """Return (vendor, match_type) for the best alias match, or (None, None)."""
    if not description:
        return None, None
    rows = db.execute(
        select(VendorAlias, Vendor).join(Vendor, VendorAlias.vendor_id == Vendor.id)
    ).all()

    best: tuple[int, Vendor, str] | None = None
    for alias, vendor in rows:
        if _alias_matches(alias, description):
            rank = _MATCH_PRECEDENCE.get(alias.match_type or "contains", 0)
            if best is None or rank > best[0]:
                best = (rank, vendor, alias.match_type or "contains")
    if best is None:
        return None, None
    return best[1], best[2]


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
    """Transaction count and total spend for a vendor."""
    count = db.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.merchant_id == vendor_id)
    ) or 0
    total = db.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
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
        canonical = signature.title() if signature.isupper() else signature
        household = get_or_create_default_household(db)
        vendor = Vendor(
            household_id=household.id,
            canonical_name=canonical or description[:60],
            display_name=canonical or description[:60],
            created_by="user",
        )
        db.add(vendor)
        db.flush()
        db.add(
            VendorAlias(
                vendor_id=vendor.id,
                alias=signature or description,
                match_type="contains",
                source="user",
            )
        )
    vendor.default_category_id = category_id
    db.commit()
    db.refresh(vendor)
    return vendor
