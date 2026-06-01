"""AI gateway API routes (spec §22, §24)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Transaction
from app.schemas.ai import AIRequestOut, AIStatus, ClassifyResult
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
def classify(
    transaction_id: int,
    approve: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict:
    """Ask AI to suggest a category (suggestion only — never applied here)."""
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    try:
        return ai_service.classify_transaction(db, txn, approved=approve)
    except AIDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
