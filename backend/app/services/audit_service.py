"""Audit logging (spec §12.20, §28.5).

A thin writer over the ``audit_logs`` table for security-relevant and otherwise
important actions (user management now; failed-unlock and sensitive actions are
wired in a later sub-stage). Best-effort and never raises into the caller — an
audit write must never break the action it records.
"""

from __future__ import annotations

import json

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models import AuditLog

logger = get_logger(__name__)


def record(
    db: Session,
    *,
    action: str,
    actor: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: dict | None = None,
    household_id: int | None = None,
) -> None:
    """Append an audit entry. Flushes but does not commit (joins the caller's tx)."""
    try:
        db.add(
            AuditLog(
                household_id=household_id,
                actor=actor or "system",
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details_json=json.dumps(details, default=str) if details else None,
            )
        )
        db.flush()
    except Exception:  # pragma: no cover - audit must never break the action
        logger.exception("Failed to write audit log for action=%s", action)


def record_api_action(
    db: Session, *, actor: str | None, method: str, path: str, status: int
) -> None:
    """Generic per-request audit entry for any mutating API call (backlog: "track
    all user + API actions in logs"). Complements the richer, action-specific
    records elsewhere; intentionally logs no request body (privacy). Action is the
    fixed label ``api_call`` so the Logs action-filter stays small; method/path/
    status live in the details."""
    record(
        db,
        action="api_call",
        actor=actor,
        entity_type="api",
        details={"method": method, "path": path, "status": status},
    )


# Single action name for important user decisions (AI/cloud/privacy posture), so
# the Logs viewer can surface them all together via the action filter. The human-
# readable text lives in details["summary"]; structured fields sit alongside it.
DECISION_ACTION = "decision"


def record_decision(
    db: Session, *, summary: str, actor: str | None = None, details: dict | None = None
) -> None:
    """Record an important consent/privacy decision the user took (e.g. switching AI
    to a cloud mode, turning OCR on/off, sending an image to the AI). Grouped under
    the ``decision`` action so the activity log can show a "Decisions" view."""
    payload: dict = {"summary": summary}
    if details:
        payload.update(details)
    record(db, action=DECISION_ACTION, actor=actor, entity_type="decision", details=payload)


def record_image_sent(db: Session, *, actor: str | None, kind: str, size: int) -> None:
    """Record sending an image (statement/receipt) to the AI as a decision — an
    image can't be redacted, so the send is itself a privacy choice. Notes whether
    it went to the cloud or stayed local."""
    from app.services import ai_service  # lazy: avoid an import cycle at module load

    ai = ai_service.status(db)
    where = "cloud AI" if ai.get("is_cloud") else "the local AI"
    record_decision(
        db,
        actor=actor,
        summary=f"Sent a {kind} image to {where} for extraction (images can't be redacted)",
        details={"kind": kind, "bytes": size, "privacy_mode": ai.get("privacy_mode")},
    )


def recent(
    db: Session,
    *,
    limit: int = 100,
    action_prefix: str | None = None,
    include_archived: bool = False,
) -> list[AuditLog]:
    """Most-recent audit entries, newest first. Optional action-prefix filter.

    Archived entries (aged out by the retention engine, backlog #78) are hidden
    unless ``include_archived`` is set."""
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    if action_prefix:
        stmt = stmt.where(AuditLog.action.like(f"{action_prefix}%"))
    if not include_archived:
        stmt = stmt.where(AuditLog.archived_at.is_(None))
    return list(db.scalars(stmt.limit(limit)).all())


def distinct_actions(db: Session) -> list[str]:
    """The set of action names that appear in the log (for a filter dropdown)."""
    stmt = select(distinct(AuditLog.action)).order_by(AuditLog.action)
    return list(db.scalars(stmt).all())


def to_dict(entry: AuditLog) -> dict:
    """Serialise an audit row for the API, parsing the JSON detail blob."""
    details: dict | None = None
    if entry.details_json:
        try:
            details = json.loads(entry.details_json)
        except (ValueError, TypeError):  # pragma: no cover - tolerate legacy/bad rows
            details = {"raw": entry.details_json}
    return {
        "id": entry.id,
        "created_at": entry.created_at,
        "actor": entry.actor,
        "action": entry.action,
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "details": details,
    }
