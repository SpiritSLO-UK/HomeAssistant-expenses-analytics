"""Saved CSV import column-mapping profiles (backlog: user-defined CSV import).

When a bank has no built-in parser, the user maps the CSV's columns to our
logical fields in the UI; saving that mapping as a named, reusable **import
profile** means a future statement from the same bank imports in one click. The
mapping is just ``{logical_field: csv_header}`` — no transaction data — so a
profile is safe to export/share.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ImportProfile(Base, TimestampMixin):
    __tablename__ = "import_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # JSON object mapping a logical field (date/amount/debit/credit/description/
    # merchant/currency/category/external_id/posted_date) to the CSV header name.
    mapping_json: Mapped[str] = mapped_column(Text, nullable=False)
    default_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="GBP")
    # How to read ambiguous CSV dates: "auto" (heuristic per-file detection, the
    # historic behaviour), "dmy" (force UK day-first DD/MM) or "mdy" (force US
    # month-first MM/DD). Needed for all-ambiguous US statements (every day ≤ 12),
    # where auto-detection has no evidence. Maps to GenericCsvParser(month_first=…).
    date_format: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="auto", default="auto"
    )
