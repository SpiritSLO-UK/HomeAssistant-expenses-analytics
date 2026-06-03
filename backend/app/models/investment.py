"""Investment & pension models (spec §12.4, §27; backlog stretch: pensions + investments).

Investment and pension accounts are regular :class:`~app.models.account.Account`
rows with ``account_type in {"investment", "pension"}``. Their worth is tracked
two ways, and an account may use either (or both):

- **Value snapshots** (:class:`AccountValue`) — "this account was worth £X on
  date Y", typically copied from a pension/platform statement. Mirrors the
  savings ``SavingsBalance`` snapshot pattern; the latest snapshot is the
  account's value and a series charts growth.
- **Holdings** (:class:`Holding`) — individual positions: N units of a ticker at
  an average cost, with a last price. Market value = units × last_price and the
  unrealised gain = market value − cost basis. Prices are entered manually; an
  optional price feed updates ``last_price``/``last_price_at`` (follow-up PR).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AccountValue(Base):
    __tablename__ = "account_values"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class HoldingPrice(Base):
    """Price history for a holding — one row per (holding, date), recorded whenever
    the price is set/updated/synced. Lets the portfolio value be reconstructed over
    time for charts + day/month/year change figures (today's `Holding.last_price` is
    only the latest point)."""

    __tablename__ = "holding_prices"

    id: Mapped[int] = mapped_column(primary_key=True)
    holding_id: Mapped[int] = mapped_column(
        ForeignKey("holdings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class Holding(Base, TimestampMixin):
    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Ticker symbol, e.g. "VWRL.L" or "AAPL". Uppercased on write.
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Fractional units are common (e.g. £100 of a £312 share), so 6 dp.
    units: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    # Average cost per unit (the cost basis) — optional; without it no gain is shown.
    avg_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    # Last known price per unit (manual or, later, auto-fetched) + when it was set.
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    last_price_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
