"""ReviewItem model (spec §12.18, §23). The main safety mechanism."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReviewItem(Base):
    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    # transaction | vendor | receipt | category | ai_request | import
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # unknown_vendor | unknown_category | low_confidence | duplicate_possible |
    # receipt_unmatched | split_invalid | cloud_ai_approval_required |
    # sensitive_data_detected | parser_error
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    # info | warning | error
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    # open | resolved | ignored
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)
    suggested_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
