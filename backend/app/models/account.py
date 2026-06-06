"""Account model (spec §12.4)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Account(Base, TimestampMixin):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    institution: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # current_account | debit_card | credit_card | savings | loan | mortgage | cash | other
    account_type: Mapped[str] = mapped_column(String(32), nullable=False, default="current_account")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    last_four: Mapped[str | None] = mapped_column(String(4), nullable=True)
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Optional annual interest rate (percent) — used by savings pots for a
    # projected-growth estimate. NULL = unknown / not interest-bearing.
    interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
