"""Savings models (spec §12.4; backlog #96, #91).

Savings are tracked two ways:

- **Balance snapshots** (``SavingsBalance``) — the user records "this savings
  account held £X on date Y". A series of snapshots gives a balance history to
  chart growth. The savings account itself is a regular :class:`Account` with
  ``account_type == "savings"``.
- **Goals** (``SavingsGoal``) — a target amount (optionally by a date), either
  **linked** to a savings account (progress = that account's latest balance) or
  tracked **manually** (``current_amount``).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class SavingsBalance(Base):
    __tablename__ = "savings_balances"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class SavingsGoal(Base, TimestampMixin):
    __tablename__ = "savings_goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # When set, progress tracks this savings account's latest balance; otherwise
    # ``current_amount`` is updated manually.
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    current_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    # active | achieved | archived
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
