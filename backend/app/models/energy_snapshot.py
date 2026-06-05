"""EnergySnapshot model — periodic samples of produced energy (backlog: energy
production/saving trend over time).

Each row is the summed production reading (kWh) from the configured source at a
point in time. The trend is derived from these: for a **cumulative** sensor the
per-period production is the difference between consecutive boundary readings; for
an **interval** sensor each snapshot is the production since the last read and the
period total is their sum. Captured best-effort when the offset is read live.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EnergySnapshot(Base):
    __tablename__ = "energy_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    # Summed production reading (kWh) across the configured entities/topics at capture.
    produced: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    # ha_api | mqtt — which source produced this reading.
    source: Mapped[str] = mapped_column(String(16), nullable=False)
