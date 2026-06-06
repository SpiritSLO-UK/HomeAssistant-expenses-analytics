"""System statistics for the Settings page.

Headline numbers an owner/manager wants at a glance: how big the database is on
disk and how much AI processing has happened. The AI/processing tallies are
reused from :mod:`dashboard_service` so there is a single source of truth.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.services import dashboard_service


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:  # pragma: no cover - file may not exist (e.g. in-memory test DB)
        return 0


def database_bytes() -> int:
    """On-disk size of the SQLite database in bytes, including the ``-wal`` and
    ``-shm`` sidecar files (which can hold pages not yet checkpointed into the main
    file). Returns 0 when the file can't be read (e.g. an in-memory test DB)."""
    base = settings.database_file
    total = _file_size(base)
    for suffix in ("-wal", "-shm"):
        total += _file_size(Path(str(base) + suffix))
    return total


def system_stats(db: Session) -> dict:
    """Storage + processing/AI tallies for the Settings 'Storage & statistics' card."""
    proc = dashboard_service.processing_stats(db)
    return {
        "database_bytes": database_bytes(),
        "transactions": proc["transactions_imported"],
        "statements": proc["statements_imported"],
        "receipts": proc["receipts_total"],
        "ai_total": proc["ai_total"],
        "ai_cloud": proc["ai_cloud"],
        "ai_local": proc["ai_local"],
        "ai_completed": proc["ai_completed"],
        "ai_failed": proc["ai_failed"],
        "ai_avg_seconds": proc["ai_avg_seconds"],
    }
