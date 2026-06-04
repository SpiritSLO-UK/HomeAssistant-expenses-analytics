"""Travel / spend-abroad API (backlog: holidays by country/currency).

Read-only foreign-spend analytics (account-scoped, archived-excluded) plus a
write-gated action to turn a detected trip into a Project.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import auth_service, travel_service

router = APIRouter(prefix="/travel", tags=["travel"])


def _scope(request: Request, db: Session) -> set[int] | None:
    return auth_service.visible_account_scope(request, db)


@router.get("/by-currency")
def by_currency(request: Request, db: Annotated[Session, Depends(get_db)]) -> dict:
    return travel_service.by_currency(db, account_ids=_scope(request, db))


@router.get("/history")
def history(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    months: Annotated[int, Query(ge=1, le=60)] = 12,
) -> dict:
    """Foreign-spend over-time series (base currency) for the Travel chart."""
    return travel_service.history(db, account_ids=_scope(request, db), months=months)


@router.get("/trips")
def trips(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    gap_days: Annotated[int, Query(ge=1, le=120)] = travel_service.DEFAULT_TRIP_GAP_DAYS,
) -> list[dict]:
    return travel_service.detect_trips(db, account_ids=_scope(request, db), gap_days=gap_days)


class TripProjectRequest(BaseModel):
    name: str
    transaction_ids: list[int]
    budget_amount: Decimal | None = None


@router.post("/trips/project", status_code=201, responses={400: {"description": "Bad request"}})
def trip_to_project(payload: TripProjectRequest, request: Request, db: Annotated[Session, Depends(get_db)]) -> dict:
    """Turn a detected trip into a Project (write-gated by the auth middleware)."""
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="A project name is required.")
    if not payload.transaction_ids:
        raise HTTPException(status_code=400, detail="No transactions to add.")
    project = travel_service.create_project_from_trip(
        db,
        name=payload.name,
        transaction_ids=payload.transaction_ids,
        budget_amount=payload.budget_amount,
        account_ids=_scope(request, db),
    )
    return {"project_id": project.id, "name": project.name}
