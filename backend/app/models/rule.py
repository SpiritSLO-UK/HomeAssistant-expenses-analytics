"""Rule model (spec §12.11, §36).

A rule is a condition -> action pair applied during import and on demand.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Rule(Base, TimestampMixin):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # merchant_contains | description_contains | vendor_equals | account_equals |
    # amount_equals | amount_between | recurring_payment | category_equals | source_format
    condition_type: Mapped[str] = mapped_column(String(32), nullable=False)
    condition_value: Mapped[str] = mapped_column(String(512), nullable=False)

    # set_vendor | set_category | set_project | set_country | mark_transfer |
    # mark_income | mark_subscription | require_review | block_cloud_ai
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    action_value: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # user | manual_correction | import | system
    created_from: Mapped[str | None] = mapped_column(String(32), nullable=True)
