"""Activity-log (audit) viewer API (spec §28.5, §38; backlog #92).

Surfaces the ``audit_logs`` table — who did what, when — to the household owner.
Owner-only: the activity log can reveal user-management and other sensitive
actions, so it sits behind ``require_owner`` (read access; no step-up needed for
viewing). The AI-call audit lives in its own table and is exposed by
``/api/ai/requests``; the Logs page shows both side by side.

Low-level runtime/debug logs are written to stdout (the Home Assistant add-on
log panel), not the database, so they are not served here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.schemas.logs import AuditLogOut
from app.services import audit_service
from app.services.auth_service import require_owner

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("/activity", response_model=list[AuditLogOut])
def activity(
    limit: int = Query(default=100, ge=1, le=500),
    action: str | None = Query(default=None, description="Filter by action-name prefix"),
    db: Session = Depends(get_db),
    _owner: User = Depends(require_owner),
) -> list[dict]:
    entries = audit_service.recent(db, limit=limit, action_prefix=action)
    return [audit_service.to_dict(e) for e in entries]


@router.get("/actions", response_model=list[str])
def actions(
    db: Session = Depends(get_db), _owner: User = Depends(require_owner)
) -> list[str]:
    """Distinct action names present in the log (drives the filter dropdown)."""
    return audit_service.distinct_actions(db)
