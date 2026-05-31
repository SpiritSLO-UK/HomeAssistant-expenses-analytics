"""Tag model and the transaction<->tag association table (spec §12.13)."""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, String, Table

from app.db.base import Base, TimestampMixin
from sqlalchemy.orm import Mapped, mapped_column

# Many-to-many association between transactions and tags.
transaction_tags = Table(
    "transaction_tags",
    Base.metadata,
    Column(
        "transaction_id",
        ForeignKey("transactions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    colour: Mapped[str | None] = mapped_column(String(16), nullable=True)
