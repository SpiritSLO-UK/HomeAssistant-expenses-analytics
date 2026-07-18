"""Database engine / session management.

SQLite for the MVP (spec §9.2, §10.1). The engine is normally plaintext. When
**at-rest encryption** is enabled (backlog #15b) the engine is built over
SQLCipher via a connection ``creator`` that issues ``PRAGMA key``. The engine is
rebindable so it can be (un)locked at runtime:

- encryption disabled  -> plaintext engine (default; unchanged behaviour)
- enabled + key known   -> SQLCipher engine
- enabled + no key yet  -> locked (no engine until the user unlocks)

The plaintext path is the common case and is identical to before, so non-
encrypted setups (and all of Windows dev, where SQLCipher has no wheel) are
unaffected.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any, cast

from sqlalchemy import create_engine, event
from sqlalchemy.engine import CursorResult, Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app.config import settings

# Imported for the side effect of registering the FTS `after_create` hook on
# Base.metadata, so the full-text search index is (re)built on every create_all
# (startup safety net, fresh installs and the test harness). Best-effort.
from app.db import search_index  # noqa: F401
from app.logging import get_logger

logger = get_logger(__name__)


class DatabaseLocked(Exception):
    """Raised when a DB session is requested while the database is locked."""


def _ensure_database_dir() -> None:
    settings.database_file.parent.mkdir(parents=True, exist_ok=True)


_ensure_database_dir()

# One sessionmaker, rebound to whichever engine is active via .configure().
SessionLocal = sessionmaker(autoflush=False, autocommit=False, future=True)

_engine: Engine | None = None
_locked: bool = False


def get_engine() -> Engine | None:
    return _engine


def require_engine() -> Engine:
    """The active engine, or raise when the database is locked/unconfigured. Use
    where a non-None engine is required (schema create, dispose)."""
    if _engine is None:
        raise DatabaseLocked("Database engine is not configured.")
    return _engine


def dml_rowcount(result: Any) -> int:
    """Affected-row count from an UPDATE/DELETE result. ``Session.execute`` is
    typed to return the read ``Result`` (which has no ``.rowcount``), but DML
    yields a ``CursorResult`` at runtime — this keeps that typed in one place."""
    return cast(CursorResult, result).rowcount


def is_locked() -> bool:
    return _locked


def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    # Wait up to 5s for a lock instead of erroring immediately with "database is
    # locked" under concurrent writers (CR-FEAT-3); NORMAL is the safe pairing
    # with WAL (durable on app crash, only loses on OS/power loss).
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


# SQLite connections are cheap file handles and WAL mode allows many concurrent
# readers, so keep a generous pool. The dashboard alone fans out ~10 parallel
# card queries per load, and a few open tabs / a burst of navigation easily
# exceed the SQLAlchemy default of 5 + 10 overflow = 15, which then surfaces as a
# `QueuePool limit ... reached, connection timed out` error (HTTP 500) rather than
# just being slower. 20 + 30 = 50 gives ample headroom at negligible cost. Write
# contention is still serialised safely by WAL + `busy_timeout` (see the pragmas).
_POOL_KW = {"pool_size": 20, "max_overflow": 30, "pool_timeout": 30}


def _build_plaintext_engine() -> Engine:
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        future=True,
        **_POOL_KW,
    )
    event.listen(engine, "connect", _set_sqlite_pragma)
    return engine


def _build_encrypted_engine(passphrase: str) -> Engine:
    """Engine backed by SQLCipher. Requires the optional ``sqlcipher3`` driver
    (present on Linux/the add-on; not available as a Windows wheel)."""
    import sqlcipher3  # pyright: ignore[reportMissingImports]  -- optional 'encryption' extra (no Windows wheel)

    db_path = str(settings.database_file)
    safe = passphrase.replace("'", "''")  # escape for the PRAGMA literal

    def _creator():
        conn = sqlcipher3.connect(db_path, check_same_thread=False)
        conn.execute(f"PRAGMA key='{safe}'")
        conn.execute("PRAGMA foreign_keys=ON")
        # Match the plaintext engine's pragmas (this path previously diverged — no
        # WAL, no busy_timeout): WAL for concurrency, a 5s lock wait, NORMAL sync.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    # The "sqlite://" URL (no file) would default to SingletonThreadPool, which
    # rejects pool_size/max_overflow; force QueuePool so the encrypted path gets
    # the same generous pool as the plaintext engine. The creator opens the real
    # SQLCipher file per connection (check_same_thread=False), so pooling is safe.
    return create_engine(
        "sqlite://", creator=_creator, future=True, poolclass=QueuePool, **_POOL_KW
    )


def configure(passphrase: str | None = None) -> None:
    """(Re)build the active engine. ``passphrase=None`` -> plaintext."""
    global _engine, _locked
    if _engine is not None:
        _engine.dispose()
    _engine = _build_plaintext_engine() if passphrase is None else _build_encrypted_engine(passphrase)
    SessionLocal.configure(bind=_engine)
    _locked = False


def lock() -> None:
    """Drop the engine and refuse sessions until unlocked."""
    global _engine, _locked
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _locked = True


def init() -> None:
    """Decide the initial engine state at startup from the encryption marker."""
    from app.services import security_service

    state = security_service.read_marker()
    if not state or not state.get("enabled"):
        configure(None)
        return
    if not security_service.sqlcipher_available():
        logger.error("DB encryption is enabled but the SQLCipher driver is unavailable — locking.")
        lock()
        return
    if settings.db_key:  # stored-key mode: unattended unlock
        if security_service.verify_passphrase(settings.db_key):
            configure(settings.db_key)
            logger.info("Database unlocked from stored key.")
            return
        logger.error(
            "Stored DB key (HAFI_DB_KEY) did not open the database — locking. Check it "
            "matches your encryption passphrase, or clear it and unlock via the UI."
        )
        lock()
        return
    logger.info("Database is encrypted and locked; awaiting unlock.")
    lock()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    if _locked or _engine is None:
        raise DatabaseLocked()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
