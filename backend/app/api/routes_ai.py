"""AI gateway API routes (spec §22, §24)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
    CloudBatchPreview,
    CloudBatchSendRequest,
    CloudBatchSendResult,
)
from app.services import ai_service, auth_service
from app.services.ai_provider import AIError
from app.services.ai_service import AIDisabled

router = APIRouter(prefix="/ai", tags=["ai"])


def _scope(request: Request, db: Session) -> set[int] | None:
    return auth_service.visible_account_scope(request, db)


@router.get("/status", response_model=AIStatus)
def ai_status(db: Session = Depends(get_db)) -> dict:
    return ai_service.status(db)


@router.get("/requests", response_model=list[AIRequestOut])
def ai_requests(
    include_archived: bool = Query(default=False, description="Include archived (aged-out) entries"),
    db: Session = Depends(get_db),
):
    """The AI audit log (spec §22.6)."""
    return ai_service.list_requests(db, include_archived=include_archived)


@router.post("/classify/{transaction_id}", response_model=ClassifyResult)
def classify(transaction_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    """Ask AI to suggest a category (suggestion only — never applied here).
    In cloud_manual mode this returns ``approval_required``; approve it via
    ``/api/ai/requests/{id}/approve``."""
    txn = db.get(Transaction, transaction_id)
    scope = _scope(request, db)
    if txn is None or (scope is not None and txn.account_id is not None and txn.account_id not in scope):
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
def classify_batch(
    request: Request, limit: int = Query(default=25, ge=1, le=100), db: Session = Depends(get_db)
) -> dict:
    """Suggest categories for uncategorised transactions (local_llm only).
    Suggestions only — apply with /api/ai/apply after the user approves."""
    try:
        return ai_service.classify_batch(db, limit=limit, account_ids=_scope(request, db))
    except AIDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/apply", response_model=ApplyResult)
def apply(payload: ApplyRequest, db: Session = Depends(get_db)) -> dict:
    """Apply user-approved AI category suggestions (treated as manual choices)."""
    items = [{"transaction_id": i.transaction_id, "category_id": i.category_id} for i in payload.items]
    return {"applied": ai_service.apply_suggestions(db, items)}


@router.post("/cloud-batch/prepare", response_model=CloudBatchPreview)
def cloud_batch_prepare(
    request: Request, limit: int = Query(default=25, ge=1, le=100), db: Session = Depends(get_db)
) -> dict:
    """Stage 1 of a cloud batch (spec §22.3, §22.5): preview the redacted payloads
    that *would* be sent for uncategorised transactions. Nothing is sent yet."""
    try:
        return ai_service.cloud_batch_prepare(db, limit=limit, account_ids=_scope(request, db))
    except AIDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/cloud-batch/send", response_model=CloudBatchSendResult)
def cloud_batch_send(payload: CloudBatchSendRequest, db: Session = Depends(get_db)) -> dict:
    """Stage 2 of a cloud batch: send the approved redacted requests, reject the
    rest, and return suggestions to review (apply via /api/ai/apply)."""
    try:
        return ai_service.cloud_batch_send(
            db, approve_ids=payload.approve_ids, reject_ids=payload.reject_ids
        )
    except AIDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
