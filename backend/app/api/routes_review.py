"""Review queue API routes (spec §23, §24)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import ReviewItem
from app.schemas.review import STATUSES, ReviewItemOut, ReviewStatusUpdate
from app.services import review_service

router = APIRouter(prefix="/review", tags=["review"])


class ReviewBulkResolve(BaseModel):
    """Body for POST /review/bulk-resolve: the ids to update and the target
    status (defaults to ``resolved``)."""

    ids: list[int]
    status: str = "resolved"


@router.get("", response_model=list[ReviewItemOut])
def list_review_items(
    db: Annotated[Session, Depends(get_db)],
    status: Annotated[str | None, Query()] = "open",
    item_type: Annotated[str | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
) -> list[ReviewItem]:
    return review_service.list_items(db, status=status, item_type=item_type, severity=severity)


@router.post("/bulk-resolve", responses={400: {"description": "Bad request"}})
def bulk_resolve_review_items(
    payload: ReviewBulkResolve, db: Annotated[Session, Depends(get_db)]
) -> dict:
    if payload.status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown status. One of: {sorted(STATUSES)}")
    return {"updated": review_service.bulk_resolve(db, payload.ids, payload.status)}


@router.get("/count")
def review_count(db: Annotated[Session, Depends(get_db)]) -> dict:
    return {"open": review_service.open_count(db)}


@router.patch(
    "/{item_id}",
    response_model=ReviewItemOut,
    responses={400: {"description": "Bad request"}, 404: {"description": "Not found"}},
)
def update_review_item(
    item_id: int, payload: ReviewStatusUpdate, db: Annotated[Session, Depends(get_db)]
) -> ReviewItem:
    item = db.get(ReviewItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Review item not found")
    if payload.status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown status. One of: {sorted(STATUSES)}")
    return review_service.set_status(db, item, payload.status)
