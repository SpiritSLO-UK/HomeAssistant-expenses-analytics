"""CurveFundingLink model — maps a Curve funding-card label to a real account.

Curve is an overlay/pass-through card: every payment is forwarded to whichever
underlying card funded it, so the same spend also appears on that card's own
statement. The user tells us which app account each Curve "Card Name" really is
(e.g. ``Credit Card ••1006`` → their Barclays account); cross-account dedup then
recognises the duplicate across the two statements (curve_link_service).

Household-scoped; the label is unique per household (case-insensitive match is
done in the service).
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class CurveFundingLink(Base, TimestampMixin):
    __tablename__ = "curve_funding_links"
    __table_args__ = (
        UniqueConstraint("household_id", "label", name="uq_curve_funding_links_household_label"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    # The funding-card label exactly as it appears in the Curve export.
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    # The underlying real account this funding card represents.
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
