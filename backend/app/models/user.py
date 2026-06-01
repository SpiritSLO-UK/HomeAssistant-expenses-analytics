"""User model (spec §6, §12.2).

Identity comes from Home Assistant ingress (the reverse proxy sets
``X-Remote-User-*`` headers); standalone/dev falls back to a single local owner.
Users therefore *appear* on first request rather than being created by a form —
the owner then manages their role and approval status (backlog #82, #126).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# Roles (most→least privileged). ``owner`` is the administrator.
#   owner  — full admin: user management, settings, destructive/system actions
#   member — read + write their own/shared finance data
#   viewer — read-only
#   child  — read-only, intended for a limited budget/savings view (frontend nav)
ROLES = ("owner", "member", "viewer", "child")
WRITE_ROLES = ("owner", "member")  # may perform non-GET requests
ADMIN_ROLES = ("owner",)  # may manage users / system

# Account lifecycle (backlog #126: a new user is pending until approved).
STATUSES = ("pending", "approved", "disabled")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    # Stable identifier from the HA ingress header (``X-Remote-User-Id``), or
    # ``"local"`` for the standalone single-user fallback. Used to match a
    # returning user to their row.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="owner")
    # pending | approved | disabled — the gate the API enforces.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="approved")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Optional app-level MFA (TOTP, backlog #124). ``mfa_secret`` is the base32
    # seed; ``mfa_enabled`` flips true only after the user confirms a code.
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
