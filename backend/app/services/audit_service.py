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


def recent(db: Session, *, limit: int = 100, action_prefix: str | None = None) -> list[AuditLog]:
    """Most-recent audit entries, newest first. Optional action-prefix filter."""
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    if action_prefix:
        stmt = stmt.where(AuditLog.action.like(f"{action_prefix}%"))
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
