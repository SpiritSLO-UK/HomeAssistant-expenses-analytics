"""Schemas for the savings API (spec §12.4; backlog #96, #91)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class SavingsAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    institution: str | None = None
    currency: str | None = None


class SavingsAccountOut(BaseModel):
    id: int
    name: str
    institution: str | None
    currency: str
    latest_balance: str | None
    balance_count: int


class BalanceCreate(BaseModel):
    as_of_date: date
    balance: Decimal
    note: str | None = None


class BalanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    as_of_date: date
    balance: Decimal
    currency: str
    note: str | None


class GoalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target_amount: Decimal = Field(gt=0)
    target_date: date | None = None
    account_id: int | None = None
    current_amount: Decimal | None = None
    currency: str | None = None


class GoalUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    target_amount: Decimal | None = Field(default=None, gt=0)
    target_date: date | None = None
    account_id: int | None = None
    current_amount: Decimal | None = None
    status: str | None = None


class GoalOut(BaseModel):
    id: int
    name: str
    target_amount: str
    target_date: date | None
    account_id: int | None
    current_amount: str
    current: str
    remaining: str
    percent: float
    currency: str
    status: str


class SavingsSummary(BaseModel):
    currency: str
    total_savings: str
    accounts: list[SavingsAccountOut]
    goals: list[GoalOut]
