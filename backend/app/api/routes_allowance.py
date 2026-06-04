"""Child allowance API (backlog #82; spec §6, §19).

``/summary`` always serves the **current** user (no id param), so a child can
only ever see their own allowance. Creating/reviewing allocations and child
budgets is a parent action (write-gated to owner/member); the child-role gate in
``main.py`` confines a ``child`` to ``/api/allowance/summary``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.schemas.allowance import AllocationCreate, AllocationOut, AllowanceSummary
from app.services import allowance_service, auth_service
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/allowance", tags=["allowance"])


@router.get("/summary", response_model=AllowanceSummary, responses={404: {"description": "Not found"}})
def allowance_summary(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    user_id: int | None = None,
) -> dict:
    """The current user's allowance. A parent (owner/member) may pass ``user_id``
    to view a child's allowance; for anyone else (incl. a child) ``user_id`` is
    ignored and they only ever see their own."""
    target = user
    if user_id is not None and auth_service.can_write(user.role):
        found = db.get(User, user_id)
        if found is None:
            raise HTTPException(status_code=404, detail="User not found")
        target = found
    return allowance_service.summary(db, target)


@router.post(
    "/allocations", response_model=AllocationOut, status_code=201, responses={400: {"description": "Bad request"}}
)
def create_allocation(payload: AllocationCreate, db: Annotated[Session, Depends(get_db)]) -> dict:
    try:
        row = allowance_service.create_allocation(
            db,
            child_id=payload.child_id,
            transaction_id=payload.transaction_id,
            split_id=payload.split_id,
            category_id=payload.category_id,
            amount=payload.amount,
            description=payload.description,
            as_of=payload.as_of,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return allowance_service.allocation_to_dict(db, row)


@router.get("/allocations", response_model=list[AllocationOut])
def list_allocations(user_id: int, db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    return [allowance_service.allocation_to_dict(db, a) for a in allowance_service.list_allocations(db, user_id)]


@router.delete("/allocations/{allocation_id}", status_code=204)
def delete_allocation(allocation_id: int, db: Annotated[Session, Depends(get_db)]) -> None:
    allowance_service.delete_allocation(db, allocation_id)
