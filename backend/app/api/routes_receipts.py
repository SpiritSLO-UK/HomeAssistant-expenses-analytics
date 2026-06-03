"""Receipts API routes (spec §24.10, §21)."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Receipt
from app.schemas.receipts import (
    ConfirmMatchRequest,
    MatchResult,
    ReceiptOut,
    ReceiptUpdate,
)
from app.services import ocr_service, receipt_service

router = APIRouter(prefix="/receipts", tags=["receipts"])

MAX_BYTES = 15 * 1024 * 1024  # 15 MB upload cap


@router.get("/status")
def ocr_status() -> dict:
    """Whether local OCR is available (image/PDF), for the UI."""
    return ocr_service.status()


@router.get("", response_model=list[ReceiptOut])
def list_receipts(db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    receipts = db.scalars(select(Receipt).order_by(Receipt.created_at.desc())).all()
    return [receipt_service.to_dict(db, r) for r in receipts]


@router.post("/upload", response_model=ReceiptOut, status_code=201)
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
    return receipt_service.to_dict(db, receipt)


def _get(db: Session, receipt_id: int) -> Receipt:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return receipt


@router.get("/{receipt_id}", response_model=ReceiptOut)
def get_receipt(receipt_id: int, db: Annotated[Session, Depends(get_db)]) -> dict:
    return receipt_service.to_dict(db, _get(db, receipt_id))


@router.get("/{receipt_id}/file")
def get_receipt_file(receipt_id: int, db: Annotated[Session, Depends(get_db)]) -> FileResponse:
    """Serve the stored original (image/PDF) so an attached receipt can be viewed.
    404 if retention has dropped the original (#78/#147)."""
    receipt = _get(db, receipt_id)
    path = Path(receipt.storage_path) if receipt.storage_path else None
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Receipt original is not available")
    media_type = mimetypes.guess_type(receipt.source_filename or path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=receipt.source_filename or path.name)


@router.post("/{receipt_id}/ocr", response_model=ReceiptOut)
def rerun_ocr(receipt_id: int, db: Annotated[Session, Depends(get_db)]) -> dict:
    receipt = _get(db, receipt_id)
    receipt_service.run_ocr(db, receipt, auto_match=True)
    return receipt_service.to_dict(db, receipt)


@router.patch("/{receipt_id}", response_model=ReceiptOut)
def update_receipt(receipt_id: int, payload: ReceiptUpdate, db: Annotated[Session, Depends(get_db)]) -> dict:
    receipt = _get(db, receipt_id)
    receipt_service.set_fields(db, receipt, **payload.model_dump(exclude_unset=True))
    return receipt_service.to_dict(db, receipt)


@router.post("/{receipt_id}/match", response_model=MatchResult)
def match_receipt(receipt_id: int, db: Annotated[Session, Depends(get_db)]) -> dict:
    receipt = _get(db, receipt_id)
    if receipt.total_amount is None:
        raise HTTPException(status_code=400, detail="Set the receipt total before matching")
    return receipt_service.match(db, receipt)


@router.post("/{receipt_id}/confirm-match", response_model=ReceiptOut)
def confirm_match(receipt_id: int, payload: ConfirmMatchRequest, db: Annotated[Session, Depends(get_db)]) -> dict:
    receipt = _get(db, receipt_id)
    try:
        receipt_service.confirm_match(db, receipt, payload.transaction_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return receipt_service.to_dict(db, receipt)


@router.delete("/{receipt_id}", status_code=204)
def delete_receipt(receipt_id: int, db: Annotated[Session, Depends(get_db)]) -> None:
    receipt_service.delete(db, _get(db, receipt_id))
