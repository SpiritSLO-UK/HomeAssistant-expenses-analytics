"""Asset & asset-log models (spec §25.1; backlog: car/home dashboards).

An :class:`Asset` is a tracked thing that isn't a bank account — a **car**, a
**home**, or something **other** — with a timeline of :class:`AssetLog` entries.
Each log has a ``kind`` and the fields that kind needs:

- ``refuel`` (car): ``odometer`` (in the asset's ``distance_unit``), ``litres``,
  ``cost``, ``is_full_tank`` (a partial fill can't be used for consumption),
  ``fuel_type`` → consumption stats (MPG and L/100km) between full fills.
- ``service`` / ``expense`` (any): ``cost`` (+ optional ``odometer`` for a car).
- ``reading`` (home, PR D): a ``meter`` (electricity/gas/water), a ``reading``
  value and its ``unit``.
- ``note``: free text.

Assets are household-level (like projects/budgets), visible to every approved
user. A log may optionally link to a ledger ``transaction`` so its cost ties back
to the imported statement.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Asset(Base, TimestampMixin):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # car | home | other
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="car")
    # Reg plate, address, model — free-form label.
    identifier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Distance unit the odometer is entered in (cars). UK default = miles.
    distance_unit: Mapped[str] = mapped_column(String(8), nullable=False, default="mi")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AssetLog(Base, TimestampMixin):
    __tablename__ = "asset_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    log_date: Mapped[date] = mapped_column(Date, nullable=False)
    # refuel | service | expense | reading | note
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="refuel")
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # --- Car refuel/service fields ---
    odometer: Mapped[Decimal | None] = mapped_column(Numeric(12, 1), nullable=True)
    litres: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    # A partial fill can't anchor a tank-to-tank consumption figure.
    is_full_tank: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)
    fuel_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # --- Home reading fields (used in PR D; columns shipped now) ---
    meter: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reading: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Optional link back to the ledger transaction this entry's cost came from.
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
