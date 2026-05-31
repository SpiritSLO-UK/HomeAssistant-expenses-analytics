"""Subscription / recurring-payment model (spec §20, §12.x).

Detected by ``subscription_service`` from recurring transactions of the same
vendor (or, when no vendor is matched, the same normalised merchant name). The
``name`` doubles as the grouping key when ``vendor_id`` is null.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Float, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    vendor_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    # Display label and grouping key when there's no matched vendor.
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    # weekly | monthly | quarterly | yearly
    frequency: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    next_expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_seen_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # active | possible | cancelled | ignored
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
