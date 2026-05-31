"""Setting model (spec §12.1, §38). Key/value app settings persisted in the DB.

Bootstrap configuration comes from environment variables (``app.config``);
this table holds user-editable settings managed through the Settings UI.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Setting(Base, TimestampMixin):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # JSON-encoded value; interpreted by the settings service.
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
