"""Security-health evaluation (backlog #128).

Surfaces which protections are on/off with a one-line recommendation each, so the
owner can see and fix gaps — without nagging. Each warning can be **dismissed**
(forever) or **snoozed** for N days; dismissals live in a settings row so they
persist. Read-only/informational checks never block anything.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import User
from app.services import retention_service, security_service, settings_service

DISMISSALS_KEY = "security_dismissals"

_ENCRYPTION_TITLE = "At-rest database encryption"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _load_dismissals(db: Session) -> dict:
    raw = settings_service.get(db, DISMISSALS_KEY)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):  # pragma: no cover - corrupt value
        return {}


def _save_dismissals(db: Session, data: dict) -> None:
    settings_service.set_value(db, DISMISSALS_KEY, json.dumps(data))


def _dismissal_state(dismissals: dict, check_id: str) -> tuple[bool, str | None]:
    """Return (dismissed_now, snoozed_until_iso). An expired snooze counts as not
    dismissed so the warning reappears."""
    value = dismissals.get(check_id)
    if not value:
        return (False, None)
    if value == "forever":
        return (True, None)
    try:
        until = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return (False, None)
    return (True, until.isoformat()) if until > _now() else (False, None)


def _owners_without_mfa(db: Session, household_id: int | None) -> int:
    """Count approved owner accounts in the household that have no second factor.

    The posture check must reflect *every* owner, not just the caller: a co-owner
    without MFA is a gap even when you have it enabled. Scoped to the caller's
    household so a multi-household deployment never leaks across boundaries.
    """
    return db.scalar(
        select(func.count(User.id)).where(
            User.household_id == household_id,
            User.role == "owner",
            User.status == "approved",
            User.mfa_enabled.is_(False),
        )
    ) or 0


def _add(checks: list, dismissals: dict, *, id: str, title: str, severity: str,
         recommendation: str, actionable: bool) -> None:
    dismissed, snoozed_until = _dismissal_state(dismissals, id)
    checks.append({
        "id": id,
        "title": title,
        "severity": severity,  # ok | info | warn
        "recommendation": recommendation,
        "actionable": actionable,
        "dismissed": dismissed,
        "snoozed_until": snoozed_until,
        # "active" = something to surface: a non-ok check that isn't dismissed.
        "active": severity != "ok" and not dismissed,
    })


def evaluate(db: Session, user: User) -> dict:
    dismissals = _load_dismissals(db)
    sec = security_service.status()
    checks: list[dict] = []

    # At-rest encryption
    if not sec["encryption_available"]:
        _add(checks, dismissals, id="encryption", title=_ENCRYPTION_TITLE,
             severity="info", actionable=False,
             recommendation="Not available on this platform (needs SQLCipher — e.g. the Home "
             "Assistant add-on). Encrypted backups still work everywhere.")
    elif not sec["encryption_enabled"]:
        _add(checks, dismissals, id="encryption", title=_ENCRYPTION_TITLE,
             severity="warn", actionable=True,
             recommendation="Your database isn't encrypted on disk. Enable it in "
             "Settings → Database encryption so a stolen disk can't be read.")
    else:
        _add(checks, dismissals, id="encryption", title=_ENCRYPTION_TITLE,
             severity="ok", actionable=False, recommendation="Database is encrypted at rest.")
        if sec.get("unlock_mode") == "stored":
            _add(checks, dismissals, id="stored_key", title="Encryption unlock mode",
                 severity="info", actionable=False,
                 recommendation="Using a stored key (unattended unlock). 'Prompt me' is stronger "
                 "if you can enter the passphrase at each restart.")

    # MFA across all owner accounts in the household (not just the caller's own).
    owners_without_mfa = _owners_without_mfa(db, user.household_id)
    if owners_without_mfa:
        if owners_without_mfa == 1 and not user.mfa_enabled:
            rec = ("Your owner account has no second factor. Turn on MFA in "
                   "Settings → Two-factor authentication.")
        else:
            rec = (f"{owners_without_mfa} owner account(s) have no second factor. "
                   "Every owner should turn on MFA in Settings → Two-factor authentication.")
        _add(checks, dismissals, id="mfa", title="Two-factor authentication",
             severity="warn", actionable=True, recommendation=rec)
    else:
        _add(checks, dismissals, id="mfa", title="Two-factor authentication",
             severity="ok", actionable=False,
             recommendation="MFA is enabled on all owner accounts.")

    # Repeated failed unlock attempts
    recent = sec["failed_unlocks"]["recent"]
    if recent >= 3:
        _add(checks, dismissals, id="failed_unlocks", title="Failed unlock attempts",
             severity="warn", actionable=False,
             recommendation=f"{recent} failed database-unlock attempts in the last hour. "
             "If that wasn't you, change your passphrase.")

    # Users awaiting approval — scoped to the caller's household.
    pending = db.scalar(
        select(func.count(User.id)).where(
            User.household_id == user.household_id,
            User.status == "pending",
        )
    ) or 0
    if pending:
        _add(checks, dismissals, id="pending_users", title="Users awaiting approval",
             severity="info", actionable=True,
             recommendation=f"{pending} user(s) are waiting for approval — review them in Users.")

    # Automatic cloud AI
    if settings_service.get_privacy_mode(db) == "cloud_auto":
        _add(checks, dismissals, id="cloud_ai", title="Automatic cloud AI",
             severity="info", actionable=True,
             recommendation="AI is set to cloud_auto — redacted requests are sent automatically. "
             "Use cloud_manual to approve each one, or a local LLM to keep data on-device.")

    # Data scheduled for removal awaiting confirmation (backlog #78). This is how
    # the owner is told *before* a confirm-required purge happens — the startup
    # sweep only archived these; the delete needs an explicit Run.
    try:
        pending_purge = retention_service.preview(db)["pending_purge"]
    except Exception:  # pragma: no cover - never let a health check break the page
        pending_purge = 0
    if pending_purge:
        _add(checks, dismissals, id="retention_pending", title="Data scheduled for removal",
             severity="info", actionable=True,
             recommendation=f"{pending_purge} item(s) are past their purge age and waiting for you "
             "to confirm removal. Review the plan in Settings → Data retention.")

    return {
        "checks": checks,
        "active_count": sum(1 for c in checks if c["active"]),
        "failed_unlocks": sec["failed_unlocks"],
    }


def dismiss(db: Session, check_id: str, *, snooze_days: int | None = None,
            clear: bool = False) -> tuple[bool, str | None]:
    dismissals = _load_dismissals(db)
    if clear:
        dismissals.pop(check_id, None)
    elif snooze_days and snooze_days > 0:
        dismissals[check_id] = (_now() + timedelta(days=snooze_days)).isoformat()
    else:
        dismissals[check_id] = "forever"
    _save_dismissals(db, dismissals)
    return _dismissal_state(dismissals, check_id)
