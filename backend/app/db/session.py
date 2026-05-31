"""Database engine / session management.

SQLite for the MVP (spec §9.2, §10.1). PostgreSQL can be swapped in later by
changing the URL — the rest of the app uses the SQLAlchemy session only.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.logging import get_logger

logger = get_logger(__name__)


def _ensure_database_dir() -> None:
    """Create the parent directory for the SQLite file if needed."""
    db_file: Path = settings.database_file
    db_file.parent.mkdir(parents=True, exist_ok=True)


_ensure_database_dir()

engine: Engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},  # SQLite + FastAPI threads
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    """Enable foreign keys and WAL for SQLite (off by default in SQLite)."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
