"""Data retention & expiration (spec §28; backlog #78).

A two-stage lifecycle per data type: optionally **archive after X days** (reversible —
hide the row / drop a receipt's original file, keep the data), then optionally
**purge after Y days** (permanent delete). Everything is **off by default**: nothing
is touched until the owner sets a number.

- Archive is reversible and runs automatically on the startup sweep.
- Purge is permanent. It only runs on an owner-confirmed manual ``run(..., purge_mode="all")``,
  or automatically on the startup sweep for types whose policy sets ``auto_purge=True``.
- **Before any purge that would delete rows, a timestamped safety backup is taken**
  (``backup_service.create_safety_backup``) and the backup history is trimmed.

``transactions`` are intentionally NOT a data type here — they get their own
``archived_at`` column and aggregate-exclusion in a follow-up PR (#12); only logs,
receipt files and the failed-unlock record are handled now.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models import AIRequest, AuditLog, Receipt, Transaction
from app.services import (
    audit_service,
    backup_service,
    receipt_service,
    security_service,
    settings_service,
)

logger = get_logger(__name__)

# Order matters only for display. ``failed_unlock`` is purge-only (a flat JSON file,
# nothing to "archive"); the rest support both stages.
DATA_TYPES = ("transactions", "ai_requests", "audit_logs", "receipts", "failed_unlock")
ARCHIVABLE = ("transactions", "ai_requests", "audit_logs", "receipts")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _cutoff(days: int) -> datetime:
    return _now() - timedelta(days=days)


# --- Policy storage -------------------------------------------------------

def _default_type_policy(dtype: str) -> dict:
    pol: dict = {"purge_after_days": None, "auto_purge": False}
    if dtype in ARCHIVABLE:
        pol["archive_after_days"] = None
    return pol


def default_policy() -> dict:
    return {t: _default_type_policy(t) for t in DATA_TYPES}


def get_policy(db: Session) -> dict:
    """The full retention policy: stored values merged over the all-off defaults."""
    policy = default_policy()
    raw = settings_service.get(db, settings_service.RETENTION_POLICY)
    if raw:
        try:
            stored = json.loads(raw)
        except (ValueError, TypeError):  # pragma: no cover - corrupt value
            stored = {}
        for dtype, values in stored.items():
            if dtype in policy and isinstance(values, dict):
                policy[dtype].update({k: v for k, v in values.items() if k in policy[dtype]})
    return policy


def save_policy(db: Session, policy: dict) -> None:
    settings_service.set_value(db, settings_service.RETENTION_POLICY, json.dumps(policy))


def _validate_type_policy(dtype: str, values: dict, target: dict) -> None:
    """Validate one type's submitted ``values`` and apply the valid fields to its
    ``target`` defaults in place. Raises ``ValueError`` exactly as the caller did."""
    for field in ("archive_after_days", "purge_after_days"):
        if field not in target or field not in values or values[field] is None:
            continue
        n = values[field]
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ValueError(f"{dtype}.{field} must be a non-negative whole number of days.")
        target[field] = n
    if "auto_purge" in values:
        if not isinstance(values["auto_purge"], bool):
            raise ValueError(f"{dtype}.auto_purge must be true or false.")
        target["auto_purge"] = values["auto_purge"]
    archive, purge = target.get("archive_after_days"), target.get("purge_after_days")
    if archive is not None and purge is not None and archive > purge:
        raise ValueError(
            f"{dtype}: archive-after ({archive}d) must not be later than purge-after ({purge}d)."
        )


def validate_policy(raw: object) -> dict:
    """Validate a (partial) policy and return the full normalised policy.

    Raises ``ValueError`` on an unknown type, a negative/non-integer day count, a
    non-bool ``auto_purge``, or ``archive_after_days > purge_after_days``.
    """
    if not isinstance(raw, dict):
        raise ValueError("Retention policy must be an object keyed by data type.")
    policy = default_policy()
    for dtype, values in raw.items():
        if dtype not in policy:
            raise ValueError(f"Unknown retention data type: {dtype!r}")
        if not isinstance(values, dict):
            raise ValueError(f"Policy for {dtype!r} must be an object.")
        _validate_type_policy(dtype, values, policy[dtype])
    return policy


# --- Counts (used by preview — never writes) ------------------------------

def _count(db: Session, model, *conds) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*conds)) or 0


def _receipt_age(r: Receipt) -> datetime:
    if r.receipt_date is not None:
        return datetime(r.receipt_date.year, r.receipt_date.month, r.receipt_date.day)
    return r.created_at or _now()


def _archive_due(db: Session, dtype: str, days: int) -> int:
    cutoff = _cutoff(days)
    if dtype == "transactions":
        return _count(db, Transaction, Transaction.transaction_date < cutoff.date(),
                      Transaction.archived_at.is_(None))
    if dtype == "ai_requests":
        return _count(db, AIRequest, AIRequest.created_at < cutoff, AIRequest.archived_at.is_(None))
    if dtype == "audit_logs":
        return _count(db, AuditLog, AuditLog.created_at < cutoff, AuditLog.archived_at.is_(None))
    if dtype == "receipts":
        rows = db.scalars(select(Receipt).where(Receipt.archived_at.is_(None))).all()
        return sum(1 for r in rows if _receipt_age(r) < cutoff)
    return 0


def _purge_due(db: Session, dtype: str, days: int) -> int:
    cutoff = _cutoff(days)
    if dtype == "transactions":
        return _count(db, Transaction, Transaction.transaction_date < cutoff.date())
    if dtype == "ai_requests":
        return _count(db, AIRequest, AIRequest.created_at < cutoff)
    if dtype == "audit_logs":
        return _count(db, AuditLog, AuditLog.created_at < cutoff)
    if dtype == "receipts":
        rows = db.scalars(select(Receipt)).all()
        return sum(1 for r in rows if _receipt_age(r) < cutoff)
    if dtype == "failed_unlock":
        return security_service.count_failed_unlocks_older_than(days)
    return 0


def preview(db: Session) -> dict:
    """The authoritative removal plan — what archive/purge *would* affect now.

    Per type ``{archive_due, purge_due, auto_purge}`` plus a top-level
    ``pending_purge`` total: purge-due items where ``auto_purge`` is off, i.e. what
    is waiting for the owner to confirm. No writes.
    """
    policy = get_policy(db)
    out: dict = {}
    pending = 0
    for dtype in DATA_TYPES:
        pol = policy[dtype]
        archive_days = pol.get("archive_after_days")
        purge_days = pol.get("purge_after_days")
        archive_due = _archive_due(db, dtype, archive_days) if archive_days is not None else 0
        purge_due = _purge_due(db, dtype, purge_days) if purge_days is not None else 0
        out[dtype] = {
            "archive_due": archive_due,
            "purge_due": purge_due,
            "auto_purge": bool(pol.get("auto_purge")),
        }
        if purge_due and not pol.get("auto_purge"):
            pending += purge_due
    out["pending_purge"] = pending
    return out


# --- Mutating stages ------------------------------------------------------

def _archive(db: Session, dtype: str, days: int) -> int:
    cutoff = _cutoff(days)
    if dtype == "transactions":
        res = db.execute(
            update(Transaction)
            .where(Transaction.transaction_date < cutoff.date(), Transaction.archived_at.is_(None))
            .values(archived_at=_now())
        )
        db.commit()
        return res.rowcount or 0
    if dtype == "ai_requests":
        res = db.execute(
            update(AIRequest)
            .where(AIRequest.created_at < cutoff, AIRequest.archived_at.is_(None))
            .values(archived_at=_now())
        )
        db.commit()
        return res.rowcount or 0
    if dtype == "audit_logs":
        res = db.execute(
            update(AuditLog)
            .where(AuditLog.created_at < cutoff, AuditLog.archived_at.is_(None))
            .values(archived_at=_now())
        )
        db.commit()
        return res.rowcount or 0
    if dtype == "receipts":
        rows = db.scalars(select(Receipt).where(Receipt.archived_at.is_(None))).all()
        n = 0
        for r in rows:
            if _receipt_age(r) < cutoff:
                receipt_service.drop_original(db, r)  # unlinks file, sets archived_at, commits
                n += 1
        return n
    return 0


def _purge(db: Session, dtype: str, days: int) -> int:
    cutoff = _cutoff(days)
    if dtype == "transactions":
        # FK cascades (PRAGMA foreign_keys=ON) drop splits + receipt matches and
        # null child_allocations / ai_requests references.
        res = db.execute(delete(Transaction).where(Transaction.transaction_date < cutoff.date()))
        db.commit()
        return res.rowcount or 0
    if dtype == "ai_requests":
        res = db.execute(delete(AIRequest).where(AIRequest.created_at < cutoff))
        db.commit()
        return res.rowcount or 0
    if dtype == "audit_logs":
        res = db.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
        db.commit()
        return res.rowcount or 0
    if dtype == "receipts":
        rows = db.scalars(select(Receipt)).all()
        n = 0
        for r in rows:
            if _receipt_age(r) < cutoff:
                receipt_service.delete(db, r)  # row + file + review items, commits
                n += 1
        return n
    if dtype == "failed_unlock":
        return security_service.prune_failed_unlocks(days)
    return 0


def _purge_types(policy: dict, purge_mode: str) -> list[str]:
    """Which types should purge this run: every type with a purge window when the
    owner confirmed a manual run, or only ``auto_purge`` types on the startup sweep."""
    out = []
    for dtype in DATA_TYPES:
        pol = policy[dtype]
        if pol.get("purge_after_days") is None:
            continue
        if purge_mode == "all" or pol.get("auto_purge"):
            out.append(dtype)
    return out


def any_enabled(policy: dict) -> bool:
    return any(
        policy[t].get("archive_after_days") is not None or policy[t].get("purge_after_days") is not None
        for t in DATA_TYPES
    )


def _run_archive_stage(db: Session, policy: dict, counts: dict) -> None:
    """Archive stage — reversible, no backup needed. Updates ``counts`` in place;
    one type's failure is logged and isolated."""
    for dtype in ARCHIVABLE:
        days = policy[dtype].get("archive_after_days")
        if days is None:
            continue
        try:
            counts[dtype]["archived"] = _archive(db, dtype, days)
        except Exception:  # pragma: no cover - isolate one type's failure
            logger.exception("Retention archive failed for %s", dtype)


def _run_purge_stage(db: Session, policy: dict, purge_mode: str, counts: dict) -> bool:
    """Purge stage — permanent, so take a safety backup first if it will delete.
    Updates ``counts`` in place; returns whether a backup was taken. One type's
    failure is logged and isolated; no purge runs if the backup fails."""
    purge_types = _purge_types(policy, purge_mode)
    will_delete = any(_purge_due(db, t, policy[t]["purge_after_days"]) > 0 for t in purge_types)
    backup_taken = False
    if will_delete:
        try:
            backup_service.create_safety_backup("retention")
            backup_service.prune_backups(db)
            backup_taken = True
        except Exception:  # pragma: no cover - never purge without a backup
            logger.exception("Safety backup before purge failed — skipping purge this run.")

    if will_delete and backup_taken:
        for dtype in purge_types:
            try:
                counts[dtype]["purged"] = _purge(db, dtype, policy[dtype]["purge_after_days"])
            except Exception:  # pragma: no cover - isolate one type's failure
                logger.exception("Retention purge failed for %s", dtype)
    return backup_taken


def _audit_retention_actions(db: Session, actor: str, counts: dict) -> None:
    """Audit every non-zero archive/purge action."""
    for dtype, c in counts.items():
        if c["archived"]:
            audit_service.record(db, actor=actor, action=f"archive_{dtype}",
                                 entity_type="retention", details={"count": c["archived"]})
        if c["purged"]:
            audit_service.record(db, actor=actor, action=f"purge_{dtype}",
                                 entity_type="retention", details={"count": c["purged"]})


def run(db: Session, *, actor: str, purge_mode: str) -> dict:
    """Execute the policy. ``purge_mode='all'`` (owner-confirmed: purge every due
    type) or ``'auto'`` (startup: purge only ``auto_purge`` types). Archives always
    run for every due archivable type. Returns per-type counts + whether a backup
    was taken. Per-type failures are logged and isolated."""
    policy = get_policy(db)
    counts = {t: {"archived": 0, "purged": 0} for t in DATA_TYPES}

    # 1. Archive stage — reversible, no backup needed.
    _run_archive_stage(db, policy, counts)

    # 2. Purge stage — permanent, so take a safety backup first if it will delete.
    backup_taken = _run_purge_stage(db, policy, purge_mode, counts)

    # 3. Audit every non-zero action.
    _audit_retention_actions(db, actor, counts)
    db.commit()
    return {"counts": counts, "backup_taken": backup_taken}


def run_safe(db: Session) -> None:
    """Startup sweep: archive everything due + purge only ``auto_purge`` types.
    No-op unless a policy is set; never raises into the caller."""
    try:
        if not any_enabled(get_policy(db)):
            return
        result = run(db, actor="system", purge_mode="auto")
        logger.info("Retention startup sweep: %s", result["counts"])
    except Exception:  # pragma: no cover - startup must never fail on retention
        logger.exception("Retention startup sweep failed")
