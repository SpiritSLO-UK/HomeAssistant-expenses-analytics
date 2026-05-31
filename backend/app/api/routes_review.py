"""Review queue API routes (spec §23, §24)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import ReviewItem
from app.schemas.review import STATUSES, ReviewItemOut, ReviewStatusUpdate
from app.services import review_service

router = APIRouter(prefix="/review", tags=["review"])


@router.get("", response_model=list[ReviewItemOut])
def list_review_items(
    db: Session = Depends(get_db), status: str | None = Query(default="open")
) -> list[ReviewItem]:
    return review_service.list_items(db, status=status)


@router.get("/count")
def review_count(db: Session = Depends(get_db)) -> dict:
    return {"open": review_service.open_count(db)}


@router.patch("/{item_id}", response_model=ReviewItemOut)
def update_review_item(
    item_id: int, payload: ReviewStatusUpdate, db: Session = Depends(get_db)
) -> ReviewItem:
    item = db.get(ReviewItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Review item not found")
    if payload.status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown status. One of: {sorted(STATUSES)}")
    return review_service.set_status(db, item, payload.status)
