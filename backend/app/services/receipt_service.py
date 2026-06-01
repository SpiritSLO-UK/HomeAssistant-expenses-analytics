"""Receipt storage, OCR orchestration and transaction matching (spec §21).

Pipeline (spec §21.1): store original file -> run OCR (optional) -> extract
fields -> match to a transaction -> file a review item if uncertain. Everything
works without an OCR engine: the file is stored and the user enters fields
manually, then matching/confirmation proceed exactly the same.
"""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.logging import get_logger
from app.models import Receipt, Transaction, TransactionReceiptMatch
from app.services import ocr_service, receipt_parser, review_service, settings_service
from app.services.household_service import get_or_create_default_household
from app.services.ocr_service import OcrUnavailable

logger = get_logger("app.receipts")

# Matching thresholds (spec §21.4).
AUTO_MATCH = 90
SUGGEST_MATCH = 70
DATE_WINDOW_DAYS = 10


def receipts_dir() -> Path:
    d = Path(settings.database_path).parent / "receipts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def store_upload(db: Session, filename: str, content: bytes) -> tuple[Receipt, bool]:
    """Save an uploaded receipt file (dedup by content hash). Returns
    ``(receipt, created)`` — ``created=False`` means it was already uploaded."""
    file_hash = _hash(content)
    existing = db.scalars(select(Receipt).where(Receipt.file_hash == file_hash)).first()
    if existing is not None:
        return existing, False

    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)[:120] or "receipt"
    path = receipts_dir() / f"{file_hash[:16]}_{safe}"
    path.write_bytes(content)

    receipt = Receipt(
        household_id=get_or_create_default_household(db).id,
        source_filename=filename,
        file_hash=file_hash,
        storage_path=str(path),
        ocr_status="not_processed",
    )
    db.add(receipt)
    db.commit()
    db.refresh(receipt)
    return receipt, True


def run_ocr(db: Session, receipt: Receipt, *, auto_match: bool = True) -> Receipt:
    """Extract fields from the stored file (best-effort). Falls back cleanly to
    'skipped' + a review item when no OCR engine can handle the file."""
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

    parse_conf = fields["parse_confidence"]
    combined = parse_conf if ocr_conf is None else round((ocr_conf + parse_conf) / 2, 2)
    receipt.ocr_confidence = combined
    receipt.ocr_status = "processed"

    low = receipt.total_amount is None or combined < 0.6
    receipt.needs_review = low
    if low:
        _flag(db, receipt, "low_confidence", "Low OCR confidence — check merchant/date/total.")
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


def _flag(db: Session, receipt: Receipt, reason: str, action: str) -> None:
    review_service.add(
        db, item_type="receipt", item_id=receipt.id, reason=reason,
        severity="info", suggested_action=action,
    )


# --- matching (spec §21.4) ---


def _vendor_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    a, b = a.lower(), b.lower()
    if a in b or b in a:
        return 1.0
    wa, wb = set(re.findall(r"[a-z0-9]+", a)), set(re.findall(r"[a-z0-9]+", b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def score_match(receipt: Receipt, txn: Transaction) -> tuple[int, dict]:
    parts: dict[str, int] = {}

    # amount (50)
    amount = 0
    if receipt.total_amount is not None and txn.amount is not None:
        r, t = Decimal(receipt.total_amount), abs(Decimal(txn.amount))
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
        proximity = 20 if d == 0 else 16 if d <= 1 else 12 if d <= 3 else 6 if d <= 7 else 0
    parts["date"] = proximity

    # vendor similarity (20)
    vendor = round(20 * _vendor_similarity(receipt.merchant_raw, txn.merchant_raw or txn.description_raw))
    parts["vendor"] = vendor

    return amount + proximity + vendor, parts


def _candidates(db: Session, receipt: Receipt) -> list[Transaction]:
    conds = [Transaction.is_duplicate.is_(False)]
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


def match(db: Session, receipt: Receipt, mode: str | None = None) -> dict:
    """Score transactions, (re)create a suggested/auto match for the best, or
    file an 'unmatched' review item. Returns ranked candidates (spec §21.4)."""
    mode = mode or settings_service.get(db, settings_service.RECEIPT_MATCH_MODE) or "suggest"

    scored = sorted(
        ((score_match(receipt, t), t) for t in _candidates(db, receipt)),
        key=lambda x: x[0][0],
        reverse=True,
    )[:5]

    # Drop previous *suggested* matches (keep confirmed ones) before re-matching.
    for m in _existing_matches(db, receipt.id):
        if m.match_status in ("suggested", "auto_confirmed"):
            db.delete(m)

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
        status = "auto_confirmed" if auto else "suggested"
        if auto:
            receipt.needs_review = False
            review_service.resolve_for(db, item_type="receipt", item_id=receipt.id, reason="receipt_unmatched")
    else:
        receipt.needs_review = True
        _flag(db, receipt, "receipt_unmatched", "No good transaction match — match it manually.")

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
    db.commit()
    db.refresh(chosen)
    return chosen


def delete(db: Session, receipt: Receipt) -> None:
    if receipt.storage_path:
        Path(receipt.storage_path).unlink(missing_ok=True)
    review_service.resolve_for(db, item_type="receipt", item_id=receipt.id)
    db.delete(receipt)
    db.commit()


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
        "matches": matches,
    }
