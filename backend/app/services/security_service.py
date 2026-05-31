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
    try:
        import sqlcipher3  # noqa: F401
        return True
    except Exception:
        return False


def _escape(passphrase: str) -> str:
    return passphrase.replace("'", "''")


def _remove_wal_shm() -> None:
    db = str(settings.database_file)
    for suffix in ("-wal", "-shm"):
        Path(db + suffix).unlink(missing_ok=True)


def verify_passphrase(passphrase: str) -> bool:
    """True if the passphrase opens the encrypted database."""
    import sqlcipher3

    con = sqlcipher3.connect(str(settings.database_file))
    try:
        con.execute(f"PRAGMA key='{_escape(passphrase)}'")
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

    import sqlcipher3

    db_path = str(settings.database_file)
    enc_tmp = db_path + ".enctmp"
    Path(enc_tmp).unlink(missing_ok=True)

    # Release pooled connections so the file isn't locked.
    if dbsession.get_engine() is not None:
        dbsession.get_engine().dispose()

    con = sqlcipher3.connect(db_path)  # opened as plaintext
    try:
        safe = _escape(passphrase)
        con.execute(f"ATTACH DATABASE '{enc_tmp}' AS enc KEY '{safe}'")
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

    import sqlcipher3

    db_path = str(settings.database_file)
    plain_tmp = db_path + ".plaintmp"
    Path(plain_tmp).unlink(missing_ok=True)

    if dbsession.get_engine() is not None:
        dbsession.get_engine().dispose()

    con = sqlcipher3.connect(db_path)
    try:
        con.execute(f"PRAGMA key='{_escape(passphrase)}'")
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

    Base.metadata.create_all(bind=dbsession.get_engine())
    with dbsession.SessionLocal() as db:
        ensure_default_categories(db)


def status() -> dict:
    marker = read_marker()
    return {
        "encryption_available": sqlcipher_available(),
        "encryption_enabled": bool(marker and marker.get("enabled")),
        "unlock_mode": marker.get("unlock_mode") if marker else None,
        "locked": dbsession.is_locked(),
    }
