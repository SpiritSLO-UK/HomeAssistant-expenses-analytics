"""At-rest encryption / lock API (backlog #15b).

``/status`` and ``/unlock`` must work while the database is locked, so they never
depend on a DB session. ``/enable`` and ``/disable`` change the encryption state and
are **owner-only** (they only run against an accessible DB — encrypting a plaintext
DB or decrypting an unlocked one — so the owner check has a DB to read; backlog
follow-up to #214: these previously had no auth at all).
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


@router.post("/unlock", responses={400: {"description": "Bad request"}})
def unlock(payload: Passphrase) -> dict:
    if not security_service.unlock(payload.passphrase):
        failed = security_service.record_failed_unlock()
        raise HTTPException(
            status_code=400,
            detail=f"Wrong passphrase. ({failed} failed attempt(s) in the last hour.)",
        )
    security_service.record_successful_unlock()
    return {"status": "unlocked"}


@router.post("/enable", responses={400: {"description": "Bad request"}})
def enable(
    payload: EnableRequest,
    _owner: Annotated[User, Depends(require_owner)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    # Release the session the owner check opened before swapping/reconfiguring the DB,
    # else the encryption step operates on a connection this request still holds
    # ("Cannot operate on a closed database" — same fix as the restore routes, CR-SEC-1).
    db.close()
    try:
        security_service.enable_encryption(payload.passphrase, payload.unlock_mode)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "encrypted", "unlock_mode": payload.unlock_mode}


@router.post("/disable", responses={400: {"description": "Bad request"}})
def disable(
    payload: Passphrase,
    _owner: Annotated[User, Depends(require_owner)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    db.close()  # release this request's session before the DB swap (see enable)
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
