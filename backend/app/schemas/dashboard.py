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


# --- Trends & outliers (backlog #146, #150) ---


class MonthlyPoint(BaseModel):
    month: str  # YYYY-MM
    spend: str
    income: str
    net: str


class TrendMetric(BaseModel):
    current: str
    previous: str
    delta: str
    pct: float | None  # vs previous month; None when previous was zero
    direction: str  # up | down | flat


class MonthlySeries(BaseModel):
    currency: str
    months: list[MonthlyPoint]  # oldest → newest
    trend: dict[str, TrendMetric]  # keys: spend, income, net (empty if <2 months)


class OutlierItem(BaseModel):
    type: str  # large_charge | category_spike | new_merchant | budget
    severity: str  # warn | info
    title: str
    detail: str
    amount: str | None = None
    transaction_id: int | None = None
    category_id: int | None = None
    budget_id: int | None = None


class OutliersResponse(BaseModel):
    month: str
    currency: str
    items: list[OutlierItem]
