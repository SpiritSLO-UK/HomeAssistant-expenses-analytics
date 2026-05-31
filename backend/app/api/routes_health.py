"""Health endpoint (spec §24.1)."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.config import settings
from app.db import session as dbsession

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """Service status, version, and a quick database connectivity check.

    Stays informative even when the database is locked (encrypted, awaiting
    unlock) — it reports ``status: locked`` rather than erroring.
    """
    if dbsession.is_locked():
        return {
            "status": "locked",
            "version": __version__,
            "privacy_mode": settings.privacy_mode.value,
            "database": "locked",
        }

    db_ok = False
    try:
        with dbsession.SessionLocal() as db:
            db.execute(text("SELECT 1"))
            db_ok = True
    except Exception:  # pragma: no cover - defensive
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "privacy_mode": settings.privacy_mode.value,
        "database": "ok" if db_ok else "error",
    }
