"""Schemas for the child allowance API (backlog #82; spec §6, §19)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.savings import GoalOut, SavingsAccountOut


class AllocationCreate(BaseModel):
    child_id: int
    transaction_id: int | None = None  # whole/split: the parent's transaction
    split_id: int | None = None  # split: the specific split line
    category_id: int | None = None  # the child's category (candy/toys…)
    amount: Decimal | None = Field(default=None, gt=0)  # required for manual; overrides otherwise
    description: str | None = None
    as_of: date | None = None


class AllocationOut(BaseModel):
    id: int
    as_of_date: str
    description: str | None
    category_id: int | None
    category_name: str | None
    amount: str
    currency: str
    transaction_id: int | None


class ChildBudgetStatus(BaseModel):
    budget_id: int
    name: str
    category_id: int | None
    period: str
    currency: str
    amount: str
    spent: str
    remaining: str
    percent: float
    status: str  # ok | warn | over
    period_start: str
    period_end: str


class AllowanceSavings(BaseModel):
    total_savings: str
    accounts: list[SavingsAccountOut]
    goals: list[GoalOut]


class AllowanceSummary(BaseModel):
    user_id: int
    display_name: str
    currency: str
    budgets: list[ChildBudgetStatus]
    savings: AllowanceSavings
    items: list[AllocationOut]
