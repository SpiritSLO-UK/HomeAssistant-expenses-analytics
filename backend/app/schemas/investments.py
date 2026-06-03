"""Schemas for the investments & pensions API (spec §12.4, §27)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# Account types this feature owns (a subset of accounts.ACCOUNT_TYPES).
INVESTMENT_ACCOUNT_TYPES = {"investment", "pension"}


class InvestmentAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    institution: str | None = None
    currency: str | None = None
    account_type: str = "investment"  # investment | pension


class InvestmentAccountOut(BaseModel):
    id: int
    name: str
    institution: str | None
    currency: str
    account_type: str
    current_value: str | None  # holdings market value, else latest snapshot
    cost_basis: str | None  # Σ units × avg_cost (holdings with a cost basis)
    gain: str | None  # current_value − cost_basis
    gain_pct: float | None
    has_holdings: bool
    holdings_count: int
    value_count: int


class HoldingCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    name: str | None = Field(default=None, max_length=200)
    units: Decimal = Field(gt=0)
    avg_cost: Decimal | None = Field(default=None, ge=0)
    last_price: Decimal | None = Field(default=None, ge=0)


class HoldingUpdate(BaseModel):
    symbol: str | None = Field(default=None, min_length=1, max_length=20)
    name: str | None = None
    units: Decimal | None = Field(default=None, gt=0)
    avg_cost: Decimal | None = Field(default=None, ge=0)
    last_price: Decimal | None = Field(default=None, ge=0)


class HoldingOut(BaseModel):
    id: int
    account_id: int
    symbol: str
    name: str | None
    units: str
    avg_cost: str | None
    last_price: str | None
    last_price_at: str | None
    currency: str
    market_value: str | None
    cost_basis: str | None
    gain: str | None
    gain_pct: float | None


class ValueCreate(BaseModel):
    as_of_date: date
    value: Decimal = Field(ge=0)
    note: str | None = None


class ValueAdjust(BaseModel):
    """A contribution/withdrawal delta applied to the latest value (the +/- control)."""

    amount: Decimal = Field(gt=0)
    direction: str = "contribution"  # contribution | withdrawal
    note: str | None = None


class ValueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    as_of_date: date
    value: Decimal
    currency: str
    note: str | None


class InvestmentSummary(BaseModel):
    currency: str
    total_value: str
    total_cost: str | None
    total_gain: str | None
    total_gain_pct: float | None
    by_type: dict[str, str]
    accounts: list[InvestmentAccountOut]


class PeriodChange(BaseModel):
    change: str
    pct: float | None


class HistoryPoint(BaseModel):
    date: str
    value: str


class InvestmentHistory(BaseModel):
    currency: str
    total_value: str
    points: list[HistoryPoint]
    change_day: PeriodChange
    change_month: PeriodChange
    change_year: PeriodChange
