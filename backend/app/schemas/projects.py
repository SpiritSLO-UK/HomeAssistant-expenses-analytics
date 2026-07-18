"""Schemas for the projects API (spec §24.8, §18, §12.12)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

STATUSES = {"planned", "active", "paused", "complete", "archived"}


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    status: str = "active"  # planned | active | paused | complete | archived
    budget_amount: Decimal | None = Field(default=None, ge=0)
    start_date: date | None = None
    end_date: date | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: str | None = None
    budget_amount: Decimal | None = Field(default=None, ge=0)
    start_date: date | None = None
    end_date: date | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    status: str
    budget_amount: Decimal | None
    start_date: date | None
    end_date: date | None
    created_at: datetime


class BreakdownItem(BaseModel):
    id: int | None
    name: str
    total: Decimal


class ProjectTotal(BaseModel):
    project_id: int
    name: str
    status: str
    currency: str
    spent: Decimal
    budget: Decimal | None
    remaining: Decimal | None
    percent: float | None


class ProjectForecast(BaseModel):
    """Run-rate / burn-down forecast vs budget (spec §18.2). Present only when the
    project has a positive budget; money fields are ``Decimal`` like their siblings."""

    budget: Decimal
    remaining: Decimal
    run_rate_per_day: Decimal | None
    forecast_total: Decimal | None
    on_track: bool
    exhaustion_date: date | None


class ProjectSummary(ProjectTotal):
    transaction_count: int
    first_transaction: date | None
    last_transaction: date | None
    forecast: ProjectForecast | None = None
    by_category: list[BreakdownItem]
    by_vendor: list[BreakdownItem]
