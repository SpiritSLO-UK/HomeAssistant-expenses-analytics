"""Audit logging (spec §12.20, §28.5).

A thin writer over the ``audit_logs`` table for security-relevant and otherwise
important actions (user management now; failed-unlock and sensitive actions are
wired in a later sub-stage). Best-effort and never raises into the caller — an
audit write must never break the action it records.
"""

from __future__ import annotations

import csv
import io
import json

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models import AuditLog

logger = get_logger(__name__)

# Upper bound on the serialised ``details`` blob stored per audit row. A large
# payload (e.g. a big bulk-operation body) would otherwise be persisted verbatim
# and bloat the DB. Beyond this the blob is replaced with a small marker so audit
# rows stay bounded; normal/small details round-trip unchanged (SR-E8).
MAX_DETAILS_BYTES = 4096


def _serialise_details(details: dict | None) -> str | None:
    """Serialise ``details`` to JSON, capping the result so a single audit row
    cannot grow without bound. Small payloads are returned verbatim; an oversized
    one is replaced with a compact ``{"_truncated": true, ...}`` marker that keeps
    the original size and a leading excerpt for debugging."""
    if not details:
        return None
    blob = json.dumps(details, default=str)
    if len(blob) <= MAX_DETAILS_BYTES:
        return blob
    # Keep a short excerpt (well under the cap) so the row still carries a hint of
    # the original content, plus the true size for context.
    excerpt = blob[:512]
    return json.dumps(
        {"_truncated": True, "_bytes": len(blob), "_excerpt": excerpt}
    )


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
                details_json=_serialise_details(details),
            )
        )
        db.flush()
    except Exception:  # pragma: no cover - audit must never break the action
        logger.exception("Failed to write audit log for action=%s", action)


def _household_for_actor(db: Session, actor: str | None) -> int | None:
    """Best-effort household scope for a per-request audit row (SR-E8). Prefers the
    acting user's household (matched by display name); falls back to the single
    household of this single-household MVP. Read-only and swallow-all: it must never
    raise into the audit write, and never *creates* a household as a side effect."""
    from app.models import Household, User  # local: keep the module import surface small

    try:
        if actor and actor != "system":
            hid = db.scalar(
                select(User.household_id).where(User.display_name == actor).limit(1)
            )
            if hid is not None:
                return hid
        return db.scalar(select(Household.id).order_by(Household.id).limit(1))
    except Exception:  # pragma: no cover - scoping is best-effort, never fatal
        return None


def record_api_action(
    db: Session,
    *,
    actor: str | None,
    method: str,
    path: str,
    status: int,
    household_id: int | None = None,
) -> None:
    """Generic per-request audit entry for any mutating API call (backlog: "track
    all user + API actions in logs"). Complements the richer, action-specific
    records elsewhere; intentionally logs no request body (privacy). Action is the
    fixed label ``api_call`` so the Logs action-filter stays small; method/path/
    status live in the details.

    The row is scoped to a household (SR-E8): callers may pass ``household_id``
    explicitly, otherwise it is derived best-effort from the acting user (or the
    single household of this MVP) so the entry is never written unscoped.

    Session note (CR-FEAT-4, by design): the audit middleware calls this on a
    *fresh* SQLAlchemy session and commits it separately from the request's own
    unit of work. That extra session + commit per mutation is accepted on purpose:
    the middleware runs outermost, *after* the route's transaction has already been
    committed or rolled back, so its session is closed by then and cannot be
    reused; a separate unit of work also guarantees the audit row survives even
    when the request transaction itself rolled back. Audit durability outweighs the
    small cost of one extra short-lived write."""
    if household_id is None:
        household_id = _household_for_actor(db, actor)
    record(
        db,
        action="api_call",
        actor=actor,
        entity_type="api",
        details={"method": method, "path": path, "status": status},
        household_id=household_id,
    )


# Important user decisions (AI/cloud/privacy posture) are namespaced as
# ``decision:<kind>`` so each *kind* is individually filterable in the Logs action
# dropdown, while the shared ``decision`` prefix still groups them all (the 🔑
# Decisions view uses prefix matching). The human-readable text lives in
# details["summary"]; structured fields sit alongside it.
DECISION_PREFIX = "decision"


def record_decision(
    db: Session, *, summary: str, kind: str = "other", actor: str | None = None,
    details: dict | None = None,
) -> None:
    """Record an important consent/privacy decision the user took (e.g. switching AI
    to a cloud mode, turning OCR on/off, sending an image to the AI). ``kind``
    namespaces the action as ``decision:<kind>`` so each type is its own filterable
    entry, while the ``decision`` prefix still groups them for the Decisions view."""
    payload: dict = {"summary": summary}
    if details:
        payload.update(details)
    record(db, action=f"{DECISION_PREFIX}:{kind}", actor=actor, entity_type="decision", details=payload)


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
        kind="image",
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
        # Escape LIKE metacharacters so an action prefix containing % or _ filters
        # literally rather than as a wildcard (SR-E8).
        escaped = action_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(AuditLog.action.like(f"{escaped}%", escape="\\"))
    if not include_archived:
        stmt = stmt.where(AuditLog.archived_at.is_(None))
    return list(db.scalars(stmt.limit(limit)).all())


def distinct_actions(db: Session) -> list[str]:
    """The set of action names that appear in the log (for a filter dropdown)."""
    stmt = select(distinct(AuditLog.action)).order_by(AuditLog.action)
    return list(db.scalars(stmt).all())


# Columns for the audit-log CSV export. Kept small and stable (matches the
# export_service CSV style): identity + a single already-capped details cell.
AUDIT_EXPORT_HEADERS = ["id", "timestamp", "actor", "action", "household", "details"]

# Upper bound on rows in a single export so a pathological log can't exhaust
# memory (mirrors export_service.MAX_EXPORT_ROWS).
MAX_EXPORT_ROWS = 100_000


def _household_names(db: Session) -> dict[int, str]:
    """id → name map for the households referenced by audit rows (built once so
    the export has no N+1). Best-effort: an unresolved id maps to an empty cell."""
    from app.models import Household  # local: keep the module import surface small

    return {h.id: h.name for h in db.scalars(select(Household)).all()}


def export_audit(
    db: Session,
    *,
    action_prefix: str | None = None,
    include_archived: bool = False,
    limit: int = MAX_EXPORT_ROWS,
) -> list[dict]:
    """Audit rows in CSV-ready form for the given filters, reusing the same
    ``recent()`` query path (household scope + action-prefix filter, archived
    exclusion) as the activity-log listing so "export" matches "what you see".

    The ``details`` cell is the row's already-serialised ``details_json`` — capped
    to ``MAX_DETAILS_BYTES`` on write (#319) — so a single cell can never grow
    without bound. ``csv.writer`` handles quoting/escaping of every cell."""
    entries = recent(
        db, limit=limit, action_prefix=action_prefix, include_archived=include_archived
    )
    households = _household_names(db)
    return [
        {
            "id": e.id,
            "timestamp": e.created_at.isoformat() if e.created_at else "",
            "actor": e.actor or "",
            "action": e.action,
            "household": households.get(e.household_id, ""),
            "details": e.details_json or "",
        }
        for e in entries
    ]


def audit_csv(rows: list[dict]) -> str:
    """Render export rows (from :func:`export_audit`) as a CSV document. Cells are
    escaped by ``csv.writer``; a details cell containing commas/quotes/newlines is
    quoted, not injected raw."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(AUDIT_EXPORT_HEADERS)
    for row in rows:
        writer.writerow([row[h] for h in AUDIT_EXPORT_HEADERS])
    return buf.getvalue()


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
