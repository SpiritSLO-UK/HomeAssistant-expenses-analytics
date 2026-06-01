"""AI gateway API routes (spec §22, §24)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AIRequest, Transaction
from app.schemas.ai import (
    AIRequestOut,
    AIStatus,
    ApplyRequest,
    ApplyResult,
    BatchResult,
    ClassifyResult,
)
from app.services import ai_service
from app.services.ai_provider import AIError
from app.services.ai_service import AIDisabled

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/status", response_model=AIStatus)
def ai_status(db: Session = Depends(get_db)) -> dict:
    return ai_service.status(db)


@router.get("/requests", response_model=list[AIRequestOut])
def ai_requests(db: Session = Depends(get_db)):
    """The AI audit log (spec §22.6)."""
    return ai_service.list_requests(db)


@router.post("/classify/{transaction_id}", response_model=ClassifyResult)
def classify(transaction_id: int, db: Session = Depends(get_db)) -> dict:
    """Ask AI to suggest a category (suggestion only — never applied here).
    In cloud_manual mode this returns ``approval_required``; approve it via
    ``/api/ai/requests/{id}/approve``."""
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    try:
        return ai_service.classify_transaction(db, txn)
    except AIDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _get_request(db: Session, request_id: int) -> AIRequest:
    req = db.get(AIRequest, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="AI request not found")
    return req


@router.post("/requests/{request_id}/approve", response_model=ClassifyResult)
def approve_request(request_id: int, db: Session = Depends(get_db)) -> dict:
    """Approve a pending cloud request and send it (spec §22.5)."""
    req = _get_request(db, request_id)
    try:
        return ai_service.run_request(db, req)
    except AIDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/requests/{request_id}/reject", response_model=AIRequestOut)
def reject_request(request_id: int, db: Session = Depends(get_db)) -> AIRequest:
    """Reject a pending cloud request — nothing is sent (spec §22.5)."""
    return ai_service.reject_request(db, _get_request(db, request_id))


@router.post("/classify-batch", response_model=BatchResult)
def classify_batch(limit: int = Query(default=25, ge=1, le=100), db: Session = Depends(get_db)) -> dict:
    """Suggest categories for uncategorised transactions (local_llm only).
    Suggestions only — apply with /api/ai/apply after the user approves."""
    try:
        return ai_service.classify_batch(db, limit=limit)
    except AIDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/apply", response_model=ApplyResult)
def apply(payload: ApplyRequest, db: Session = Depends(get_db)) -> dict:
    """Apply user-approved AI category suggestions (treated as manual choices)."""
    items = [{"transaction_id": i.transaction_id, "category_id": i.category_id} for i in payload.items]
    return {"applied": ai_service.apply_suggestions(db, items)}
