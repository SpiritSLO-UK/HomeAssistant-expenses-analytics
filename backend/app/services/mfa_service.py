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

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, UserSession
from app.models.user import MFA_SCOPES
from app.services import crypto_service, totp

ISSUER = "HA Finance"

# How long a verified session stays valid before the app-entry gate re-prompts.
SESSION_TTL = timedelta(hours=12)
# How recently a TOTP must have been entered to perform an admin action.
STEP_UP_TTL = timedelta(minutes=10)

# Online brute-force throttle on MFA code checks (CR-SEC-6). Per-user, in memory:
# an attacker can't restart the process, so a process-lifetime sliding window is
# enough, and it's cleared on a successful verification. After MFA_MAX_FAILED
# failures within MFA_LOCKOUT, further attempts are refused until the oldest ages
# out. (The 6-digit TOTP space is small enough that an unthrottled endpoint is
# brute-forceable over time.)
MFA_MAX_FAILED = 5
MFA_LOCKOUT = timedelta(minutes=5)
_failed_mfa_attempts: dict[int, list[datetime]] = {}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- TOTP secret at-rest encryption (CR-SEC-13) ------------------------------
#
# The stored ``mfa_secret`` used to be the raw base32 TOTP seed (plaintext,
# protected only by *optional* at-rest DB encryption). We now app-layer-encrypt
# it with the same AES-256-GCM + scrypt primitive used for backups
# (``crypto_service``), keyed on the configured app secret (``HAFI_DB_KEY`` —
# the one key we already require to be present on every start in stored-key
# mode). No schema change: the ciphertext is stored as a string in the same
# TEXT column, tagged with ``_ENC_PREFIX`` so it can never be mistaken for a
# legacy plaintext secret.
#
# Key availability is the crux: the key must be present on *every* verify. If no
# app key is configured we DO NOT encrypt (encrypting with a key that isn't
# reliably present at verify time would lock users out) — we fall back to the
# previous plaintext behaviour. Existing plaintext secrets keep working: reads
# detect the absence of the marker and use the value as-is. New / re-enrolled
# secrets are encrypted whenever a key is available.
#
# A base32 TOTP seed is only ``[A-Z2-7]`` (upper-case, no ':'), so a value that
# starts with the lower-case, colon-bearing marker can never be a real seed —
# the plaintext fallback cannot mis-fire.
_ENC_PREFIX = "mfaenc1:"


def _app_key() -> str | None:
    """The app secret used to encrypt the TOTP seed, or ``None`` if not set.

    Read live (not cached) so a runtime change / test monkeypatch is honoured.
    """
    key = settings.db_key
    return key or None


def encrypt_secret(secret: str | None) -> str | None:
    """The value to persist for a TOTP seed: app-layer ciphertext when an app key
    is configured, otherwise the seed unchanged (no key → plaintext fallback)."""
    key = _app_key()
    if not secret or not key:
        return secret
    blob = crypto_service.encrypt(secret.encode("utf-8"), key)
    return _ENC_PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt_secret(stored: str | None) -> str | None:
    """Recover the usable base32 TOTP seed from a stored value.

    Legacy plaintext seeds (no marker) are returned as-is. A marked value is
    decrypted with the app key; if the key is missing or decryption fails (e.g.
    a rotated key) we return ``None`` so verification fails closed rather than
    crashing — the seed is simply unusable until the correct key is restored.
    """
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored  # legacy plaintext seed (or None/empty)
    key = _app_key()
    if not key:
        return None
    try:
        blob = base64.b64decode(stored[len(_ENC_PREFIX):].encode("ascii"), validate=True)
        return crypto_service.decrypt(blob, key).decode("utf-8")
    except (crypto_service.DecryptError, ValueError):
        # binascii.Error and UnicodeDecodeError both subclass ValueError, so
        # catching ValueError already covers them.
        return None


# --- MFA brute-force throttle (CR-SEC-6) -------------------------------------


def _recent_mfa_failures(user_id: int) -> list[datetime]:
    cutoff = _now() - MFA_LOCKOUT
    times = [t for t in _failed_mfa_attempts.get(user_id, []) if t >= cutoff]
    if times:
        _failed_mfa_attempts[user_id] = times
    else:
        _failed_mfa_attempts.pop(user_id, None)
    return times


def mfa_lockout_seconds(user_id: int) -> int:
    """Seconds until ``user_id`` may try an MFA code again, or 0 if not locked out."""
    times = _recent_mfa_failures(user_id)
    if len(times) < MFA_MAX_FAILED:
        return 0
    return max(0, int((min(times) + MFA_LOCKOUT - _now()).total_seconds()))


def record_mfa_failure(user_id: int) -> int:
    """Note a failed MFA code check; returns the recent-failure count."""
    _failed_mfa_attempts.setdefault(user_id, []).append(_now())
    return len(_recent_mfa_failures(user_id))


def clear_mfa_failures(user_id: int) -> None:
    """Forget a user's recent MFA failures (called on a successful verification)."""
    _failed_mfa_attempts.pop(user_id, None)


def reset_throttle() -> None:
    """Clear ALL throttle state — used by tests for isolation (process-global)."""
    _failed_mfa_attempts.clear()


# --- Enrolment ---------------------------------------------------------------


def start_enrolment(db: Session, user: User, code: str | None = None) -> dict | None:
    """Begin (or restart) enrolment: stash a *pending* secret without touching the
    live factor. ``enable`` promotes it once the user confirms a code (SR-1).

    For an already-enabled user this is a re-enrolment and requires a valid
    *current* code first — otherwise an authenticated-but-unverified caller could
    reset the second factor. Returns the new secret + otpauth URI, or ``None`` when
    a required re-enrolment code is missing/invalid (the route maps that to 400).
    """
    live = decrypt_secret(user.mfa_secret)
    if user.mfa_enabled and (not code or not live or not totp.verify(live, code)):
        return None
    secret = totp.generate_secret()
    user.mfa_pending_secret = encrypt_secret(secret)
    db.commit()
    return {
        "secret": secret,
        "otpauth_uri": totp.provisioning_uri(secret, user.display_name, ISSUER),
    }


def enable(db: Session, user: User, code: str, scope: str | None = None) -> bool:
    """Confirm enrolment: verify a code against the *pending* secret, then promote
    it to the live secret and turn MFA on. ``scope`` (#157) sets what MFA gates —
    ``app`` (entry only) or ``app_admin`` (entry + admin step-up); an unknown/None
    value keeps ``app_admin``."""
    pending = decrypt_secret(user.mfa_pending_secret)
    if not pending or not totp.verify(pending, code):
        return False
    user.mfa_secret = encrypt_secret(pending)
    user.mfa_pending_secret = None
    user.mfa_enabled = True
    user.mfa_scope = scope if scope in MFA_SCOPES else "app_admin"
    user.mfa_last_counter = None  # new secret → its own timeline (CR-SEC-5)
    db.commit()
    return True


def disable(db: Session, user: User, code: str) -> bool:
    """Turn MFA off (current code required) and drop every session for the user."""
    live = decrypt_secret(user.mfa_secret)
    if not user.mfa_enabled or not live:
        return False
    if not totp.verify(live, code):
        return False
    user.mfa_enabled = False
    user.mfa_secret = None
    user.mfa_pending_secret = None
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()
    return True


# --- Sessions ----------------------------------------------------------------


def verify_and_open(db: Session, user: User, code: str) -> str | None:
    """Verify a code and mint a per-device session token (returns the raw token).

    Enforces one-time use on this entry path (CR-SEC-5): a code whose timestep was
    already consumed is refused, so a sniffed code can't be replayed to mint a
    second session. (Step-up/disable operate on an already-valid session and may
    reuse the current in-period code, which the user legitimately does in one go.)
    """
    live = decrypt_secret(user.mfa_secret)
    if not user.mfa_enabled or not live:
        return None
    counter = totp.matched_counter(live, code)
    if counter is None:
        return None
    if user.mfa_last_counter is not None and counter <= user.mfa_last_counter:
        return None  # replay — this timestep was already used to open a session
    user.mfa_last_counter = counter
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
    live = decrypt_secret(user.mfa_secret)
    if session is None or not live:
        return False
    if not totp.verify(live, code):
        return False
    session.last_step_up_at = _now()
    db.commit()
    return True


def has_recent_step_up(session: UserSession | None) -> bool:
    if session is None or session.last_step_up_at is None:
        return False
    return session.last_step_up_at >= _now() - STEP_UP_TTL
