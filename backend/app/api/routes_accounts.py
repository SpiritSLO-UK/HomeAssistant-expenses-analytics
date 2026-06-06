"""Accounts management API — shared vs private (backlog #66/#82).

Lists the accounts the caller may see and lets them set visibility. Assigning or
changing an account's **owner** is owner/admin-only; a member may toggle
``is_shared`` only on an account they already own. A non-admin never sees (and
can't PATCH → 404) an account that's private to someone else.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Account, User
from app.schemas.accounts import (
    ACCOUNT_TYPES,
    AccountCreate,
    AccountMerge,
    AccountOut,
    AccountUpdate,
)
from app.services import account_service, auth_service
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _to_dict(account: Account, owner_names: dict[int, str], in_use: set[int] | None = None) -> dict:
    return {
        "id": account.id,
        "name": account.name,
        "institution": account.institution,
        "account_type": account.account_type,
        "currency": account.currency,
        "is_active": account.is_active,
        "owner_user_id": account.owner_user_id,
        "owner_name": owner_names.get(account.owner_user_id) if account.owner_user_id else None,
        "is_shared": account.is_shared,
        "is_private": account.owner_user_id is not None and not account.is_shared,
        "in_use": account.id in in_use if in_use is not None else False,
    }


def _owner_names(db: Session) -> dict[int, str]:
    return {u.id: u.display_name for u in db.scalars(select(User)).all()}


@router.get("", response_model=list[AccountOut])
def list_accounts(request: Request, db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    scope = auth_service.visible_account_scope(request, db)
    stmt = select(Account)
    if scope is not None:
        stmt = stmt.where(Account.id.in_(scope))
    accounts = list(db.scalars(stmt.order_by(Account.name)).all())
    owner_names = _owner_names(db)
    in_use = account_service.accounts_in_use(db)
    return [_to_dict(a, owner_names, in_use) for a in accounts]


@router.post("", response_model=AccountOut, responses={400: {"description": "Bad request"}})
def create_account(
    payload: AccountCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Create a manual account. A non-admin's account is owned by them (private by
    default); only an admin may create it for someone else or as shared/household."""
    if payload.account_type not in ACCOUNT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown account type. One of: {sorted(ACCOUNT_TYPES)}")
    is_admin = auth_service.is_admin(user.role)
    owner_user_id = payload.owner_user_id
    is_shared = payload.is_shared
    if not is_admin:
        owner_user_id = user.id  # a non-admin can only create an account they own
    elif owner_user_id is not None and db.get(User, owner_user_id) is None:
        raise HTTPException(status_code=400, detail="Unknown user")
    account = account_service.create_account(
        db,
        name=payload.name,
        account_type=payload.account_type,
        currency=payload.currency,
        institution=payload.institution,
        owner_user_id=owner_user_id,
        is_shared=is_shared,
    )
    return _to_dict(account, _owner_names(db), account_service.accounts_in_use(db))


@router.patch(
    "/{account_id}",
    response_model=AccountOut,
    responses={
        400: {"description": "Bad request"},
        403: {"description": "Forbidden"},
        404: {"description": "Not found"},
    },
)
def update_account(
    account_id: int,
    payload: AccountUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    account = db.get(Account, account_id)
    scope = auth_service.visible_account_scope(request, db)
    if account is None or (scope is not None and account_id not in scope):
        raise HTTPException(status_code=404, detail="Account not found")

    data = payload.model_dump(exclude_unset=True)
    is_admin = auth_service.is_admin(user.role)
    owns_it = account.owner_user_id == user.id

    if "owner_user_id" in data and data["owner_user_id"] != account.owner_user_id and not is_admin:
        raise HTTPException(status_code=403, detail="Only the household owner can change account ownership.")
    if not is_admin and not owns_it:
        raise HTTPException(status_code=403, detail="You can only change accounts you own.")
    if data.get("account_type") is not None and data["account_type"] not in ACCOUNT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown account type. One of: {sorted(ACCOUNT_TYPES)}")
    if data.get("owner_user_id") is not None and db.get(User, data["owner_user_id"]) is None:
        raise HTTPException(status_code=400, detail="Unknown user")

    for field, value in data.items():
        setattr(account, field, value)
    db.commit()
    db.refresh(account)
    return _to_dict(account, _owner_names(db), account_service.accounts_in_use(db))


@router.delete(
    "/{account_id}",
    responses={
        404: {"description": "Not found"},
        409: {"description": "Account still has data — merge it instead"},
    },
)
def delete_account(
    account_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(auth_service.require_owner)],
) -> dict:
    """Delete an **empty** account (owner-only — structural). An account that still
    has transactions/snapshots/etc. returns 409: merge it into another instead."""
    try:
        deleted = account_service.delete_account(db, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if deleted is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"deleted": True, "id": account_id}


@router.post(
    "/{account_id}/merge",
    response_model=AccountOut,
    responses={400: {"description": "Bad request"}, 404: {"description": "Not found"}},
)
def merge_account(
    account_id: int,
    payload: AccountMerge,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(auth_service.require_owner)],
) -> dict:
    """Fold one account's transactions/statements/snapshots into another then delete
    the source (owner-only — structural/destructive). Use this for an account that
    can't be deleted because it still has data."""
    try:
        target = account_service.merge_account(db, account_id, payload.target_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if target is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return _to_dict(target, _owner_names(db), account_service.accounts_in_use(db))
