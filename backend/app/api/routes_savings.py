"""Savings API: accounts, balance snapshots, and goals (spec §12.4; #96, #91)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import SavingsGoal
from app.schemas.savings import (
    BalanceCreate,
    BalanceOut,
    GoalCreate,
    GoalOut,
    GoalUpdate,
    SavingsAccountCreate,
    SavingsAccountOut,
    SavingsSummary,
)
from app.services import savings_service

router = APIRouter(prefix="/savings", tags=["savings"])


@router.get("/summary", response_model=SavingsSummary)
def summary(db: Session = Depends(get_db)) -> dict:
    return savings_service.summary(db)


# --- Accounts ---


@router.get("/accounts", response_model=list[SavingsAccountOut])
def list_accounts(db: Session = Depends(get_db)) -> list[dict]:
    return [savings_service.account_to_dict(db, a) for a in savings_service.list_accounts(db)]


@router.post("/accounts", response_model=SavingsAccountOut, status_code=201)
def create_account(payload: SavingsAccountCreate, db: Session = Depends(get_db)) -> dict:
    account = savings_service.create_account(
        db, name=payload.name, institution=payload.institution, currency=payload.currency
    )
    return savings_service.account_to_dict(db, account)


@router.get("/accounts/{account_id}/balances", response_model=list[BalanceOut])
def balance_history(account_id: int, db: Session = Depends(get_db)):
    try:
        savings_service.get_savings_account(db, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return savings_service.balance_history(db, account_id)


@router.post("/accounts/{account_id}/balances", response_model=BalanceOut, status_code=201)
def record_balance(account_id: int, payload: BalanceCreate, db: Session = Depends(get_db)):
    try:
        return savings_service.record_balance(
            db, account_id, as_of=payload.as_of_date, balance=payload.balance, note=payload.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- Goals ---


@router.get("/goals", response_model=list[GoalOut])
def list_goals(db: Session = Depends(get_db)) -> list[dict]:
    return [savings_service.goal_to_dict(db, g) for g in savings_service.list_goals(db)]


@router.post("/goals", response_model=GoalOut, status_code=201)
def create_goal(payload: GoalCreate, db: Session = Depends(get_db)) -> dict:
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


@router.patch("/goals/{goal_id}", response_model=GoalOut)
def update_goal(goal_id: int, payload: GoalUpdate, db: Session = Depends(get_db)) -> dict:
    goal = _get_goal(db, goal_id)
    try:
        goal = savings_service.update_goal(db, goal, **payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return savings_service.goal_to_dict(db, goal)


@router.delete("/goals/{goal_id}", status_code=204)
def delete_goal(goal_id: int, db: Session = Depends(get_db)) -> None:
    savings_service.delete_goal(db, _get_goal(db, goal_id))
