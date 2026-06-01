"""Budgets API routes (spec §24.9, §19)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Budget, Category, Project, User
from app.schemas.budgets import BudgetIn, BudgetOut, BudgetSummaryItem, BudgetUpdate
from app.services import budget_service, mqtt_service, settings_service
from app.services.budget_service import PERIODS
from app.services.household_service import get_or_create_default_household

router = APIRouter(prefix="/budgets", tags=["budgets"])


def _validate(
    db: Session,
    *,
    period: str | None,
    category_id: int | None,
    project_id: int | None,
    owner_user_id: int | None = None,
) -> None:
    if period is not None and period not in PERIODS:
        raise HTTPException(status_code=400, detail=f"Unknown period '{period}'. One of: {sorted(PERIODS)}")
    if category_id is not None and db.get(Category, category_id) is None:
        raise HTTPException(status_code=400, detail="Unknown category")
    if project_id is not None and db.get(Project, project_id) is None:
        raise HTTPException(status_code=400, detail="Unknown project")
    if owner_user_id is not None and db.get(User, owner_user_id) is None:
        raise HTTPException(status_code=400, detail="Unknown user")


@router.get("", response_model=list[BudgetOut])
def list_budgets(db: Session = Depends(get_db)) -> list[Budget]:
    return list(db.scalars(select(Budget).order_by(Budget.name)).all())


@router.get("/summary", response_model=list[BudgetSummaryItem])
def budgets_summary(
    db: Session = Depends(get_db), month: date | None = Query(default=None)
) -> list[dict]:
    """Spend/remaining/percent/status for every budget (spec §19.2)."""
    return budget_service.summary(db, month or date.today())


@router.post("", response_model=BudgetOut, status_code=201)
def create_budget(payload: BudgetIn, db: Session = Depends(get_db)) -> Budget:
    _validate(
        db,
        period=payload.period,
        category_id=payload.category_id,
        project_id=payload.project_id,
        owner_user_id=payload.owner_user_id,
    )
    household = get_or_create_default_household(db)
    budget = Budget(
        household_id=household.id,
        name=payload.name,
        amount=payload.amount,
        currency=(payload.currency or settings_service.get_base_currency(db)).upper(),
        period=payload.period,
        category_id=payload.category_id,
        project_id=payload.project_id,
        owner_user_id=payload.owner_user_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        rollover_enabled=payload.rollover_enabled,
        alert_threshold_percent=payload.alert_threshold_percent,
    )
    db.add(budget)
    db.commit()
    db.refresh(budget)
    mqtt_service.publish_safe(db)  # budget updated -> refresh sensors (spec §27.1)
    return budget


@router.patch("/{budget_id}", response_model=BudgetOut)
def update_budget(budget_id: int, payload: BudgetUpdate, db: Session = Depends(get_db)) -> Budget:
    budget = db.get(Budget, budget_id)
    if budget is None:
        raise HTTPException(status_code=404, detail="Budget not found")
    data = payload.model_dump(exclude_unset=True)
    _validate(
        db,
        period=data.get("period"),
        category_id=data.get("category_id"),
        project_id=data.get("project_id"),
        owner_user_id=data.get("owner_user_id"),
    )
    if "currency" in data and data["currency"]:
        data["currency"] = data["currency"].upper()
    for field, value in data.items():
        setattr(budget, field, value)
    db.commit()
    db.refresh(budget)
    mqtt_service.publish_safe(db)
    return budget


@router.delete("/{budget_id}", status_code=204)
def delete_budget(budget_id: int, db: Session = Depends(get_db)) -> None:
    budget = db.get(Budget, budget_id)
    if budget is None:
        raise HTTPException(status_code=404, detail="Budget not found")
    db.delete(budget)
    db.commit()
    mqtt_service.publish_safe(db)
