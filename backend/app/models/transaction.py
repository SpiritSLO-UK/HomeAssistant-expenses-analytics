"""Transaction and TransactionSplit models (spec §12.6, §12.7).

The bank transaction is the source of truth. When a transaction is split,
dashboard calculations use the split rows instead of the transaction's own
category/project (spec §37).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.tag import transaction_tags


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"
    # Composite indexes for the hot list/aggregate paths: the transactions list
    # filters by account and orders/filters by date; aggregates exclude archived
    # rows over a date range (CR-FEAT-6). Names match the migration so create_all
    # (tests) and Alembic (runtime) agree.
    __table_args__ = (
        Index("ix_transactions_account_id_date", "account_id", "transaction_date"),
        Index("ix_transactions_archived_at_date", "archived_at", "transaction_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True
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
        ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True, index=True
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    # Optional ISO-3166 alpha-2 country for the spend-by-location map (e.g. a trip
    # to Spain → ES). Highest-precedence signal; falls back to vendor country then
    # currency. (geo.py / dashboard_service.country_breakdown)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # debit | credit
    direction: Mapped[str] = mapped_column(String(8), nullable=False)

    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )

    is_split: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_transfer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_income: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Business expense (vs personal) + the VAT portion in the txn's own currency
    # (business/VAT receipts). Default off/NULL; no normal aggregate reads these.
    is_business: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    vat_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # sha256(account|date|amount|currency|description|posted_date) — spec §14.5
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # The underlying funding card a Curve (overlay) row was charged to, as
    # labelled in the Curve export (e.g. "Credit Card ••1006"). NULL for ordinary
    # statements. Maps (via CurveFundingLink) to the real account, so the same
    # spend on that card's own statement can be deduped (curve_link_service).
    funding_source: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    # Multi-currency (backlog #29): the transaction's amount converted to the
    # household base currency. base_amount is NULL + needs_rate=True when a
    # foreign-currency transaction has no FX rate yet. Same-currency rows get
    # base_amount = amount and fx_rate = 1. Existing converted values are never
    # rewritten; only missing ones are backfilled.
    base_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    fx_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    needs_rate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Set by the retention engine (backlog #78): archived transactions are hidden
    # from every aggregate and the default list (kept until a later purge). NULL =
    # active. Excluded via services/scope.archived_condition.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    splits: Mapped[list[TransactionSplit]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    tags: Mapped[list[Tag]] = relationship(secondary=transaction_tags)


class TransactionSplit(Base, TimestampMixin):
    __tablename__ = "transaction_splits"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    transaction: Mapped[Transaction] = relationship(back_populates="splits")


# Imported here so ``relationship("Tag", ...)`` resolves when models load.
from app.models.tag import Tag  # noqa: E402
