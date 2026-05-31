"""AIRequest model (spec §12.19, §22.6). Every AI call must be logged."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIRequest(Base):
    __tablename__ = "ai_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # classify_transaction | enrich_vendor | parse_receipt | match_receipt |
    # suggest_category | detect_subscription
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # strict_local | local_llm | cloud_manual | cloud_auto | no_ai
    privacy_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    # not_required | pending | approved | rejected
    approval_status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_required")
    redacted_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # pending | completed | failed | rejected
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
