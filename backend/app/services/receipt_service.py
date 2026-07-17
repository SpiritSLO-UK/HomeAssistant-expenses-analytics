"""Receipt storage, OCR orchestration and transaction matching (spec §21).

Pipeline (spec §21.1): store original file -> run OCR (optional) -> extract
fields -> match to a transaction -> file a review item if uncertain. Everything
works without an OCR engine: the file is stored and the user enters fields
manually, then matching/confirmation proceed exactly the same.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.logging import get_logger
from app.models import Account, Category, Receipt, Transaction, TransactionReceiptMatch
from app.services import (
    category_service,
    fx_service,
    import_service,
    ocr_service,
    receipt_parser,
    review_service,
    settings_service,
)
from app.services.household_service import get_or_create_default_household
from app.services.ocr_service import OcrUnavailable
from app.services.scope import account_scope_condition, archived_condition

logger = get_logger("app.receipts")

# Matching thresholds (spec §21.4).
AUTO_MATCH = 90
SUGGEST_MATCH = 70
DATE_WINDOW_DAYS = 10


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def receipts_dir() -> Path:
    d = Path(settings.database_path).parent / "receipts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def drop_original(db: Session, receipt: Receipt, *, commit: bool = True) -> None:
    """Delete the stored original file but keep the row + extracted fields
    (retention / 'delete original after processing', backlog #78/#147).

    Idempotent: re-running is harmless. ``archived_at`` records that the original
    is gone; the merchant/date/total/matches stay queryable, but re-OCR is no
    longer possible. ``commit=False`` lets a caller batch this into its own commit.
    """
    if receipt.storage_path:
        Path(receipt.storage_path).unlink(missing_ok=True)
        receipt.storage_path = None
    if receipt.archived_at is None:
        receipt.archived_at = _now()
    if commit:
        db.commit()


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def store_upload(db: Session, filename: str, content: bytes) -> tuple[Receipt, bool]:
    """Save an uploaded receipt file (dedup by content hash). Returns
    ``(receipt, created)`` — ``created=False`` means it was already uploaded."""
    file_hash = _hash(content)
    household = get_or_create_default_household(db)
    # Dedup only within this household and only against receipts that still hold
    # their original file: another tenant's upload must never collapse into ours
    # (latent cross-tenant leak), and an archived receipt (original dropped by
    # retention / "delete after processing") must not be returned in place of a
    # fresh upload — re-uploading is how a user restores the file.
    existing = db.scalars(
        select(Receipt).where(
            Receipt.file_hash == file_hash,
            Receipt.household_id == household.id,
            Receipt.archived_at.is_(None),
        )
    ).first()
    if existing is not None:
        return existing, False

    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)[:120] or "receipt"
    path = receipts_dir() / f"{file_hash[:16]}_{safe}"
    path.write_bytes(content)

    receipt = Receipt(
        household_id=household.id,
        source_filename=filename,
        file_hash=file_hash,
        storage_path=str(path),
        ocr_status="not_processed",
    )
    db.add(receipt)
    db.commit()
    db.refresh(receipt)
    return receipt, True


def _apply_extracted_fields(receipt: Receipt, fields: dict) -> None:
    """Fill receipt fields from OCR output without clobbering manual values."""
    if not receipt.merchant_raw:
        receipt.merchant_raw = fields["merchant_raw"]
    if receipt.receipt_date is None:
        receipt.receipt_date = fields["receipt_date"]
    if receipt.total_amount is None:
        receipt.total_amount = fields["total_amount"]
    if receipt.vat_amount is None:
        receipt.vat_amount = fields["vat_amount"]
    if not receipt.currency:
        receipt.currency = fields["currency"]


def _finalise_ocr_confidence(db: Session, receipt: Receipt, ocr_conf: float | None, fields: dict) -> None:
    """Set the combined OCR confidence and flag low-confidence receipts for review."""
    parse_conf = fields["parse_confidence"]
    combined = parse_conf if ocr_conf is None else round((ocr_conf + parse_conf) / 2, 2)
    receipt.ocr_confidence = combined
    receipt.ocr_status = "processed"

    low = receipt.total_amount is None or combined < 0.6
    receipt.needs_review = low
    if low:
        _flag(db, receipt, "low_confidence", "Low OCR confidence — check merchant/date/total.")


def run_ocr(db: Session, receipt: Receipt, *, auto_match: bool = True) -> Receipt:
    """Extract fields from the stored file (best-effort). Falls back cleanly to
    'skipped' + a review item when OCR is turned off or no engine can handle the file."""
    if not settings_service.get_ocr_enabled(db):
        receipt.ocr_status = "skipped"
        receipt.needs_review = True
        _flag(db, receipt, "low_confidence",
              "OCR is turned off (Settings → Services) — enter the merchant, date and total manually.")
        db.commit()
        return receipt
    path = Path(receipt.storage_path) if receipt.storage_path else None
    if path is None or not path.exists():
        receipt.ocr_status = "failed"
        _flag(db, receipt, "low_confidence", "Receipt file is missing — re-upload it.")
        db.commit()
        return receipt

    if not ocr_service.can_handle(receipt.source_filename or path.name):
        receipt.ocr_status = "skipped"
        receipt.needs_review = True
        _flag(db, receipt, "low_confidence",
              "OCR unavailable for this file — enter the merchant, date and total manually.")
        db.commit()
        return receipt

    try:
        text, ocr_conf = ocr_service.extract_text(path)
    except OcrUnavailable:
        receipt.ocr_status = "skipped"
        receipt.needs_review = True
        _flag(db, receipt, "low_confidence", "OCR unavailable — enter receipt details manually.")
        db.commit()
        return receipt
    except Exception:  # pragma: no cover - engine errors
        logger.warning("OCR failed for receipt %s", receipt.id, exc_info=True)
        receipt.ocr_status = "failed"
        receipt.needs_review = True
        _flag(db, receipt, "low_confidence", "OCR failed — enter receipt details manually.")
        db.commit()
        return receipt

    fields = receipt_parser.extract_fields(text)
    # Don't clobber anything already set manually.
    _apply_extracted_fields(receipt, fields)
    _finalise_ocr_confidence(db, receipt, ocr_conf, fields)
    db.commit()
    db.refresh(receipt)

    if auto_match and receipt.total_amount is not None:
        match(db, receipt)
    return receipt


def set_fields(db: Session, receipt: Receipt, **fields) -> Receipt:
    """Manually set/correct receipt fields. Clears the low-confidence flag once a
    total is present (the user has taken over)."""
    for key in ("merchant_raw", "receipt_date", "total_amount", "vat_amount", "currency"):
        if key in fields and fields[key] is not None:
            setattr(receipt, key, fields[key])
    if receipt.total_amount is not None:
        receipt.needs_review = False
        review_service.resolve_for(db, item_type="receipt", item_id=receipt.id, reason="low_confidence")
    db.commit()
    db.refresh(receipt)
    return receipt


def _propagate_vat(txn: Transaction, receipt: Receipt) -> None:
    """Carry a receipt's VAT onto its matched transaction (business/VAT receipts),
    without clobbering a VAT amount the user already set."""
    if receipt.vat_amount is not None and txn.vat_amount is None:
        txn.vat_amount = receipt.vat_amount


# Sub-manual confidence for a category reused from a receipt's AI extraction, so
# it isn't treated as a locked manual pick (a recheck batch can still revisit it).
_AI_REUSE_CONFIDENCE = 0.8


def _reuse_ai_category(db: Session, receipt: Receipt, txn: Transaction) -> None:
    """Reuse the receipt's AI-suggested category (backlog #110) for its matched
    transaction, so we don't make a *second* AI call just to categorise it. The
    category came back on the same vision call that read the receipt. Only fills an
    **uncategorised** transaction — never overrides a manual/rule/vendor/keyword pick.
    Guards against a dangling id (no DB-level FK on upgraded SQLite) by confirming
    the category still exists."""
    if (
        receipt.ai_category_id
        and txn.category_id is None
        and db.get(Category, receipt.ai_category_id) is not None
    ):
        txn.category_id = receipt.ai_category_id
        if txn.confidence_score is None:
            txn.confidence_score = _AI_REUSE_CONFIDENCE


def _flag(db: Session, receipt: Receipt, reason: str, action: str) -> None:
    review_service.add(
        db, item_type="receipt", item_id=receipt.id, reason=reason,
        severity="info", suggested_action=action,
    )


# --- matching (spec §21.4) ---


def _normalise_vendor(s: str) -> str:
    """Canonicalise a vendor name for comparison: lower-case, spell out ``&`` as
    ``and`` and turn every other punctuation run into a single space. This lets
    trivial punctuation/spacing variants (e.g. ``M&S`` vs ``M & S``) compare as the
    same name instead of scoring apart."""
    s = s.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _vendor_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    a, b = _normalise_vendor(a), _normalise_vendor(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def score_match(receipt: Receipt, txn: Transaction) -> tuple[int, dict]:
    parts: dict[str, int] = {}

    # amount (50)
    amount = 0
    if receipt.total_amount is not None and txn.amount is not None:
        # Compare by magnitude on BOTH sides. The transaction amount is signed
        # (debit = money out, credit = money in) and a refund receipt may be
        # recorded with a negative total; abs()-ing both means a refund receipt
        # matches a credit transaction of the same size instead of being left
        # unmatchable. Matching is direction-agnostic, never hardcoded to a debit.
        r, t = abs(Decimal(receipt.total_amount)), abs(Decimal(txn.amount))
        if r == t:
            amount = 50
        else:
            tol = max(Decimal("0.50"), t * Decimal("0.01"))
            amount = 35 if abs(r - t) <= tol else 0
    parts["amount"] = amount

    # date proximity (20)
    proximity = 0
    if receipt.receipt_date is not None:
        d = abs((receipt.receipt_date - txn.transaction_date).days)
        if d == 0:
            proximity = 20
        elif d <= 1:
            proximity = 16
        elif d <= 3:
            proximity = 12
        elif d <= 7:
            proximity = 6
    parts["date"] = proximity

    # vendor similarity (20)
    vendor = round(20 * _vendor_similarity(receipt.merchant_raw, txn.merchant_raw or txn.description_raw))
    parts["vendor"] = vendor

    return amount + proximity + vendor, parts


def _household_scope_condition(receipt: Receipt) -> list[ColumnElement[bool]]:
    """Restrict candidates to the receipt's own household (orphan txns with no
    household stay visible, matching the shared-account convention). A no-op for a
    single-household install, but it stops a receipt matching another tenant's
    transactions."""
    if receipt.household_id is None:
        return []
    return [or_(Transaction.household_id == receipt.household_id, Transaction.household_id.is_(None))]


def _candidates(
    db: Session, receipt: Receipt, account_ids: set[int] | None = None
) -> list[Transaction]:
    conds: list[ColumnElement[bool]] = [
        Transaction.is_duplicate.is_(False),
        *_household_scope_condition(receipt),
        *account_scope_condition(account_ids),
        *archived_condition(),
    ]
    if receipt.receipt_date is not None:
        conds.append(Transaction.transaction_date >= receipt.receipt_date - timedelta(days=DATE_WINDOW_DAYS))
        conds.append(Transaction.transaction_date <= receipt.receipt_date + timedelta(days=DATE_WINDOW_DAYS))
    return list(db.scalars(select(Transaction).where(*conds)).all())


def _existing_matches(db: Session, receipt_id: int) -> list[TransactionReceiptMatch]:
    return list(
        db.scalars(
            select(TransactionReceiptMatch).where(TransactionReceiptMatch.receipt_id == receipt_id)
        ).all()
    )


def _drop_stale_matches(db: Session, receipt_id: int) -> None:
    """Drop previous *suggested* matches (keep confirmed ones) before re-matching."""
    for m in _existing_matches(db, receipt_id):
        if m.match_status in ("suggested", "auto_confirmed"):
            db.delete(m)


def _record_best_match(db: Session, receipt: Receipt, best_score: int, best_txn: Transaction, *, mode: str) -> str:
    """Create the suggested/auto match for the best candidate and apply the
    auto-confirm side effects. Returns the resulting match status."""
    auto = mode == "auto" and best_score >= AUTO_MATCH
    db.add(
        TransactionReceiptMatch(
            transaction_id=best_txn.id,
            receipt_id=receipt.id,
            match_score=best_score,
            match_status="auto_confirmed" if auto else "suggested",
            matched_by="local_ocr",
        )
    )
    if auto:
        receipt.needs_review = False
        review_service.resolve_for(db, item_type="receipt", item_id=receipt.id, reason="receipt_unmatched")
        _propagate_vat(best_txn, receipt)
        # NOTE: deliberately do NOT drop the original here. This is a purely
        # automatic score-≥90 match that no human has confirmed; a single OCR
        # misread on the amount/date could auto-match the wrong transaction, and
        # dropping the file would irreversibly destroy the sole copy of the
        # receipt (backlog #147). "Delete original after processing" only takes
        # effect on a user-confirmed match (see confirm_match); an auto-match
        # always keeps the file so the user can still verify or re-OCR it.
    return "auto_confirmed" if auto else "suggested"


def match(
    db: Session, receipt: Receipt, mode: str | None = None, *, account_ids: set[int] | None = None
) -> dict:
    """Score transactions, (re)create a suggested/auto match for the best, or
    file an 'unmatched' review item. Returns ranked candidates (spec §21.4).

    ``account_ids`` narrows the candidate transactions to a set of visible accounts
    (``None`` = unrestricted); candidates are always confined to the receipt's own
    household and exclude archived transactions."""
    mode = mode or settings_service.get(db, settings_service.RECEIPT_MATCH_MODE) or "suggest"

    scored = sorted(
        ((score_match(receipt, t), t) for t in _candidates(db, receipt, account_ids)),
        key=lambda x: x[0][0],
        reverse=True,
    )[:5]

    _drop_stale_matches(db, receipt.id)

    candidates = [
        {
            "transaction_id": t.id,
            "score": s[0],
            "breakdown": s[1],
            "transaction_date": t.transaction_date.isoformat(),
            "amount": str(t.amount),
            "description": t.description_raw,
        }
        for (s, t) in scored
    ]

    status = "unmatched"
    if scored and scored[0][0][0] >= SUGGEST_MATCH:
        best_score, best_txn = scored[0][0][0], scored[0][1]
        status = _record_best_match(db, receipt, best_score, best_txn, mode=mode)
    else:
        receipt.needs_review = True
        _flag(
            db, receipt, "receipt_unmatched",
            "No matching transaction — add the recommended one, or match manually.",
        )

    db.commit()
    return {
        "status": status,
        "best_score": candidates[0]["score"] if candidates else 0,
        "candidates": candidates,
    }


def confirm_match(db: Session, receipt: Receipt, transaction_id: int) -> TransactionReceiptMatch:
    """Confirm a receipt↔transaction match (one-to-one): drop other matches for
    this receipt, mark this one confirmed, and clear its review items."""
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise ValueError("Transaction not found")

    chosen: TransactionReceiptMatch | None = None
    for m in _existing_matches(db, receipt.id):
        if m.transaction_id == transaction_id:
            chosen = m
        else:
            db.delete(m)
    if chosen is None:
        chosen = TransactionReceiptMatch(transaction_id=transaction_id, receipt_id=receipt.id)
        db.add(chosen)
    chosen.match_status = "confirmed"
    chosen.matched_by = "user"

    receipt.needs_review = False
    review_service.resolve_for(db, item_type="receipt", item_id=receipt.id, reason="receipt_unmatched")
    review_service.resolve_for(db, item_type="receipt", item_id=receipt.id, reason="low_confidence")
    _propagate_vat(txn, receipt)
    _reuse_ai_category(db, receipt, txn)
    # Processed & matched → optionally drop the original (backlog #147).
    if settings_service.get_receipt_delete_after_processing(db):
        drop_original(db, receipt, commit=False)
    db.commit()
    db.refresh(chosen)
    return chosen


def delete(db: Session, receipt: Receipt) -> None:
    if receipt.storage_path:
        Path(receipt.storage_path).unlink(missing_ok=True)
    review_service.resolve_for(db, item_type="receipt", item_id=receipt.id)
    db.delete(receipt)
    db.commit()


def recommend_transaction(db: Session, receipt: Receipt) -> dict | None:
    """A pre-filled transaction to recommend for an *unmatched* receipt — exactly
    what :func:`create_transaction_from_receipt` would produce, surfaced so the
    user can add it in one click (Receipts page + Review Queue). Returns None when
    there's no amount yet to base it on.

    The suggested category is the AI's pick (if any) else a keyword guess on the
    merchant; the account is left to the caller (the UI defaults to a dedicated
    'Cash & receipts' account)."""
    if receipt.total_amount is None:
        return None
    category_id: int | None = None
    if receipt.ai_category_id and db.get(Category, receipt.ai_category_id) is not None:
        category_id = receipt.ai_category_id
    else:
        category_id, _ = category_service.categorise_text(db, receipt.merchant_raw or "")
    category_name = None
    if category_id is not None:
        cat = db.get(Category, category_id)
        category_name = cat.name if cat is not None else None
    base = settings_service.get_base_currency(db)
    return {
        "merchant": receipt.merchant_raw or f"Receipt #{receipt.id}",
        "transaction_date": (receipt.receipt_date or _now().date()).isoformat(),
        "amount": f"{-abs(Decimal(receipt.total_amount)):.2f}",  # purchase = money out
        "currency": (receipt.currency or base or "GBP")[:3].upper(),
        "category_id": category_id,
        "category_name": category_name,
    }


def to_dict(db: Session, receipt: Receipt) -> dict:
    matches = [
        {
            "transaction_id": m.transaction_id,
            "match_score": m.match_score,
            "match_status": m.match_status,
            "matched_by": m.matched_by,
        }
        for m in _existing_matches(db, receipt.id)
    ]
    # Recommend a transaction only when nothing matched at all (a suggested or
    # confirmed match means the user should review that first).
    recommended = recommend_transaction(db, receipt) if not matches else None
    return {
        "id": receipt.id,
        "source_filename": receipt.source_filename,
        "receipt_date": receipt.receipt_date.isoformat() if receipt.receipt_date else None,
        "merchant_raw": receipt.merchant_raw,
        "total_amount": str(receipt.total_amount) if receipt.total_amount is not None else None,
        "vat_amount": str(receipt.vat_amount) if receipt.vat_amount is not None else None,
        "currency": receipt.currency,
        "ocr_status": receipt.ocr_status,
        "ocr_confidence": receipt.ocr_confidence,
        "needs_review": receipt.needs_review,
        "has_file": bool(receipt.storage_path),
        "matches": matches,
        "recommended_transaction": recommended,
    }


def receipts_for_transaction(db: Session, transaction_id: int) -> list[Receipt]:
    """Receipts linked to a transaction (any match status), newest first."""
    return list(
        db.scalars(
            select(Receipt)
            .join(TransactionReceiptMatch, TransactionReceiptMatch.receipt_id == Receipt.id)
            .where(TransactionReceiptMatch.transaction_id == transaction_id)
            .order_by(Receipt.created_at.desc())
        ).all()
    )


def attach_to_transaction(db: Session, receipt: Receipt, transaction_id: int) -> TransactionReceiptMatch:
    """Link a receipt to a transaction as a user-confirmed match and **keep** the
    original file. Unlike :func:`confirm_match` it never drops the original — the
    whole point of an attached receipt is to be able to view it later."""
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise ValueError("Transaction not found")
    chosen: TransactionReceiptMatch | None = None
    for m in _existing_matches(db, receipt.id):
        if m.transaction_id == transaction_id:
            chosen = m
        else:
            db.delete(m)
    if chosen is None:
        chosen = TransactionReceiptMatch(transaction_id=transaction_id, receipt_id=receipt.id)
        db.add(chosen)
    chosen.match_status = "confirmed"
    chosen.matched_by = "user"
    receipt.needs_review = False
    review_service.resolve_for(db, item_type="receipt", item_id=receipt.id, reason="receipt_unmatched")
    review_service.resolve_for(db, item_type="receipt", item_id=receipt.id, reason="low_confidence")
    _propagate_vat(txn, receipt)
    _reuse_ai_category(db, receipt, txn)
    db.commit()
    db.refresh(chosen)
    return chosen


def create_transaction_from_receipt(db: Session, receipt: Receipt, *, account_id: int) -> Transaction:
    """Materialise a transaction from a receipt's fields and attach the receipt to
    it as a confirmed match (keeps the original).

    For cash or otherwise un-imported purchases where matching found nothing — the
    receipt becomes the source of a real transaction. Receipts are purchases, so
    the amount is recorded as money out (debit); it's converted to base currency
    and auto-categorised exactly like an imported row, and the unmatched review
    item is cleared by the attach.
    """
    if receipt.total_amount is None:
        raise ValueError("Set the receipt total before creating a transaction")
    account = db.get(Account, account_id)
    if account is None:
        raise ValueError("Account not found")

    household = get_or_create_default_household(db)
    base_currency = settings_service.get_base_currency(db)
    fx_mode = settings_service.get_fx_mode(db)

    txn = Transaction(
        household_id=household.id,
        account_id=account.id,
        transaction_date=receipt.receipt_date or _now().date(),
        description_raw=receipt.merchant_raw or f"Receipt #{receipt.id}",
        merchant_raw=receipt.merchant_raw,
        amount=-abs(Decimal(receipt.total_amount)),
        currency=(receipt.currency or base_currency or "GBP")[:3].upper(),
        direction="debit",
        source_hash=_hash(f"receipt-txn:{receipt.id}".encode()),
        needs_review=False,
    )
    db.add(txn)
    db.flush()
    fx_service.convert_transaction(db, txn, base_currency, fx_mode, allow_fetch=(fx_mode == "frankfurter"))
    import_service.auto_categorise(db, txn)
    db.commit()
    db.refresh(txn)

    attach_to_transaction(db, receipt, txn.id)
    return txn
