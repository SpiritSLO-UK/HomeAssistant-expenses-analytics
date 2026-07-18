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

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.schemas.logs import AuditLogOut
from app.services import audit_service
from app.services.auth_service import require_owner

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("/activity", response_model=list[AuditLogOut])
def activity(
    db: Annotated[Session, Depends(get_db)],
    _owner: Annotated[User, Depends(require_owner)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    action: Annotated[str | None, Query(description="Filter by action-name prefix")] = None,
    include_archived: Annotated[bool, Query(description="Include archived (aged-out) entries")] = False,
) -> list[dict]:
    entries = audit_service.recent(db, limit=limit, action_prefix=action, include_archived=include_archived)
    return [audit_service.to_dict(e) for e in entries]


@router.get("/actions")
def actions(db: Annotated[Session, Depends(get_db)], _owner: Annotated[User, Depends(require_owner)]) -> list[str]:
    """Distinct action names present in the log (drives the filter dropdown)."""
    return audit_service.distinct_actions(db)


@router.get("/audit/export.csv")
def export_audit(
    db: Annotated[Session, Depends(get_db)],
    _owner: Annotated[User, Depends(require_owner)],
    action: Annotated[str | None, Query(description="Filter by action-name prefix")] = None,
    include_archived: Annotated[bool, Query(description="Include archived (aged-out) entries")] = False,
) -> Response:
    """Download the activity log as CSV. Owner-only, like the rest of this router —
    the audit log can reveal sensitive user-management actions. Honours the same
    action-prefix / archived filters as the ``/activity`` listing."""
    rows = audit_service.export_audit(db, action_prefix=action, include_archived=include_archived)
    filename = f"audit-log-{date.today().isoformat()}.csv"
    # utf-8-sig writes a BOM so Excel detects UTF-8 correctly (matches routes_export).
    return Response(
        content=audit_service.audit_csv(rows).encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
