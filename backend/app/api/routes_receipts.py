"""Receipts API routes (spec §24.10, §21)."""

from __future__ import annotations

import mimetypes
from datetime import date as _date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Receipt, User
from app.schemas.receipts import (
    ConfirmMatchRequest,
    CreateTransactionRequest,
    CreateTransactionResult,
    MatchResult,
    ReceiptOut,
    ReceiptUpdate,
    ReceiptUploadOut,
)
from app.services import ai_service, audit_service, ocr_service, receipt_service
from app.services.ai_provider import AIError
from app.services.ai_service import AIDisabled
from app.services.auth_service import get_current_user
from app.services.household_service import get_or_create_account, get_or_create_default_household

router = APIRouter(prefix="/receipts", tags=["receipts"])

# Dedicated account for transactions materialised from receipts (cash / un-imported
# purchases), when the user doesn't want to attribute them to a real bank account.
CASH_RECEIPTS_ACCOUNT = "Cash & receipts"

MAX_BYTES = 15 * 1024 * 1024  # 15 MB upload cap


@router.get("/status")
def ocr_status() -> dict:
    """Whether local OCR is available (image/PDF), for the UI."""
    return ocr_service.status()


@router.get("", response_model=list[ReceiptOut])
def list_receipts(db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    receipts = db.scalars(select(Receipt).order_by(Receipt.created_at.desc())).all()
    return [receipt_service.to_dict(db, r) for r in receipts]


@router.post(
    "/upload",
    response_model=ReceiptUploadOut,
    status_code=201,
    responses={400: {"description": "Bad request"}, 413: {"description": "Payload too large"}},
)
async def upload_receipt(file: Annotated[UploadFile, File()], db: Annotated[Session, Depends(get_db)]) -> dict:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 15 MB)")

    receipt, created = receipt_service.store_upload(db, file.filename or "receipt", content)
    if created:
        # Best-effort OCR + auto-match; degrades to 'skipped' if no engine.
        receipt_service.run_ocr(db, receipt, auto_match=True)
    result = receipt_service.to_dict(db, receipt)
    # Re-uploading a byte-identical file is deduped by content hash — the existing
    # receipt is returned. Flag it so the UI can say "already imported" instead of
    # silently looking like a fresh upload (mirrors statement-import dedup feedback).
    result["already_imported"] = not created
    return result


def _get(db: Session, receipt_id: int) -> Receipt:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return receipt


@router.get("/{receipt_id}", response_model=ReceiptOut, responses={404: {"description": "Not found"}})
def get_receipt(receipt_id: int, db: Annotated[Session, Depends(get_db)]) -> dict:
    return receipt_service.to_dict(db, _get(db, receipt_id))


# Media types we're willing to render INLINE in the browser / in-app viewer.
# Deliberately raster-image + PDF only: image/svg+xml is excluded because an SVG can
# carry <script>, and serving it inline would run in our origin (CR-SEC-14 / L2).
_INLINE_SAFE_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "application/pdf",
}


@router.get("/{receipt_id}/file", responses={404: {"description": "Not found"}})
def get_receipt_file(receipt_id: int, db: Annotated[Session, Depends(get_db)]) -> FileResponse:
    """Serve the stored original (image/PDF) so an attached receipt can be viewed.
    404 if retention has dropped the original (#78/#147)."""
    receipt = _get(db, receipt_id)
    path = Path(receipt.storage_path) if receipt.storage_path else None
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Receipt original is not available")
    guessed = mimetypes.guess_type(receipt.source_filename or path.name)[0]
    # Preview a known-safe image/PDF inline; serve anything else (SVG, an unexpected
    # or unknown type) as an opaque download so it can't execute in our origin.
    # X-Content-Type-Options: nosniff stops the browser MIME-sniffing past the type.
    if guessed in _INLINE_SAFE_TYPES:
        media_type, disposition = guessed, "inline"
    else:
        media_type, disposition = "application/octet-stream", "attachment"
    return FileResponse(
        path,
        media_type=media_type,
        filename=receipt.source_filename or path.name,
        content_disposition_type=disposition,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.post("/{receipt_id}/ocr", response_model=ReceiptOut, responses={404: {"description": "Not found"}})
def rerun_ocr(receipt_id: int, db: Annotated[Session, Depends(get_db)]) -> dict:
    receipt = _get(db, receipt_id)
    receipt_service.run_ocr(db, receipt, auto_match=True)
    return receipt_service.to_dict(db, receipt)


def _receipt_fields_from_ai(fields: dict) -> dict:
    """Map the vision model's {merchant, date, total, currency} to receipt fields."""
    out: dict = {}
    merchant = str(fields.get("merchant") or "").strip()
    if merchant:
        out["merchant_raw"] = merchant[:300]
    raw_date = str(fields.get("date") or "").strip()
    if raw_date and raw_date.lower() != "null":
        try:
            out["receipt_date"] = _date.fromisoformat(raw_date[:10])
        except ValueError:
            pass
    raw_total = str(fields.get("total") or "").strip().replace(",", "")
    if raw_total:
        try:
            total = Decimal(raw_total)
            if total >= 0:
                out["total_amount"] = total
        except InvalidOperation:
            pass
    cur = str(fields.get("currency") or "").strip()
    if cur and cur.lower() != "null":
        out["currency"] = cur[:3].upper()
    return out


@router.post(
    "/{receipt_id}/ai-extract",
    response_model=ReceiptOut,
    responses={400: {"description": "AI off / not an image"}, 404: {"description": "Not found"},
               502: {"description": "AI error"}},
)
def ai_extract_receipt(
    receipt_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Opt-in vision-AI fallback: read merchant/date/total from the receipt image
    when OCR couldn't. The frontend warns first (the image is sent to the AI, and
    an image can't be redacted). Image receipts only."""
    receipt = _get(db, receipt_id)
    path = Path(receipt.storage_path) if receipt.storage_path else None
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Receipt original is not available")
    mime = mimetypes.guess_type(receipt.source_filename or path.name)[0] or ""
    if not mime.startswith("image/"):
        raise HTTPException(status_code=400, detail="AI extraction needs an image receipt (not a PDF).")
    content = path.read_bytes()
    try:
        fields = ai_service.extract_receipt_image(db, content, mime)
    except AIDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=502, detail=f"AI extraction failed: {exc}") from exc
    audit_service.record_image_sent(db, actor=user.display_name, kind="receipt", size=len(content))
    # Persist the AI's suggested category so the matched/created transaction can
    # reuse it without a second AI call (backlog #110).
    if fields.get("category_id"):
        receipt.ai_category_id = fields["category_id"]
    db.commit()
    updates = _receipt_fields_from_ai(fields)
    if updates:
        receipt_service.set_fields(db, receipt, **updates)
    if receipt.total_amount is not None:
        receipt_service.match(db, receipt)
    return receipt_service.to_dict(db, receipt)


@router.patch("/{receipt_id}", response_model=ReceiptOut, responses={404: {"description": "Not found"}})
def update_receipt(receipt_id: int, payload: ReceiptUpdate, db: Annotated[Session, Depends(get_db)]) -> dict:
    receipt = _get(db, receipt_id)
    receipt_service.set_fields(db, receipt, **payload.model_dump(exclude_unset=True))
    return receipt_service.to_dict(db, receipt)


@router.post(
    "/{receipt_id}/match",
    response_model=MatchResult,
    responses={400: {"description": "Bad request"}, 404: {"description": "Not found"}},
)
def match_receipt(receipt_id: int, db: Annotated[Session, Depends(get_db)]) -> dict:
    receipt = _get(db, receipt_id)
    if receipt.total_amount is None:
        raise HTTPException(status_code=400, detail="Set the receipt total before matching")
    return receipt_service.match(db, receipt)


@router.post(
    "/{receipt_id}/confirm-match",
    response_model=ReceiptOut,
    responses={400: {"description": "Bad request"}, 404: {"description": "Not found"}},
)
def confirm_match(receipt_id: int, payload: ConfirmMatchRequest, db: Annotated[Session, Depends(get_db)]) -> dict:
    receipt = _get(db, receipt_id)
    try:
        receipt_service.confirm_match(db, receipt, payload.transaction_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return receipt_service.to_dict(db, receipt)


@router.post(
    "/{receipt_id}/create-transaction",
    response_model=CreateTransactionResult,
    responses={400: {"description": "Bad request"}, 404: {"description": "Not found"}},
)
def create_transaction(
    receipt_id: int, payload: CreateTransactionRequest, db: Annotated[Session, Depends(get_db)]
) -> dict:
    """Materialise a transaction from an unmatched receipt. Either pick an existing
    account or set ``new_account`` to use/create a dedicated 'Cash & receipts' one."""
    receipt = _get(db, receipt_id)
    if payload.new_account:
        household = get_or_create_default_household(db)
        account_id = get_or_create_account(db, household, CASH_RECEIPTS_ACCOUNT).id
    elif payload.account_id is not None:
        account_id = payload.account_id
    else:
        raise HTTPException(status_code=400, detail="Choose an account or create a dedicated one.")
    try:
        txn = receipt_service.create_transaction_from_receipt(db, receipt, account_id=account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"transaction_id": txn.id, "receipt": receipt_service.to_dict(db, receipt)}


@router.delete("/{receipt_id}", status_code=204, responses={404: {"description": "Not found"}})
def delete_receipt(receipt_id: int, db: Annotated[Session, Depends(get_db)]) -> None:
    receipt_service.delete(db, _get(db, receipt_id))
