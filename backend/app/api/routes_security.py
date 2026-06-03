"""At-rest encryption / lock API (backlog #15b).

These routes must work while the database is locked, so they never depend on a
DB session.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.services import security_health_service, security_service
from app.services.auth_service import require_owner

router = APIRouter(prefix="/security", tags=["security"])


class Passphrase(BaseModel):
    passphrase: str


class EnableRequest(BaseModel):
    passphrase: str
    unlock_mode: str = "prompt"  # prompt | stored


class DismissRequest(BaseModel):
    check_id: str
    snooze_days: int | None = None  # None/0 → dismiss forever; N → snooze N days
    clear: bool = False  # un-dismiss / un-snooze


@router.get("/status")
def status() -> dict:
    return security_service.status()


@router.post("/unlock")
def unlock(payload: Passphrase) -> dict:
    if not security_service.unlock(payload.passphrase):
        failed = security_service.record_failed_unlock()
        raise HTTPException(
            status_code=400,
            detail=f"Wrong passphrase. ({failed} failed attempt(s) in the last hour.)",
        )
    security_service.record_successful_unlock()
    return {"status": "unlocked"}


@router.post("/enable")
def enable(payload: EnableRequest) -> dict:
    try:
        security_service.enable_encryption(payload.passphrase, payload.unlock_mode)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "encrypted", "unlock_mode": payload.unlock_mode}


@router.post("/disable")
def disable(payload: Passphrase) -> dict:
    try:
        security_service.disable_encryption(payload.passphrase)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "decrypted"}


@router.get("/health")
def health(db: Annotated[Session, Depends(get_db)], owner: Annotated[User, Depends(require_owner)]) -> dict:
    """Owner-only security-health summary: which protections are on/off (#128)."""
    return security_health_service.evaluate(db, owner)


@router.post("/health/dismiss")
def dismiss_health(
    payload: DismissRequest, db: Annotated[Session, Depends(get_db)], _owner: Annotated[User, Depends(require_owner)]
) -> dict:
    dismissed, snoozed_until = security_health_service.dismiss(
        db, payload.check_id, snooze_days=payload.snooze_days, clear=payload.clear
    )
    return {"check_id": payload.check_id, "dismissed": dismissed, "snoozed_until": snoozed_until}
