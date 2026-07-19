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
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import MfaBackupCode, User, UserSession
from app.models.user import MFA_SCOPES
from app.services import crypto_service, totp

ISSUER = "HA Finance"

# How long a verified session stays valid before the app-entry gate re-prompts.
SESSION_TTL = timedelta(hours=12)
# How recently a TOTP must have been entered to perform an admin action.
STEP_UP_TTL = timedelta(minutes=10)
# Cap on stored sessions per user: re-verifying from many devices/browsers (or a
# script) can't grow ``user_sessions`` without bound. On each new verification we
# evict the oldest beyond this many (LRU by creation).
MAX_SESSIONS_PER_USER = 10

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
    """Hash a raw session token for storage and lookup.

    When an app key (``HAFI_DB_KEY``) is configured the digest is keyed with it
    via HMAC-SHA256, so a stolen ``user_sessions`` table can't be turned into a
    forged token without also knowing the server key (a bare unsalted hash of a
    guessed/leaked token would otherwise match a row directly). With no app key
    set we fall back to a plain SHA-256 — the same key-availability posture as
    the TOTP-secret encryption above. Both digests are 64 hex chars, matching the
    ``token_hash`` column.

    NOTE: enabling a key (or rotating it) changes every token's hash, so existing
    sessions stop matching and users re-verify once — acceptable given the
    12-hour session TTL.
    """
    key = _app_key()
    if key:
        return hmac.new(
            key.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
        ).hexdigest()
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
    # encrypt_internal (not encrypt): HAFI_DB_KEY is an internal app key, not a
    # user-chosen backup passphrase, so the 8-char backup floor must not apply —
    # a short db_key must still let enrolment store a seed rather than 500 (#22).
    # Same container format, so existing ``mfaenc1:`` seeds still decrypt.
    blob = crypto_service.encrypt_internal(secret.encode("utf-8"), key)
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
        return crypto_service.decrypt_internal(blob, key).decode("utf-8")
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
    count = len(_recent_mfa_failures(user_id))
    # Opt-in HA notification (best-effort). Only the type + recent count leave the
    # process — never the TOTP code, and no user id / PII in the payload. Lazy
    # import avoids an import cycle and keeps auth independent of MQTT importing.
    from app.services import mqtt_service

    mqtt_service.publish_security_event_safe("failed_mfa", count)
    return count


def clear_mfa_failures(user_id: int) -> None:
    """Forget a user's recent MFA failures (called on a successful verification)."""
    _failed_mfa_attempts.pop(user_id, None)


def reset_throttle() -> None:
    """Clear ALL throttle state — used by tests for isolation (process-global)."""
    _failed_mfa_attempts.clear()


# --- Backup / recovery codes (CR-FEAT-1) -------------------------------------
#
# Single-use codes that let a user who has lost their authenticator get back in
# (critical when ``mfa_policy="required"``, which otherwise means a permanent
# lockout). We generate N plaintext codes ONCE — shown to the user immediately —
# and persist only their hashes, reusing the session-token hashing (HMAC-SHA256
# keyed on the app key, plain SHA-256 without one). A code is accepted on the
# entry path or in ``disable``; the matching row is stamped ``used_at`` so it can
# never be replayed. Regenerating replaces the whole set.

BACKUP_CODE_COUNT = 10
# Length of each generated code. Drawn from an unambiguous alphabet (no 0/O/1/I)
# so a user can transcribe it reliably from a printout.
_BACKUP_CODE_LEN = 10
_BACKUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _new_backup_code() -> str:
    return "".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(_BACKUP_CODE_LEN))


def _normalize_backup_code(code: str) -> str:
    """Fold whitespace/dashes and case so entry is forgiving (``ab-cd 12`` == ``ABCD12``)."""
    return "".join(code.split()).replace("-", "").upper()


def _hash_backup_code(code: str) -> str:
    """Storage hash for a backup code — same keyed digest as session tokens."""
    return _hash_token(_normalize_backup_code(code))


def generate_backup_codes(db: Session, user: User) -> list[str]:
    """Issue a fresh set of ``BACKUP_CODE_COUNT`` plaintext codes, returned ONCE.

    Any previous (used or unused) codes for the user are discarded first, so the
    caller can safely treat the returned list as the complete new set. Only the
    hashes are persisted — the plaintext is never stored and cannot be recovered.
    """
    db.execute(delete(MfaBackupCode).where(MfaBackupCode.user_id == user.id))
    codes = [_new_backup_code() for _ in range(BACKUP_CODE_COUNT)]
    for code in codes:
        db.add(MfaBackupCode(user_id=user.id, code_hash=_hash_backup_code(code)))
    db.commit()
    return codes


def backup_codes_remaining(db: Session, user: User) -> int:
    """How many unused backup codes the user has left (for the UI)."""
    return (
        db.scalar(
            select(func.count())
            .select_from(MfaBackupCode)
            .where(MfaBackupCode.user_id == user.id, MfaBackupCode.used_at.is_(None))
        )
        or 0
    )


def _consume_backup_code(db: Session, user: User, code: str) -> bool:
    """Spend an UNUSED backup code matching ``code``; stamp it used and return True.

    Compares in constant time against every unused hash (never short-circuits on
    the first differing byte). The caller is responsible for committing.
    """
    target = _hash_backup_code(code)
    rows = db.scalars(
        select(MfaBackupCode).where(
            MfaBackupCode.user_id == user.id, MfaBackupCode.used_at.is_(None)
        )
    ).all()
    for row in rows:
        if hmac.compare_digest(row.code_hash, target):
            row.used_at = _now()
            return True
    return False


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
    # A re-enrolment mints a brand-new secret; any sessions opened against the
    # OLD secret must not outlive it (they were second-factor'd with a factor
    # that no longer exists). Drop every session so only ones verified against
    # the new secret are valid. On a first-time enable there are none to drop.
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()
    return True


def disable(db: Session, user: User, code: str) -> bool:
    """Turn MFA off and drop every session + backup code for the user.

    Accepts either a current TOTP code or an unused backup code (single-use), so a
    user who has lost their authenticator can still recover out of a ``required``
    lockout instead of being stuck forever."""
    live = decrypt_secret(user.mfa_secret)
    if not user.mfa_enabled or not live:
        return False
    if not (totp.verify(live, code) or _consume_backup_code(db, user, code)):
        return False
    user.mfa_enabled = False
    user.mfa_secret = None
    user.mfa_pending_secret = None
    db.execute(delete(MfaBackupCode).where(MfaBackupCode.user_id == user.id))
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()
    return True


# --- Sessions ----------------------------------------------------------------


def _evict_excess_sessions(db: Session, user_id: int) -> None:
    """Keep at most ``MAX_SESSIONS_PER_USER`` newest sessions; drop the rest.

    Ordered newest-first by creation (id breaks same-timestamp ties, so the row
    just added — highest id — is always kept), then everything past the cap is
    deleted. Bounds per-user session growth (backlog item)."""
    excess = db.scalars(
        select(UserSession.id)
        .where(UserSession.user_id == user_id)
        .order_by(UserSession.created_at.desc(), UserSession.id.desc())
        .offset(MAX_SESSIONS_PER_USER)
    ).all()
    if excess:
        db.execute(delete(UserSession).where(UserSession.id.in_(excess)))


def _accept_entry_code(db: Session, user: User, live: str, code: str) -> bool:
    """Whether ``code`` opens the entry gate — a fresh (non-replayed) TOTP or an
    unused backup code. Advances the anti-replay counter / spends the backup code
    as a side effect; the caller commits."""
    counter = totp.matched_counter(live, code)
    if counter is not None:
        if user.mfa_last_counter is not None and counter <= user.mfa_last_counter:
            return False  # replay — this timestep already opened a session
        user.mfa_last_counter = counter
        return True
    # Not a TOTP match: fall back to a single-use backup / recovery code.
    return _consume_backup_code(db, user, code)


def verify_and_open(db: Session, user: User, code: str) -> str | None:
    """Verify a code and mint a per-device session token (returns the raw token).

    Accepts a current TOTP code or a single-use backup code (lost-authenticator
    recovery). Enforces one-time use on this entry path (CR-SEC-5): a TOTP whose
    timestep was already consumed is refused, so a sniffed code can't be replayed
    to mint a second session; a backup code is stamped used the first time it
    opens the gate. (Step-up/disable operate on an already-valid session and may
    reuse the current in-period code, which the user legitimately does in one go.)
    """
    live = decrypt_secret(user.mfa_secret)
    if not user.mfa_enabled or not live:
        return None
    if not _accept_entry_code(db, user, live, code):
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
    db.flush()  # assign the new row its id/created_at before we pick what to evict
    _evict_excess_sessions(db, user.id)
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
