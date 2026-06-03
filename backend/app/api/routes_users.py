"""Users & access-control API (spec §6, §8.2, §28; backlog #82, #126, #74).

Identity is supplied by Home Assistant ingress, so there is no "create user"
form — users appear on first request (see ``auth_service``) and the owner manages
them here. All mutating endpoints require the owner (administrator) role.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.schemas.users import MemberOut, MeOut, UserOut, UserUpdate
from app.services import auth_service, mfa_service
from app.services.auth_service import get_current_user, require_owner, require_owner_step_up

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=MeOut)
def get_me(
    request: Request, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]
) -> MeOut:
    mfa_required = user.mfa_enabled and not mfa_service.has_valid_session(
        db, user.id, request.headers.get(auth_service.SESSION_HEADER)
    )
    return MeOut(
        id=user.id,
        display_name=user.display_name,
        role=user.role,
        status=user.status,
        is_admin=auth_service.is_admin(user.role),
        can_write=user.status == "approved" and auth_service.can_write(user.role),
        can_manage_settings=auth_service.can_manage_settings(user),
        mfa_enabled=user.mfa_enabled,
        mfa_required=mfa_required,
    )


@router.get("/members", response_model=list[MemberOut])
def list_members(
    db: Annotated[Session, Depends(get_db)], _user: Annotated[User, Depends(get_current_user)]
) -> list[User]:
    """Approved members for the per-member spend filter (Dashboard + Transactions).
    Any approved user may read this — the spend it maps to is still scoped to the
    caller's own visibility."""
    return auth_service.list_members(db)


@router.get("", response_model=list[UserOut])
def list_users(db: Annotated[Session, Depends(get_db)], _owner: Annotated[User, Depends(require_owner)]) -> list[User]:
    return auth_service.list_users(db)


def _get_target(db: Session, user_id: int) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    return target


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Annotated[Session, Depends(get_db)],
    owner: Annotated[User, Depends(require_owner_step_up)],
) -> User:
    target = _get_target(db, user_id)
    try:
        return auth_service.update_user(
            db,
            actor=owner,
            target=target,
            role=payload.role,
            new_status=payload.status,
            display_name=payload.display_name,
            email=payload.email,
            can_manage_settings=payload.can_manage_settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{user_id}/approve", response_model=UserOut)
def approve_user(
    user_id: int, db: Annotated[Session, Depends(get_db)], owner: Annotated[User, Depends(require_owner_step_up)]
) -> User:
    target = _get_target(db, user_id)
    try:
        return auth_service.update_user(db, actor=owner, target=target, new_status="approved")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{user_id}")
def delete_user(
    user_id: int, db: Annotated[Session, Depends(get_db)], owner: Annotated[User, Depends(require_owner_step_up)]
) -> dict:
    target = _get_target(db, user_id)
    try:
        auth_service.delete_user(db, actor=owner, target=target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted", "id": user_id}
