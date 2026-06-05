"""Energy-cost offset API (HA): read production, net it against energy-bill spend.

Reads (offset/status) are available to approved users (account-scoped); config
edits are gated to settings managers (same RBAC as the rest of Settings).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.user import User
from app.schemas.energy import EnergyConfig, EnergyConfigUpdate
from app.services import auth_service, energy_service

router = APIRouter(prefix="/energy", tags=["energy"])


def _ref(month: str | None) -> date:
    """Resolve a ``YYYY-MM`` (or ``YYYY-MM-DD``) query param to a date; today if blank."""
    if not month:
        return date.today()
    try:
        return date.fromisoformat(month if len(month) == 10 else f"{month}-01")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="month must be YYYY-MM or YYYY-MM-DD") from exc


@router.get("/offset")
def offset(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    month: Annotated[str | None, Query()] = None,
) -> dict:
    return energy_service.offset(
        db, _ref(month), account_ids=auth_service.visible_account_scope(request, db)
    )


@router.get("/status")
def status(db: Annotated[Session, Depends(get_db)]) -> dict:
    return energy_service.status(db)


@router.get("/config", response_model=EnergyConfig)
def get_config(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(auth_service.require_settings_manager)],
) -> dict:
    return energy_service.get_config(db)


@router.put("/config", response_model=EnergyConfig, responses={400: {"description": "Invalid config"}})
def update_config(
    payload: EnergyConfigUpdate,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(auth_service.require_settings_manager)],
) -> dict:
    try:
        return energy_service.validate_and_save(db, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
