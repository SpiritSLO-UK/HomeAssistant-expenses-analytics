"""System statistics for the Settings page.

Headline numbers an owner/manager wants at a glance: how big the database is on
disk and how much AI processing has happened. The AI/processing tallies are
reused from :mod:`dashboard_service` so there is a single source of truth.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Receipt, Statement, Transaction
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
    """Storage + processing/AI tallies for the Settings 'Storage & statistics' card.

    This card surfaces only the storage size, three import counts and the AI
    tallies, so we compute exactly those. We reuse ``dashboard_service``'s count
    and AI-aggregation helpers (single source of truth) rather than running the
    dashboard's full ``processing_stats`` — which additionally computes the
    receipt OCR breakdown, per-task tally and pending counts that this card
    never shows.
    """
    ai = dashboard_service._ai_stats(db)
    return {
        "database_bytes": database_bytes(),
        "transactions": dashboard_service._count(db, Transaction),
        "statements": dashboard_service._count(db, Statement, Statement.status == "imported"),
        "receipts": dashboard_service._count(db, Receipt),
        "ai_total": ai["total"],
        "ai_cloud": ai["cloud"],
        "ai_local": ai["total"] - ai["cloud"],
        "ai_completed": ai["completed"],
        "ai_failed": ai["failed"],
        "ai_avg_seconds": ai["avg_seconds"],
    }
