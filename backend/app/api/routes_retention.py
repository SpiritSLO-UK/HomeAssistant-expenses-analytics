"""Data-retention API (spec §28; backlog #78, #147).

Owner-only throughout. Per your security call, **changing the policy and running a
purge require an MFA step-up** (a fresh code when the owner has MFA enabled — no
lockout for an owner who hasn't), via ``require_owner_step_up``. Reads
(``/policy``, ``/preview``) are owner-only but need no step-up.

The ``/preview`` is the authoritative "removal plan": the owner sees exactly what
would be archived/purged before anything is deleted. ``/run`` takes a safety
backup before any purge (inside ``retention_service.run``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.schemas.retention import RetentionPolicyUpdate
from app.services import audit_service, retention_service, settings_service
from app.services.auth_service import require_owner, require_owner_step_up

router = APIRouter(prefix="/retention", tags=["retention"])


def _policy_response(db: Session) -> dict:
    return {
        "policy": retention_service.get_policy(db),
        "data_types": list(retention_service.DATA_TYPES),
        "archivable": list(retention_service.ARCHIVABLE),
        "receipt_delete_after_processing": settings_service.get_receipt_delete_after_processing(db),
        "backup_trim": settings_service.get_backup_trim_policy(db),
    }


def _validate_backup_trim(raw: dict) -> dict:
    """Validate the backup-trim limits. Each must be a whole number ≥ 1 (a 0 would
    be degenerate — e.g. 'keep zero backups' or 'delete anything ≥ 0 days old')."""
    current: dict[str, int | None] = {"max_age_days": None, "max_total_mb": None, "min_keep": None}
    for field in current:
        if field not in raw or raw[field] is None:
            continue
        n = raw[field]
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise HTTPException(status_code=400, detail=f"backup_trim.{field} must be a whole number ≥ 1.")
        current[field] = n
    return current


@router.get("/policy")
def get_policy(db: Annotated[Session, Depends(get_db)], _owner: Annotated[User, Depends(require_owner)]) -> dict:
    return _policy_response(db)


@router.put("/policy", responses={400: {"description": "Bad request"}})
def update_policy(
    payload: RetentionPolicyUpdate,
    db: Annotated[Session, Depends(get_db)],
    owner: Annotated[User, Depends(require_owner_step_up)],
) -> dict:
    if payload.policy is not None:
        try:
            validated = retention_service.validate_policy(payload.policy)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        retention_service.save_policy(db, validated)

    if payload.receipt_delete_after_processing is not None:
        settings_service.set_value(
            db,
            settings_service.RECEIPT_DELETE_AFTER_PROCESSING,
            "true" if payload.receipt_delete_after_processing else "false",
        )

    if payload.backup_trim is not None:
        trim = _validate_backup_trim(payload.backup_trim)
        for field, key in (
            ("max_age_days", settings_service.BACKUP_MAX_AGE_DAYS),
            ("max_total_mb", settings_service.BACKUP_MAX_TOTAL_MB),
            ("min_keep", settings_service.BACKUP_MIN_KEEP),
        ):
            if trim[field] is not None:
                settings_service.set_value(db, key, str(trim[field]))

    audit_service.record(
        db,
        actor=owner.display_name,
        action="update_retention_policy",
        entity_type="retention",
        details={"policy_changed": payload.policy is not None},
    )
    db.commit()
    return _policy_response(db)


@router.get("/preview")
def preview(db: Annotated[Session, Depends(get_db)], _owner: Annotated[User, Depends(require_owner)]) -> dict:
    return retention_service.preview(db)


@router.post("/run")
def run(db: Annotated[Session, Depends(get_db)], owner: Annotated[User, Depends(require_owner_step_up)]) -> dict:
    result = retention_service.run(db, actor=owner.display_name, purge_mode="all")
    audit_service.record(
        db,
        actor=owner.display_name,
        action="run_retention",
        entity_type="retention",
        details=result["counts"],
    )
    db.commit()
    return result
