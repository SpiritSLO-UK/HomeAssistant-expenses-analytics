"""Schemas for the transactions API (spec §24.4)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.tags import TagOut


class SplitOut(BaseModel):
    """One persisted split part (spec §12.7)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    amount: Decimal
    category_id: int | None
    project_id: int | None
    description: str | None
    notes: str | None


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
    archived_at: datetime | None
    created_at: datetime
    tags: list[TagOut] = []


class TransactionDetailOut(TransactionOut):
    """Single-transaction view, including its splits (spec §17). Kept separate
    from the list schema so listing doesn't lazy-load splits for every row."""

    splits: list[SplitOut] = []


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


# --- Splits (spec §17, §24.4 POST /transactions/{id}/split) ---


class SplitIn(BaseModel):
    """One proposed split part. Amount is in the transaction's own currency."""

    amount: Decimal
    category_id: int | None = None
    project_id: int | None = None
    description: str | None = Field(default=None, max_length=300)
    notes: str | None = None


class SetSplitsRequest(BaseModel):
    splits: list[SplitIn]


class SplitsResponse(BaseModel):
    transaction_id: int
    is_split: bool
    currency: str
    total: Decimal
    splits: list[SplitOut]
