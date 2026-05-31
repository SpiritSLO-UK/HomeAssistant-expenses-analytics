"""Schemas for the subscriptions API (spec §20, §24)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

FREQUENCIES = {"weekly", "monthly", "quarterly", "yearly"}
STATUSES = {"active", "possible", "cancelled", "ignored"}


class SubscriptionOut(BaseModel):
    id: int
    vendor_id: int | None
    category_id: int | None
    name: str
    amount: Decimal
    currency: str
    frequency: str
    monthly_amount: Decimal
    interval_days: int
    next_expected_date: date | None
    last_seen_date: date | None
    confidence_score: float | None
    occurrences: int
    status: str


class SubscriptionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    amount: Decimal | None = Field(default=None, gt=0)
    frequency: str | None = None
    category_id: int | None = None
    status: str | None = None
    next_expected_date: date | None = None


class DetectResult(BaseModel):
    created: int
    updated: int
    total: int


class DashboardSubscriptions(BaseModel):
    currency: str
    monthly_total: Decimal
    count: int
    subscriptions: list[SubscriptionOut]
