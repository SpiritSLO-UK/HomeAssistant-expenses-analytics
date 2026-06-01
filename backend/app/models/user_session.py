"""MFA session model (backlog #124).

When a user with MFA enabled passes a TOTP challenge, we issue a random session
token and store **only its SHA-256 hash** here (the raw token lives in the
browser). The app-entry gate requires a valid, unexpired session; admin actions
additionally require a recent step-up (``last_step_up_at``).

Sessions are per-device (each browser gets its own token) and are cleared when
the user disables MFA. They are in the app database, so at-rest encryption (when
enabled) protects them too.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Last successful TOTP entry on this session — drives the admin step-up window.
    last_step_up_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
