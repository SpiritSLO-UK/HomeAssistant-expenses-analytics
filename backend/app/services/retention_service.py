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

from app.db.session import dml_rowcount
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


def _as_naive_utc(dt: datetime) -> datetime:
    """Normalise to a naive-UTC datetime so it compares cleanly with ``_cutoff``
    (which is naive). An aware value is converted to UTC then stripped; a naive one
    is assumed already-UTC and returned unchanged."""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _receipt_age(r: Receipt) -> datetime:
    if r.receipt_date is not None:
        return datetime(r.receipt_date.year, r.receipt_date.month, r.receipt_date.day)
    return _as_naive_utc(r.created_at or _now())


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

def _archive(db: Session, dtype: str, days: int, counter: dict) -> None:
    """Archive one type, recording the applied count in ``counter['archived']``.
    Bulk-DML types set the count only after their commit; receipts self-commit per
    row, so the running count is incremented as each lands — a mid-loop abort still
    reports the rows already archived."""
    cutoff = _cutoff(days)
    if dtype == "transactions":
        res = db.execute(
            update(Transaction)
            .where(Transaction.transaction_date < cutoff.date(), Transaction.archived_at.is_(None))
            .values(archived_at=_now())
        )
        db.commit()
        counter["archived"] = dml_rowcount(res) or 0
    elif dtype == "ai_requests":
        res = db.execute(
            update(AIRequest)
            .where(AIRequest.created_at < cutoff, AIRequest.archived_at.is_(None))
            .values(archived_at=_now())
        )
        db.commit()
        counter["archived"] = dml_rowcount(res) or 0
    elif dtype == "audit_logs":
        res = db.execute(
            update(AuditLog)
            .where(AuditLog.created_at < cutoff, AuditLog.archived_at.is_(None))
            .values(archived_at=_now())
        )
        db.commit()
        counter["archived"] = dml_rowcount(res) or 0
    elif dtype == "receipts":
        rows = db.scalars(select(Receipt).where(Receipt.archived_at.is_(None))).all()
        for r in rows:
            if _receipt_age(r) < cutoff:
                receipt_service.drop_original(db, r)  # unlinks file, sets archived_at, commits
                counter["archived"] += 1


def _audit_purge(db: Session, dtype: str, actor: str, count: int) -> None:
    audit_service.record(db, actor=actor, action=f"purge_{dtype}",
                         entity_type="retention", details={"count": count})


def _purge_dml(db: Session, stmt, dtype: str, actor: str, counter: dict) -> None:
    """Bulk-DML purge: the audit row joins the DELETE's transaction and both commit
    together — either both land or neither. The count is reported only once that
    commit succeeds, so a failed purge reports 0 rather than a phantom deletion."""
    res = db.execute(stmt)
    n = dml_rowcount(res) or 0
    if n:
        _audit_purge(db, dtype, actor, n)
    db.commit()
    counter["purged"] = n


def _purge_receipts(db: Session, cutoff: datetime, actor: str, counter: dict) -> None:
    """Receipts self-commit per row, so true delete+audit atomicity isn't possible.
    Instead keep the running count accurate and audit whatever actually landed —
    including a partial count if the loop aborts partway."""
    rows = db.scalars(select(Receipt)).all()
    try:
        for r in rows:
            if _receipt_age(r) < cutoff:
                receipt_service.delete(db, r)  # row + file + review items, commits
                counter["purged"] += 1
    finally:
        if counter["purged"]:
            _audit_purge(db, "receipts", actor, counter["purged"])
            db.commit()


def _purge_failed_unlock(db: Session, days: int, actor: str, counter: dict) -> None:
    """The failed-unlock record is a flat JSON file pruned outside the DB; audit and
    commit the count once it's gone."""
    n = security_service.prune_failed_unlocks(days)
    counter["purged"] = n
    if n:
        _audit_purge(db, "failed_unlock", actor, n)
        db.commit()


def _purge(db: Session, dtype: str, days: int, actor: str, counter: dict) -> None:
    """Purge one type and audit it durably in the same step, recording the applied
    count in ``counter['purged']``. See the per-branch helpers for the exact
    delete/audit/commit ordering each type can guarantee."""
    cutoff = _cutoff(days)
    if dtype == "transactions":
        # FK cascades (PRAGMA foreign_keys=ON) drop splits + receipt matches and
        # null child_allocations / ai_requests references.
        _purge_dml(db, delete(Transaction).where(Transaction.transaction_date < cutoff.date()),
                   dtype, actor, counter)
    elif dtype == "ai_requests":
        _purge_dml(db, delete(AIRequest).where(AIRequest.created_at < cutoff), dtype, actor, counter)
    elif dtype == "audit_logs":
        _purge_dml(db, delete(AuditLog).where(AuditLog.created_at < cutoff), dtype, actor, counter)
    elif dtype == "receipts":
        _purge_receipts(db, cutoff, actor, counter)
    elif dtype == "failed_unlock":
        _purge_failed_unlock(db, days, actor, counter)


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
            _archive(db, dtype, days, counts[dtype])
        except Exception:  # pragma: no cover - isolate one type's failure
            db.rollback()
            logger.exception("Retention archive failed for %s", dtype)


def _take_safety_backup(db: Session) -> bool:
    try:
        backup_service.create_safety_backup("retention")
        backup_service.prune_backups(db)
        return True
    except Exception:  # pragma: no cover - never purge without a backup
        logger.exception("Safety backup before purge failed — skipping purge this run.")
        return False


def _run_purge_stage(db: Session, policy: dict, actor: str, purge_mode: str, counts: dict) -> bool:
    """Purge stage — permanent, so take a safety backup first if it will delete.
    Each type is purged *and* audited durably before the next (see ``_purge``), so a
    successful purge is always audited and a mid-run failure never leaves an
    un-audited deletion. Updates ``counts`` in place; returns whether a backup was
    taken. One type's failure is logged and isolated; no purge runs without a backup."""
    purge_types = _purge_types(policy, purge_mode)
    will_delete = any(_purge_due(db, t, policy[t]["purge_after_days"]) > 0 for t in purge_types)
    if not will_delete:
        return False
    if not _take_safety_backup(db):
        return False
    for dtype in purge_types:
        try:
            _purge(db, dtype, policy[dtype]["purge_after_days"], actor, counts[dtype])
        except Exception:  # pragma: no cover - isolate one type's failure
            db.rollback()
            logger.exception("Retention purge failed for %s", dtype)
    return True


def _audit_archive_actions(db: Session, actor: str, counts: dict) -> None:
    """Audit every non-zero archive action. Purges are audited as they run, in the
    same transaction as (or immediately after) each delete — see ``_purge``."""
    for dtype, c in counts.items():
        if c["archived"]:
            audit_service.record(db, actor=actor, action=f"archive_{dtype}",
                                 entity_type="retention", details={"count": c["archived"]})


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
    #    Each purge is audited durably as it runs (same tx as the delete for bulk
    #    DML; immediately after for per-row receipt/failed-unlock deletes).
    backup_taken = _run_purge_stage(db, policy, actor, purge_mode, counts)

    # 3. Audit every non-zero archive action (purges already audited above).
    _audit_archive_actions(db, actor, counts)
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
