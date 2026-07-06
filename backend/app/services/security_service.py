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


def verify_passphrase(passphrase: str) -> bool:
    """True if the passphrase opens the encrypted database."""
    import sqlcipher3  # pyright: ignore[reportMissingImports]  -- optional 'encryption' extra

    con = sqlcipher3.connect(str(settings.database_file))
    try:
        con.execute(_key_pragma(passphrase))
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return True
    except Exception:
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

    os.replace(enc_tmp, db_path)
    _remove_wal_shm()
    if not verify_passphrase(passphrase):  # pragma: no cover - sanity
        raise RuntimeError("Encryption verification failed; aborting.")
    _write_marker(unlock_mode)
    dbsession.configure(passphrase)
    logger.info("Database encryption enabled (unlock_mode=%s).", unlock_mode)


def disable_encryption(passphrase: str) -> None:
    """Decrypt the database back to plaintext."""
    if not read_marker():
        raise RuntimeError("Encryption is not enabled.")
    if not verify_passphrase(passphrase):
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
    dbsession.configure(None)
    logger.info("Database encryption disabled.")


def unlock(passphrase: str) -> bool:
    """Verify the passphrase and bring the database online."""
    if not verify_passphrase(passphrase):
        return False
    dbsession.configure(passphrase)
    _ensure_schema_and_seed()
    logger.info("Database unlocked.")
    return True


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
        # True when HAFI_DB_KEY is configured (add-on option / env), i.e. the
        # database can unlock unattended in "stored" mode. Lets the UI flag a
        # "stored" setup whose key isn't actually wired (would lock on restart).
        "stored_key_present": bool(settings.db_key),
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
