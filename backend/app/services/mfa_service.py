"""App-level MFA (TOTP) enrolment, verification and session handling (#124).

MFA is **opt-in per user** and layered on top of Home Assistant's own auth (it's
a second factor *inside* the app). Flow:

1. ``start_enrolment`` generates a secret and returns the otpauth URI; the user
   adds it to their authenticator. ``mfa_enabled`` stays False until they confirm.
2. ``enable`` verifies a code and turns MFA on.
3. On opening the app, a user with MFA enabled must ``verify_and_open`` a code,
   which mints a per-device :class:`UserSession` token (the app-entry gate).
4. Admin actions additionally require a recent ``step_up`` on that session.
5. ``disable`` (code required) turns MFA off and drops all sessions.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import User, UserSession
from app.models.user import MFA_SCOPES
from app.services import totp

ISSUER = "HA Finance"

# How long a verified session stays valid before the app-entry gate re-prompts.
SESSION_TTL = timedelta(hours=12)
# How recently a TOTP must have been entered to perform an admin action.
STEP_UP_TTL = timedelta(minutes=10)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- Enrolment ---------------------------------------------------------------


def start_enrolment(db: Session, user: User) -> dict:
    """Generate (or regenerate, if not yet confirmed) a secret for ``user``."""
    secret = totp.generate_secret()
    user.mfa_secret = secret
    user.mfa_enabled = False
    db.commit()
    return {
        "secret": secret,
        "otpauth_uri": totp.provisioning_uri(secret, user.display_name, ISSUER),
    }


def enable(db: Session, user: User, code: str, scope: str | None = None) -> bool:
    """Confirm enrolment: verify a code against the pending secret, then turn on.
    ``scope`` (#157) sets what MFA gates — ``app`` (entry only) or ``app_admin``
    (entry + admin step-up); an unknown/None value keeps ``app_admin``."""
    if not user.mfa_secret or not totp.verify(user.mfa_secret, code):
        return False
    user.mfa_enabled = True
    user.mfa_scope = scope if scope in MFA_SCOPES else "app_admin"
    db.commit()
    return True


def disable(db: Session, user: User, code: str) -> bool:
    """Turn MFA off (current code required) and drop every session for the user."""
    if not user.mfa_enabled or not user.mfa_secret:
        return False
    if not totp.verify(user.mfa_secret, code):
        return False
    user.mfa_enabled = False
    user.mfa_secret = None
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()
    return True


# --- Sessions ----------------------------------------------------------------


def verify_and_open(db: Session, user: User, code: str) -> str | None:
    """Verify a code and mint a per-device session token (returns the raw token)."""
    if not user.mfa_enabled or not user.mfa_secret:
        return None
    if not totp.verify(user.mfa_secret, code):
        return None
    # Tidy expired sessions for this user while we're here.
    db.execute(
        delete(UserSession).where(
            UserSession.user_id == user.id, UserSession.expires_at < _now()
        )
    )
    raw = secrets.token_urlsafe(32)
    now = _now()
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=_hash_token(raw),
            expires_at=now + SESSION_TTL,
            last_step_up_at=now,
        )
    )
    db.commit()
    return raw


def get_valid_session(db: Session, user_id: int, token: str | None) -> UserSession | None:
    if not token:
        return None
    row = db.scalars(
        select(UserSession).where(UserSession.token_hash == _hash_token(token))
    ).first()
    if row is None or row.user_id != user_id or row.expires_at < _now():
        return None
    return row


def has_valid_session(db: Session, user_id: int, token: str | None) -> bool:
    return get_valid_session(db, user_id, token) is not None


def step_up(db: Session, user: User, token: str | None, code: str) -> bool:
    """Re-verify a code on the current session, refreshing the step-up window."""
    session = get_valid_session(db, user.id, token)
    if session is None or not user.mfa_secret:
        return False
    if not totp.verify(user.mfa_secret, code):
        return False
    session.last_step_up_at = _now()
    db.commit()
    return True


def has_recent_step_up(session: UserSession | None) -> bool:
    if session is None or session.last_step_up_at is None:
        return False
    return session.last_step_up_at >= _now() - STEP_UP_TTL
