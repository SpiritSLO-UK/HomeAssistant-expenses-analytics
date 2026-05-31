"""Schemas for the transactions API (spec §24.4)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int | None
    statement_id: int | None
    transaction_date: date
    posted_date: date | None
    description_raw: str
    merchant_raw: str | None
    merchant_id: int | None
    amount: Decimal
    currency: str
    direction: str
    base_amount: Decimal | None
    fx_rate: Decimal | None
    needs_rate: bool
    category_id: int | None
    project_id: int | None
    is_split: bool
    is_transfer: bool
    is_income: bool
    is_duplicate: bool
    needs_review: bool
    review_reason: str | None
    confidence_score: float | None
    created_at: datetime


class TransactionListResponse(BaseModel):
    items: list[TransactionOut]
    total: int
    limit: int
    offset: int


class TransactionUpdate(BaseModel):
    """Partial update of a transaction (spec §24.4 PATCH)."""

    category_id: int | None = None
    project_id: int | None = None
    merchant_id: int | None = None
    is_transfer: bool | None = None
    is_income: bool | None = None
    needs_review: bool | None = None
    review_reason: str | None = None
