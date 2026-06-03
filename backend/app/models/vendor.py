"""Vendor and VendorAlias models (spec §12.9, §12.10)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Vendor(Base, TimestampMixin):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    default_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    service_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # ISO-3166 alpha-2 country code (e.g. GB, US, FR) for the spend-by-location
    # map. Optional; when unset, a transaction's country is inferred from its
    # currency. (geo.py / dashboard_service.country_breakdown)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # rule | user | local_ai | cloud_ai | import
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    aliases: Mapped[list[VendorAlias]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )


class VendorAlias(Base, TimestampMixin):
    __tablename__ = "vendor_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    # exact | contains | regex | fuzzy
    match_type: Mapped[str] = mapped_column(String(16), nullable=False, default="contains")
    # user | ai | import
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    vendor: Mapped[Vendor] = relationship(back_populates="aliases")
