"""Tag model and the transaction<->tag association table (spec §12.13)."""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, String, Table, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

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

    # Case-insensitive uniqueness per household so "Work" and "work" can't both be
    # created (SR-B8). household_id is nullable and SQLite/Postgres treat NULLs as
    # distinct in a unique index, so scope by COALESCE(household_id, -1). Name mirrors
    # the Alembic migration so create_all (tests) and Alembic (runtime) agree.
    __table_args__ = (
        Index(
            "ix_tags_household_lower_name",
            text("COALESCE(household_id, -1)"),
            text("lower(name)"),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    colour: Mapped[str | None] = mapped_column(String(16), nullable=True)
