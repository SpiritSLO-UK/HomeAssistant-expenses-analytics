"""AuditLog model (spec §12.20, §28.5). Everything external/important is logged."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    # User display name or "system".
    actor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # import_statement | delete_transaction | update_category | create_rule |
    # approve_ai_request | send_cloud_ai_request | confirm_receipt_match | ...
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    # Set by the retention engine (backlog #78): archived entries are hidden from
    # the activity-log viewer but kept until a later purge. NULL = active.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
