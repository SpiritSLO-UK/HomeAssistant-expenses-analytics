"""MFA / auth API (backlog #124).

Optional app-level two-factor (TOTP) layered on top of Home Assistant auth.
Every endpoint acts on the *current* user; the audit log records each change.
These routes are reachable while the MFA entry gate is unsatisfied (so a user can
verify), but still require an approved account.
"""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.schemas.auth import CodeIn, EnableIn, SetupIn, SetupOut, VerifyOut
from app.services import audit_service, auth_service, mfa_service
from app.services.auth_service import get_current_user

# 429 is raised by the shared lockout guard below, so document it router-wide.
router = APIRouter(
    prefix="/auth/mfa",
    tags=["auth"],
    responses={429: {"description": "Too many incorrect codes — temporarily locked out"}},
)

_BAD_CODE = "That code didn't match. Try again."


def _ensure_not_locked(user: User) -> None:
    """Refuse an MFA code check while the user is locked out (CR-SEC-6)."""
    secs = mfa_service.mfa_lockout_seconds(user.id)
    if secs > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Too many incorrect codes — try again in about {max(1, secs // 60)} minute(s).",
            headers={"Retry-After": str(secs)},
        )


def _bad_code(db: Session, user: User, detail: str) -> NoReturn:
    """Record a failed MFA attempt (throttle + audit) and raise 400 (CR-SEC-6)."""
    count = mfa_service.record_mfa_failure(user.id)
    audit_service.record(
        db, actor=user.display_name, action="mfa_failed",
        entity_type="user", entity_id=user.id, details={"recent_failures": count},
    )
    db.commit()
    raise HTTPException(status_code=400, detail=detail)


@router.post("/setup", responses={400: {"description": "Bad request"}})
def setup(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    payload: SetupIn | None = None,
) -> SetupOut:
    # Re-enrolling an already-enabled user requires the current code (SR-1): without
    # it, start_enrolment refuses and we treat it as a failed code check (throttle +
    # audit), so an authenticated-but-unverified caller can't reset the factor. A
    # fresh enrolment (no live factor to protect) always succeeds and needs no code.
    reenrol = user.mfa_enabled
    if reenrol:
        _ensure_not_locked(user)
    data = mfa_service.start_enrolment(db, user, payload.code if payload else None)
    if data is None:
        _bad_code(db, user, "Enter your current authenticator code to change MFA.")
    if reenrol:
        mfa_service.clear_mfa_failures(user.id)
    return SetupOut(**data)


@router.post("/enable", responses={400: {"description": "Bad request"}})
def enable(
    payload: EnableIn, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]
) -> dict:
    _ensure_not_locked(user)
    if not mfa_service.enable(db, user, payload.code, payload.scope):
        _bad_code(db, user, _BAD_CODE)
    mfa_service.clear_mfa_failures(user.id)
    audit_service.record(db, actor=user.display_name, action="mfa_enabled", entity_type="user", entity_id=user.id)
    db.commit()
    return {"status": "enabled", "mfa_scope": user.mfa_scope}


@router.post("/disable", responses={400: {"description": "Bad request"}})
def disable(
    payload: CodeIn, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]
) -> dict:
    _ensure_not_locked(user)
    if not mfa_service.disable(db, user, payload.code):
        _bad_code(db, user, "MFA is not enabled or the code didn't match.")
    mfa_service.clear_mfa_failures(user.id)
    audit_service.record(db, actor=user.display_name, action="mfa_disabled", entity_type="user", entity_id=user.id)
    db.commit()
    return {"status": "disabled"}


@router.post("/verify", responses={400: {"description": "Bad request"}})
def verify(
    payload: CodeIn, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]
) -> VerifyOut:
    _ensure_not_locked(user)
    token = mfa_service.verify_and_open(db, user, payload.code)
    if token is None:
        _bad_code(db, user, _BAD_CODE)
    mfa_service.clear_mfa_failures(user.id)
    return VerifyOut(token=token, expires_in_seconds=int(mfa_service.SESSION_TTL.total_seconds()))


@router.post("/step-up", responses={400: {"description": "Bad request"}})
def step_up(
    payload: CodeIn,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    _ensure_not_locked(user)
    token = request.headers.get(auth_service.SESSION_HEADER)
    if not mfa_service.step_up(db, user, token, payload.code):
        _bad_code(db, user, _BAD_CODE)
    mfa_service.clear_mfa_failures(user.id)
    return {"status": "verified"}
