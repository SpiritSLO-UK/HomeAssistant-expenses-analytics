"""Health endpoint (spec §24.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.config import settings
from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Return service status, version, and a quick database connectivity check."""
    try:
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
