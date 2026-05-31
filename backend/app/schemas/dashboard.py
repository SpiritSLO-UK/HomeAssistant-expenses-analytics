"""Schemas for the dashboard API (spec §24.12)."""

from __future__ import annotations

from pydantic import BaseModel


class DashboardSummary(BaseModel):
    month: str
    currency: str
    spend_this_month: str
    income_this_month: str
    net_this_month: str
    total_transactions: int
    uncategorised_transactions: int
    review_items: int
    needs_rate: int = 0


class CategoryBreakdownItem(BaseModel):
    category_id: int | None
    name: str
    colour: str | None
    total: str
    count: int


class VendorBreakdownItem(BaseModel):
    vendor_id: int | None
    name: str
    total: str
    count: int
