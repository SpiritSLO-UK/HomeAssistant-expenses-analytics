"""Accounts management API — shared vs private (backlog #66/#82).

Lists the accounts the caller may see and lets them set visibility. Assigning or
changing an account's **owner** is owner/admin-only; a member may toggle
``is_shared`` only on an account they already own. A non-admin never sees (and
can't PATCH → 404) an account that's private to someone else.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Account, User
from app.schemas.accounts import ACCOUNT_TYPES, AccountOut, AccountUpdate
from app.services import auth_service
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _to_dict(account: Account, owner_names: dict[int, str]) -> dict:
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
    }


@router.get("", response_model=list[AccountOut])
def list_accounts(request: Request, db: Session = Depends(get_db)) -> list[dict]:
    scope = auth_service.visible_account_scope(request, db)
    stmt = select(Account)
    if scope is not None:
        stmt = stmt.where(Account.id.in_(scope))
    accounts = list(db.scalars(stmt.order_by(Account.name)).all())
    owner_names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    return [_to_dict(a, owner_names) for a in accounts]


@router.patch("/{account_id}", response_model=AccountOut)
def update_account(
    account_id: int,
    payload: AccountUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
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
    owner_names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    return _to_dict(account, owner_names)
