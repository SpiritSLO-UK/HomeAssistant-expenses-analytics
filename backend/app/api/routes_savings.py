"""Savings API: accounts, balance snapshots, and goals (spec §12.4; #96, #91)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import SavingsGoal
from app.schemas.savings import (
    BalanceAdjust,
    BalanceCreate,
    BalanceOut,
    GoalCreate,
    GoalOut,
    GoalUpdate,
    SavingsAccountCreate,
    SavingsAccountOut,
    SavingsAccountUpdate,
    SavingsSummary,
)
from app.services import auth_service, savings_service

router = APIRouter(prefix="/savings", tags=["savings"])


def _require_visible(request: Request, db: Session, account_id: int) -> None:
    """404 if the caller may not see this account (avoids leaking existence)."""
    scope = auth_service.visible_account_scope(request, db)
    if scope is not None and account_id not in scope:
        raise HTTPException(status_code=404, detail="Not a savings account")


@router.get("/summary", response_model=SavingsSummary)
def summary(request: Request, db: Annotated[Session, Depends(get_db)]) -> dict:
    return savings_service.summary(db, account_ids=auth_service.visible_account_scope(request, db))


# --- Accounts ---


@router.get("/accounts", response_model=list[SavingsAccountOut])
def list_accounts(request: Request, db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    scope = auth_service.visible_account_scope(request, db)
    return [savings_service.account_to_dict(db, a) for a in savings_service.list_accounts(db, account_ids=scope)]


@router.post("/accounts", response_model=SavingsAccountOut, status_code=201)
def create_account(payload: SavingsAccountCreate, db: Annotated[Session, Depends(get_db)]) -> dict:
    account = savings_service.create_account(
        db, name=payload.name, institution=payload.institution, currency=payload.currency
    )
    return savings_service.account_to_dict(db, account)


@router.patch("/accounts/{account_id}", response_model=SavingsAccountOut, responses={404: {"description": "Not found"}})
def update_account(
    account_id: int, payload: SavingsAccountUpdate, request: Request, db: Annotated[Session, Depends(get_db)]
) -> dict:
    """Edit a savings account (currently the interest rate)."""
    _require_visible(request, db, account_id)
    fields = payload.model_dump(exclude_unset=True)
    try:
        if "interest_rate" in fields:
            savings_service.set_interest_rate(db, account_id, fields["interest_rate"])
        account = savings_service.get_savings_account(db, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return savings_service.account_to_dict(db, account)


@router.post(
    "/accounts/{account_id}/adjust",
    response_model=BalanceOut,
    status_code=201,
    responses={400: {"description": "Bad request"}, 404: {"description": "Not found"}},
)
def adjust_balance(account_id: int, payload: BalanceAdjust, request: Request, db: Annotated[Session, Depends(get_db)]):
    """Deposit or withdraw via the +/- control — records a new snapshot at
    latest ± amount."""
    _require_visible(request, db, account_id)
    if payload.direction not in ("deposit", "withdraw"):
        raise HTTPException(status_code=400, detail="direction must be 'deposit' or 'withdraw'")
    delta = payload.amount if payload.direction == "deposit" else -payload.amount
    try:
        return savings_service.adjust_balance(db, account_id, delta=delta, note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/accounts/{account_id}/balances", response_model=list[BalanceOut], responses={404: {"description": "Not found"}}
)
def balance_history(account_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    _require_visible(request, db, account_id)
    try:
        savings_service.get_savings_account(db, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return savings_service.balance_history(db, account_id)


@router.post(
    "/accounts/{account_id}/balances",
    response_model=BalanceOut,
    status_code=201,
    responses={404: {"description": "Not found"}},
)
def record_balance(account_id: int, payload: BalanceCreate, request: Request, db: Annotated[Session, Depends(get_db)]):
    _require_visible(request, db, account_id)
    try:
        return savings_service.record_balance(
            db, account_id, as_of=payload.as_of_date, balance=payload.balance, note=payload.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- Goals ---


@router.get("/goals", response_model=list[GoalOut])
def list_goals(db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    return [savings_service.goal_to_dict(db, g) for g in savings_service.list_goals(db)]


@router.post("/goals", response_model=GoalOut, status_code=201, responses={400: {"description": "Bad request"}})
def create_goal(payload: GoalCreate, db: Annotated[Session, Depends(get_db)]) -> dict:
    try:
        goal = savings_service.create_goal(
            db,
            name=payload.name,
            target_amount=payload.target_amount,
            target_date=payload.target_date,
            account_id=payload.account_id,
            current_amount=payload.current_amount,
            currency=payload.currency,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return savings_service.goal_to_dict(db, goal)


def _get_goal(db: Session, goal_id: int) -> SavingsGoal:
    goal = db.get(SavingsGoal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    return goal


@router.patch("/goals/{goal_id}", response_model=GoalOut, responses={400: {"description": "Bad request"}})
def update_goal(goal_id: int, payload: GoalUpdate, db: Annotated[Session, Depends(get_db)]) -> dict:
    goal = _get_goal(db, goal_id)
    try:
        goal = savings_service.update_goal(db, goal, **payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return savings_service.goal_to_dict(db, goal)


@router.delete("/goals/{goal_id}", status_code=204)
def delete_goal(goal_id: int, db: Annotated[Session, Depends(get_db)]) -> None:
    savings_service.delete_goal(db, _get_goal(db, goal_id))
