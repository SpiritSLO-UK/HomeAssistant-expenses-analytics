"""MFA / auth API (backlog #124).

Optional app-level two-factor (TOTP) layered on top of Home Assistant auth.
Every endpoint acts on the *current* user; the audit log records each change.
These routes are reachable while the MFA entry gate is unsatisfied (so a user can
verify), but still require an approved account.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.schemas.auth import CodeIn, SetupOut, VerifyOut
from app.services import audit_service, auth_service, mfa_service
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/auth/mfa", tags=["auth"])


@router.post("/setup")
def setup(db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]) -> SetupOut:
    data = mfa_service.start_enrolment(db, user)
    return SetupOut(**data)


@router.post("/enable")
def enable(
    payload: CodeIn, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]
) -> dict:
    if not mfa_service.enable(db, user, payload.code):
        raise HTTPException(status_code=400, detail="That code didn't match. Try again.")
    audit_service.record(db, actor=user.display_name, action="mfa_enabled", entity_type="user", entity_id=user.id)
    db.commit()
    return {"status": "enabled"}


@router.post("/disable")
def disable(
    payload: CodeIn, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]
) -> dict:
    if not mfa_service.disable(db, user, payload.code):
        raise HTTPException(status_code=400, detail="MFA is not enabled or the code didn't match.")
    audit_service.record(db, actor=user.display_name, action="mfa_disabled", entity_type="user", entity_id=user.id)
    db.commit()
    return {"status": "disabled"}


@router.post("/verify")
def verify(
    payload: CodeIn, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]
) -> VerifyOut:
    token = mfa_service.verify_and_open(db, user, payload.code)
    if token is None:
        raise HTTPException(status_code=400, detail="That code didn't match. Try again.")
    return VerifyOut(token=token, expires_in_seconds=int(mfa_service.SESSION_TTL.total_seconds()))


@router.post("/step-up")
def step_up(
    payload: CodeIn,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    token = request.headers.get(auth_service.SESSION_HEADER)
    if not mfa_service.step_up(db, user, token, payload.code):
        raise HTTPException(status_code=400, detail="That code didn't match. Try again.")
    return {"status": "verified"}
