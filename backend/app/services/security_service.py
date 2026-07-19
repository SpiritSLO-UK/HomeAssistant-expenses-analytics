"""At-rest database encryption (backlog #15b).

Optional SQLCipher encryption of the SQLite database. The user chooses a
passphrase (or not). Two unlock modes:

- ``prompt``  — key held in memory only; after each restart the app is locked
  until the passphrase is entered in the UI.
- ``stored``  — passphrase supplied via the ``HAFI_DB_KEY`` env/add-on option so
  the add-on starts unattended (key lives on the device — weaker).

**There is no recovery if the passphrase is lost.**

Requires the optional ``sqlcipher3`` driver, which is available on Linux / the
add-on but has no Windows wheel. On Windows the feature reports as unavailable
and the app runs plaintext as before.

Encryption marker (plaintext, next to the DB): ``encryption.json``
``{"enabled": true, "unlock_mode": "prompt"|"stored"}``
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import settings
from app.db import session as dbsession
from app.logging import get_logger

logger = get_logger(__name__)


def _marker_path() -> Path:
    return settings.database_file.parent / "encryption.json"


def read_marker() -> dict | None:
    path = _marker_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - corrupt marker
        return None


def _write_marker(unlock_mode: str) -> None:
    _marker_path().write_text(
        json.dumps({"enabled": True, "unlock_mode": unlock_mode}), encoding="utf-8"
    )


def _delete_marker() -> None:
    _marker_path().unlink(missing_ok=True)


# --- Stored auto-unlock key file (standalone convenience) --------------------
#
# On the HA add-on the auto-unlock key comes from the add-on option db_key ->
# HAFI_DB_KEY env var, so the container starts unattended. On a standalone
# install there is no such option, so historically the user had to hand-edit
# .env and restart. To make "stored" mode work from the UI we persist the
# passphrase to a protected key file (0600) beside the DB and read it back at
# startup.
#
# Security posture: on standalone the key already lives on the host disk (in
# .env); writing it to a 0600 file on the SAME data volume is the same posture,
# not weaker. True at-rest protection is "prompt" mode, where nothing is stored.
# The env key (add-on / explicit HAFI_DB_KEY) always wins over the file.


def _key_file_path() -> Path:
    return settings.database_file.parent / ".db_key"


def save_stored_key(passphrase: str) -> None:
    """Persist the auto-unlock passphrase to a 0600 file beside the database.

    Never logs the passphrase. Used by "stored" unlock mode on standalone
    installs (no HAFI_DB_KEY env). The add-on path does not call this: the env
    key is authoritative there.
    """
    path = _key_file_path()
    # Create the file with owner-only perms FROM THE OUTSET (#28). The old
    # write_text-then-chmod left a create-then-chmod window in which the plaintext
    # passphrase sat on disk world-readable (~0644 under the usual umask). We remove
    # any pre-existing file and O_CREAT|O_EXCL a fresh one with mode 0o600, so the
    # key is never on disk with perms wider than 0600.
    path.unlink(missing_ok=True)
    # NOSONAR(python:S2083): the path is the FIXED ".db_key" filename joined to the
    # app's own operator-configured data dir (settings.database_file.parent). It is
    # not request/attacker-controlled, and mirrors the existing encryption.json marker
    # write. Flagged as a path-injection false positive.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # NOSONAR
    try:
        os.write(fd, passphrase.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)  # belt-and-braces (also sets the read-only bit on Windows)


def clear_stored_key() -> None:
    """Remove the stored key file, best-effort (no error if absent)."""
    _key_file_path().unlink(missing_ok=True)


def read_stored_key_file() -> str | None:
    """The stripped contents of the key file, or None if absent/empty."""
    path = _key_file_path()
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def resolve_stored_key() -> str | None:
    """The auto-unlock key to try at startup: env wins, then the saved file.

    settings.db_key (HAFI_DB_KEY / add-on option) is authoritative so the add-on
    behaviour is unchanged; the key file only backs the standalone UI path.
    """
    if settings.db_key:
        return settings.db_key
    return read_stored_key_file()


def stored_key_source() -> str | None:
    """Where the resolved auto-unlock key comes from: "env", "file", or None."""
    if settings.db_key:
        return "env"
    if read_stored_key_file():
        return "file"
    return None


def sqlcipher_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("sqlcipher3") is not None


def _sql_string_literal(value: str) -> str:
    """Return ``value`` as a safely-quoted SQLite/SQLCipher string literal,
    *including* the surrounding single quotes (CR-SEC-15).

    SQLite string literals escape an embedded single quote by doubling it; no
    other character is special (there is no backslash-escaping in a standard
    single-quoted literal), so doubling quotes is complete and correct. A NUL
    byte would be silently truncated by the driver's C string handling and is
    rejected up front so a passphrase can never be quietly shortened.

    This centralises the previously ad-hoc ``"'" + s.replace("'", "''") + "'"``
    interpolation into one audited helper. Passphrase-as-key semantics are
    unchanged: SQLCipher still runs its own KDF over the passphrase text, so
    databases created before this change open unchanged (no re-key).
    """
    if "\x00" in value:
        raise ValueError("Passphrase must not contain a NUL byte.")
    return "'" + value.replace("'", "''") + "'"


def _key_pragma(passphrase: str) -> str:
    """The ``PRAGMA key = '...'`` statement that unlocks the DB with ``passphrase``."""
    return f"PRAGMA key = {_sql_string_literal(passphrase)}"


def _remove_wal_shm() -> None:
    db = str(settings.database_file)
    for suffix in ("-wal", "-shm"):
        Path(db + suffix).unlink(missing_ok=True)


class EncryptionUnavailableError(RuntimeError):
    """At-rest encryption can't be exercised because the SQLCipher driver/runtime
    is unavailable — kept deliberately distinct from a *wrong passphrase* so callers
    and the UI don't conflate "can't check" with "incorrect" (a bare ``return False``
    made them indistinguishable)."""


def verify_passphrase(passphrase: str) -> bool:
    """True if ``passphrase`` opens the encrypted database, False if it's wrong.

    Raises :class:`EncryptionUnavailableError` when the check can't run at all
    (SQLCipher driver missing / environment broken) so a genuine wrong passphrase
    (``False``) is distinguishable from "encryption unavailable".
    """
    return _passphrase_opens(str(settings.database_file), passphrase)


def _passphrase_opens(path: str, passphrase: str) -> bool:
    """Open the encrypted database at ``path`` and confirm ``passphrase`` decrypts it.

    Shared by :func:`verify_passphrase` (the live DB) and :func:`enable_encryption`
    (a freshly-encrypted temp file, verified before it is promoted into place).
    Only a genuine wrong passphrase returns ``False``; an unavailable driver raises
    :class:`EncryptionUnavailableError`.
    """
    if not sqlcipher_available():
        raise EncryptionUnavailableError("SQLCipher driver not available on this platform.")

    import sqlcipher3  # pyright: ignore[reportMissingImports]  -- optional 'encryption' extra

    con = sqlcipher3.connect(path)
    try:
        con.execute(_key_pragma(passphrase))
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return True
    except sqlcipher3.DatabaseError:
        # A wrong passphrase surfaces here: SQLCipher fails to decrypt the header on
        # the first read ("file is not a database"). That — and only that — is False.
        return False
    finally:
        con.close()


def enable_encryption(passphrase: str, unlock_mode: str = "prompt") -> None:
    """Encrypt the current plaintext database in place (via sqlcipher_export)."""
    if not sqlcipher_available():
        raise RuntimeError("SQLCipher driver not available on this platform.")
    if read_marker():
        raise RuntimeError("Encryption is already enabled.")
    if not passphrase:
        raise ValueError("A passphrase is required.")
    if unlock_mode not in {"prompt", "stored"}:
        raise ValueError("unlock_mode must be 'prompt' or 'stored'.")

    import sqlcipher3  # pyright: ignore[reportMissingImports]  -- optional 'encryption' extra

    db_path = str(settings.database_file)
    enc_tmp = db_path + ".enctmp"
    Path(enc_tmp).unlink(missing_ok=True)

    # Release pooled connections so the file isn't locked.
    engine = dbsession.get_engine()
    if engine is not None:
        engine.dispose()

    con = sqlcipher3.connect(db_path)  # opened as plaintext
    try:
        con.execute(f"ATTACH DATABASE '{enc_tmp}' AS enc KEY {_sql_string_literal(passphrase)}")
        con.execute("SELECT sqlcipher_export('enc')")
        con.execute("DETACH DATABASE enc")
    finally:
        con.close()

    # Verify the freshly-encrypted copy opens on its TEMP path *before* moving it into
    # place. Previously os.replace ran first and verification second, so a bad export
    # destroyed the plaintext original before it was ever checked (data loss). Now a
    # failed verification leaves the original database untouched.
    if not _passphrase_opens(enc_tmp, passphrase):
        Path(enc_tmp).unlink(missing_ok=True)
        raise RuntimeError("Encryption verification failed; original database left intact.")

    # Drop any WAL/SHM sidecars the verification open may have created for the temp
    # file so only the single encrypted database file is promoted.
    for suffix in ("-wal", "-shm"):
        Path(enc_tmp + suffix).unlink(missing_ok=True)

    os.replace(enc_tmp, db_path)
    _remove_wal_shm()
    _write_marker(unlock_mode)
    if unlock_mode == "stored" and not settings.db_key:
        # Standalone (no HAFI_DB_KEY env): persist the key so the app auto-unlocks
        # on restart without hand-editing .env. When the env key IS set (add-on) it
        # is authoritative, so we do not write a file. "prompt" mode stores nothing.
        save_stored_key(passphrase)
    else:
        clear_stored_key()  # prompt mode, or add-on env key: no stale file left behind
    dbsession.configure(passphrase)
    logger.info("Database encryption enabled (unlock_mode=%s).", unlock_mode)


def disable_encryption(passphrase: str) -> None:
    """Decrypt the database back to plaintext."""
    if not read_marker():
        raise RuntimeError("Encryption is not enabled.")
    if not passphrase:
        # Mirror enable_encryption's guard: an empty passphrase is a bad request, not a
        # "wrong passphrase". Reject it explicitly instead of letting the blank value fall
        # through to the verifier and surface as a generic decryption failure.
        raise ValueError("A passphrase is required.")
    if not verify_passphrase(passphrase):
        # A wrong current passphrase on the disable path — opt-in HA notification
        # (best-effort, no secret in the payload) before surfacing the 400.
        _notify_security_event("wrong_passphrase")
        raise ValueError("Wrong passphrase.")

    import sqlcipher3  # pyright: ignore[reportMissingImports]  -- optional 'encryption' extra

    db_path = str(settings.database_file)
    plain_tmp = db_path + ".plaintmp"
    Path(plain_tmp).unlink(missing_ok=True)

    engine = dbsession.get_engine()
    if engine is not None:
        engine.dispose()

    con = sqlcipher3.connect(db_path)
    try:
        con.execute(_key_pragma(passphrase))
        con.execute(f"ATTACH DATABASE '{plain_tmp}' AS plaintext KEY ''")
        con.execute("SELECT sqlcipher_export('plaintext')")
        con.execute("DETACH DATABASE plaintext")
    finally:
        con.close()

    os.replace(plain_tmp, db_path)
    _remove_wal_shm()
    _delete_marker()
    clear_stored_key()  # decrypting removes the auto-unlock key
    dbsession.configure(None)
    logger.info("Database encryption disabled.")


def unlock(passphrase: str) -> bool:
    """Verify the passphrase and bring the database online.

    This is the prompt-mode path: the DB was locked at startup (encrypted, no
    stored key), so migrations were skipped then and run HERE now that the
    unlocked SQLCipher engine exists."""
    if not verify_passphrase(passphrase):
        return False
    dbsession.configure(passphrase)
    _migrate_after_unlock()
    _ensure_schema_and_seed()
    logger.info("Database unlocked.")
    return True


def _migrate_after_unlock() -> None:
    """Bring the just-unlocked encrypted DB to head. On failure re-lock rather
    than serve a possibly-inconsistent schema, and surface the error."""
    from app.db.migrations_runner import run_migrations

    try:
        run_migrations()
    except Exception:
        logger.error(
            "Migration after unlock failed; re-locking to avoid serving inconsistent data.",
            exc_info=True,
        )
        dbsession.lock()
        raise


def _ensure_schema_and_seed() -> None:
    from app.db.base import Base
    from app.services.category_service import ensure_default_categories

    Base.metadata.create_all(bind=dbsession.require_engine())
    with dbsession.SessionLocal() as db:
        ensure_default_categories(db)


def status() -> dict:
    marker = read_marker()
    return {
        "encryption_available": sqlcipher_available(),
        "encryption_enabled": bool(marker and marker.get("enabled")),
        "unlock_mode": marker.get("unlock_mode") if marker else None,
        "locked": dbsession.is_locked(),
        # True when an auto-unlock key is available (either the HAFI_DB_KEY env /
        # add-on option, or the saved key file written by "stored" mode on a
        # standalone install), i.e. the database can unlock unattended. Lets the UI
        # flag a "stored" setup whose key isn't wired (would lock on restart).
        "stored_key_present": bool(resolve_stored_key()),
        # Where that key comes from ("env" | "file" | None) so the UI can tailor
        # its copy. Not security-sensitive: it never exposes the key itself.
        "stored_key_source": stored_key_source(),
        "failed_unlocks": failed_unlock_summary(),
    }


# --- Failed-unlock tracking (backlog #130) ----------------------------------
#
# Unlock attempts happen while the database is locked (encrypted, not yet
# opened), so the app DB is unavailable — we log them to a small JSON file next
# to the database instead.

_MAX_STORED_UNLOCK_EVENTS = 50

# Serialises read-modify-write of the events file so concurrent failed-unlock records
# can't lose updates — the brute-force counter it feeds must never *under*count (SR-7).
# Sync routes run in a threadpool, so two unlock attempts really can race here.
_events_lock = threading.Lock()


def _events_path() -> Path:
    return settings.database_file.parent / "security_events.json"


def _read_events() -> dict:
    path = _events_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - corrupt file
        return {}


def _write_events(data: dict) -> None:
    # Atomic: write a temp file in the same directory then os.replace, so a crash or a
    # concurrent reader never sees a truncated/half-written file (SR-7). mkstemp gives a
    # safe, unique temp path (no hand-built filename).
    path = _events_path()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".security_events_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data))
        os.replace(tmp, path)
    except Exception:  # pragma: no cover - clean up the temp file on any write failure
        Path(tmp).unlink(missing_ok=True)
        raise


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _notify_security_event(event_type: str, recent_failures: int | None = None) -> None:
    """Publish a security event to MQTT (opt-in, best-effort). Lazy import avoids an
    import cycle and keeps the auth path independent of MQTT being importable."""
    from app.services import mqtt_service  # lazy: avoid import cycle

    mqtt_service.publish_security_event_safe(event_type, recent_failures)


def record_failed_unlock() -> int:
    """Record a failed unlock; returns the number of failures in the recent window."""
    with _events_lock:  # the whole read-modify-write is serialised (SR-7)
        events = _read_events()
        attempts = events.get("failed_unlocks", [])
        attempts.append(_now().isoformat())
        events["failed_unlocks"] = attempts[-_MAX_STORED_UNLOCK_EVENTS:]
        _write_events(events)
    recent = failed_unlock_summary()["recent"]
    logger.warning("Failed database unlock attempt (%d recent).", recent)
    # Opt-in HA notification. No secret is passed — only the type + recent count.
    _notify_security_event("failed_unlock", recent)
    return recent


def record_successful_unlock() -> None:
    """Clear the failed-attempt streak and note the successful unlock time."""
    with _events_lock:
        events = _read_events()
        events["failed_unlocks"] = []
        events["last_unlock_at"] = _now().isoformat()
        _write_events(events)


def prune_failed_unlocks(older_than_days: int) -> int:
    """Drop recorded failed-unlock timestamps older than the cutoff (retention,
    backlog #78). Returns the number removed. Unparseable rows are dropped too."""
    cutoff = _now() - timedelta(days=older_than_days)
    with _events_lock:
        events = _read_events()
        stored = events.get("failed_unlocks", [])
        kept: list[str] = []
        for value in stored:
            try:
                if datetime.fromisoformat(value) >= cutoff:
                    kept.append(value)
            except (ValueError, TypeError):  # pragma: no cover - bad row → drop it
                continue
        removed = len(stored) - len(kept)
        if removed:
            events["failed_unlocks"] = kept
            _write_events(events)
    return removed


def count_failed_unlocks_older_than(older_than_days: int) -> int:
    """How many recorded failed-unlock timestamps are older than the cutoff (used
    by the retention preview). Unparseable rows count as prunable."""
    events = _read_events()
    cutoff = _now() - timedelta(days=older_than_days)
    n = 0
    for value in events.get("failed_unlocks", []):
        try:
            if datetime.fromisoformat(value) < cutoff:
                n += 1
        except (ValueError, TypeError):  # pragma: no cover - bad row
            n += 1
    return n


def failed_unlock_summary(window_minutes: int = 60) -> dict:
    events = _read_events()
    cutoff = _now() - timedelta(minutes=window_minutes)
    parsed: list[datetime] = []
    for value in events.get("failed_unlocks", []):
        try:
            parsed.append(datetime.fromisoformat(value))
        except (ValueError, TypeError):  # pragma: no cover - bad row
            continue
    recent = [t for t in parsed if t >= cutoff]
    return {
        "recent": len(recent),
        "total_stored": len(parsed),
        "last_attempt_at": max(parsed).isoformat() if parsed else None,
    }
