"""MFA single-use backup / recovery codes (CR-FEAT-1).

A lost authenticator with ``mfa_policy="required"`` would otherwise mean a
permanent lockout with no in-app recovery. Each row is ONE hashed recovery code:
the plaintext is shown to the user exactly once at generation time and only its
hash is stored here (same HMAC/SHA-256-keyed-on-``db_key`` approach as the
session ``token_hash``). A code is single-use — accepting it in the MFA
verification / disable path stamps ``used_at`` so it can never be replayed.

Regenerating replaces the whole set (rows are deleted and re-created), and
disabling MFA clears every code for the user.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MfaBackupCode(Base):
    __tablename__ = "mfa_backup_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Keyed hash of the normalised plaintext code (64 hex chars, like token_hash).
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    # NULL while the code is still spendable; stamped when it is consumed.
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
