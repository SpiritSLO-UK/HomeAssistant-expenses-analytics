"""Transaction and TransactionSplit models (spec §12.6, §12.7).

The bank transaction is the source of truth. When a transaction is split,
dashboard calculations use the split rows instead of the transaction's own
category/project (spec §37).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, Float, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.tag import transaction_tags


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    statement_id: Mapped[int | None] = mapped_column(
        ForeignKey("statements.id", ondelete="SET NULL"), nullable=True
    )
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    transaction_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    posted_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    description_raw: Mapped[str] = mapped_column(Text, nullable=False)
    merchant_raw: Mapped[str | None] = mapped_column(String(300), nullable=True)
    merchant_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    # debit | credit
    direction: Mapped[str] = mapped_column(String(8), nullable=False)

    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )

    is_split: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_transfer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_income: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # sha256(account|date|amount|currency|description|posted_date) — spec §14.5
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Multi-currency (backlog #29): the transaction's amount converted to the
    # household base currency. base_amount is NULL + needs_rate=True when a
    # foreign-currency transaction has no FX rate yet. Same-currency rows get
    # base_amount = amount and fx_rate = 1. Existing converted values are never
    # rewritten; only missing ones are backfilled.
    base_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    fx_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    needs_rate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    splits: Mapped[list[TransactionSplit]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    tags: Mapped[list[Tag]] = relationship(secondary=transaction_tags)


class TransactionSplit(Base, TimestampMixin):
    __tablename__ = "transaction_splits"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    transaction: Mapped[Transaction] = relationship(back_populates="splits")


# Imported here so ``relationship("Tag", ...)`` resolves when models load.
from app.models.tag import Tag  # noqa: E402
