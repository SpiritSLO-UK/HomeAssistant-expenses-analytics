"""Schemas for the budgets API (spec §24.9, §19)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class BudgetIn(BaseModel):
    """Create a budget. Leave both category_id and project_id unset for a
    whole-household "total" budget (spec §19.1)."""

    name: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0)
    period: str = "monthly"  # weekly | monthly | quarterly | yearly | custom
    category_id: int | None = None
    project_id: int | None = None
    currency: str | None = None  # defaults to the household base currency
    start_date: date | None = None
    end_date: date | None = None
    rollover_enabled: bool = False
    alert_threshold_percent: int | None = Field(default=80, ge=0, le=100)


class BudgetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    amount: Decimal | None = Field(default=None, gt=0)
    period: str | None = None
    category_id: int | None = None
    project_id: int | None = None
    currency: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    rollover_enabled: bool | None = None
    alert_threshold_percent: int | None = Field(default=None, ge=0, le=100)


class BudgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    amount: Decimal
    currency: str
    period: str
    category_id: int | None
    project_id: int | None
    start_date: date | None
    end_date: date | None
    rollover_enabled: bool
    alert_threshold_percent: int | None
    created_at: datetime


class BudgetSummaryItem(BaseModel):
    budget_id: int
    name: str
    category_id: int | None
    project_id: int | None
    period: str
    currency: str
    amount: Decimal
    spent: Decimal
    remaining: Decimal
    percent: float
    status: str  # ok | warn | over
    alert_threshold_percent: int | None
    period_start: date
    period_end: date
