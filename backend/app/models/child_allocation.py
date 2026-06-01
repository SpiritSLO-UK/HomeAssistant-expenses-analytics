"""ChildAllocation model — the kid's-allowance overlay (backlog #82; spec §6, §19).

A row attributes spend to a *child* user so it shows on the child's allowance
view, **without** changing the parent's books: normal aggregation (dashboards,
household budgets, analytics) never reads this table, so the originating
transaction stays fully on the parent's expenses. Three shapes:

- **whole**  — ``transaction_id`` set, ``amount`` = the transaction's spend.
- **split**  — ``transaction_id`` + ``transaction_split_id`` set, ``amount`` = the split line.
- **manual** — all transaction refs NULL; the parent types the item in.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ChildAllocation(Base, TimestampMixin):
    __tablename__ = "child_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    # The child this spend is shown to.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The parent's transaction this is drawn from (NULL for a manual entry).
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    transaction_split_id: Mapped[int | None] = mapped_column(
        ForeignKey("transaction_splits.id", ondelete="SET NULL"), nullable=True
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)  # positive money-out
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
