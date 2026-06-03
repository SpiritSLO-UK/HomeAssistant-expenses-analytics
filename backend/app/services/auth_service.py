"""Identity & access control (spec §6, §8.2, §28; backlog #82, #126, #74).

Home Assistant authenticates the user *before* the request reaches this add-on
and forwards their identity via ingress headers (``X-Remote-User-Id`` /
``X-Remote-User-Name`` / ``X-Remote-User-Display-Name``). We map that to a
:class:`User` row. Running standalone (no HA, e.g. local dev) there is no header,
so we fall back to a single ``"local"`` owner — behaviour identical to the old
single-user app.

Bootstrap rule: the **first** user ever seen becomes the ``owner`` and is
auto-approved. Anyone who appears afterwards starts as a ``member`` with status
``pending`` and has no data access until the owner approves them (#126).

This module never trusts a client-supplied role — the role always comes from the
stored row, keyed by the proxy-supplied identity (#74).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Account, User
from app.models.user import ADMIN_ROLES, ROLES, STATUSES, WRITE_ROLES
from app.services import audit_service
from app.services.household_service import get_or_create_default_household

# HA ingress identity headers (lower-cased lookup; Starlette headers are
# case-insensitive). ``X-Remote-User-Id`` is the stable key.
HEADER_ID = "x-remote-user-id"
HEADER_NAME = "x-remote-user-name"
HEADER_DISPLAY = "x-remote-user-display-name"

# Per-device MFA session token (issued after a TOTP challenge, backlog #124).
SESSION_HEADER = "x-hafi-session"

# Standalone / dev fallback identity (no HA in front).
LOCAL_EXTERNAL_ID = "local"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _identity_from_request(request: Request) -> tuple[str, str]:
    """Return ``(external_id, display_name)`` from ingress headers or the local
    fallback. Never raises — an unauthenticated edge resolves to the local user."""
    headers = request.headers
    ext_id = (headers.get(HEADER_ID) or "").strip()
    display = (headers.get(HEADER_DISPLAY) or headers.get(HEADER_NAME) or "").strip()
    if not ext_id:
        return LOCAL_EXTERNAL_ID, (display or "Local User")
    return ext_id, (display or "Home Assistant user")


def resolve_current_user(db: Session, request: Request) -> User:
    """Find or create the user for this request and refresh ``last_seen_at``.

    First user → owner+approved (bootstrap). Later users → member+pending (#126).
    The caller is responsible for committing the session.
    """
    ext_id, display = _identity_from_request(request)
    user = db.scalars(select(User).where(User.external_id == ext_id)).first()

    if user is None:
        # Adopt a pre-existing single-user row (created before multi-user) so an
        # upgraded install keeps its owner instead of spawning a duplicate.
        if ext_id == LOCAL_EXTERNAL_ID:
            user = db.scalars(
                select(User).where(User.external_id.is_(None)).order_by(User.id).limit(1)
            ).first()

    if user is None:
        is_first = db.scalar(select(func.count(User.id))) == 0
        household = get_or_create_default_household(db)
        user = User(
            household_id=household.id,
            external_id=ext_id,
            display_name=display,
            role="owner" if is_first else "member",
            status="approved" if is_first else "pending",
            is_active=True,
        )
        db.add(user)
        db.flush()
    else:
        if user.external_id is None:
            user.external_id = ext_id
        if display and user.display_name != display and ext_id != LOCAL_EXTERNAL_ID:
            user.display_name = display

    user.last_seen_at = _now()
    return user


# --- Role helpers (single source of truth) ---


def can_write(role: str) -> bool:
    return role in WRITE_ROLES


def is_admin(role: str) -> bool:
    return role in ADMIN_ROLES


# --- Account visibility (shared vs private; backlog #66/#82) ---------------
#
# An account is PRIVATE iff it has an owner AND isn't shared
# (``owner_user_id IS NOT NULL AND is_shared == False``). Accounts with no owner
# (the default for every existing / auto-created account) are household-shared,
# so legacy data stays visible to everyone with no backfill.


def _shared_or_own(user_id: int):
    """SQL predicate for accounts a non-admin may see: shared (or unowned) plus
    their own private accounts."""
    return or_(
        Account.owner_user_id.is_(None),
        Account.is_shared.is_(True),
        Account.owner_user_id == user_id,
    )


def visible_account_ids(db: Session, user: User) -> set[int] | None:
    """Account ids this user may see, or ``None`` meaning **unrestricted**.

    The owner/admin sees everything (``None`` — a genuine no-filter fast path).
    Everyone else sees shared/unowned accounts plus their own private ones.
    Returning ``None`` (not the full set) for owners keeps every aggregate on a
    real fast path with no ``account_id IN (...)`` clause.
    """
    if is_admin(user.role):
        return None
    return set(db.scalars(select(Account.id).where(_shared_or_own(user.id))).all())


def visible_account_scope(request: Request, db: Session) -> set[int] | None:
    """Resolve the request's user and return their visible account-id set
    (``None`` = unrestricted). Convenience for read routes."""
    return visible_account_ids(db, get_current_user(request, db))


def scoped_account_ids(db: Session, user: User, scope: str) -> set[int] | None:
    """Apply a Mine/Shared/All view toggle on top of the base visibility set.

    ``all`` → the base visible set (``None`` for the owner). ``mine`` → accounts
    the user owns; ``shared`` → shared/unowned accounts. ``mine``/``shared`` are
    intersected with the base set so the toggle can only ever *narrow*, never
    widen, what a user may see.
    """
    base = visible_account_ids(db, user)
    if scope == "all":
        return base
    if scope == "mine":
        chosen = set(db.scalars(select(Account.id).where(Account.owner_user_id == user.id)).all())
    else:  # shared
        chosen = set(
            db.scalars(
                select(Account.id).where(
                    or_(Account.owner_user_id.is_(None), Account.is_shared.is_(True))
                )
            ).all()
        )
    return chosen if base is None else (chosen & base)


def member_account_scope(db: Session, user: User, member_id: int) -> set[int]:
    """Account ids **owned by** ``member_id``, intersected with what ``user`` may
    see — the per-member spend filter (backlog #66/#82).

    The intersection is the security boundary: a member can only ever narrow
    *within* their own visibility, never use the filter to peek at someone else's
    private accounts. A member who owns no accounts yields the empty set (→ "show
    nothing", never "everything")."""
    owned = set(db.scalars(select(Account.id).where(Account.owner_user_id == member_id)).all())
    base = visible_account_ids(db, user)
    return owned if base is None else (owned & base)


def unowned_account_scope(db: Session, user: User) -> set[int]:
    """Account ids with **no owner** (household/shared-without-a-person),
    intersected with what ``user`` may see. Used for the "Shared / unassigned"
    row of the per-member breakdown — every account is either owned by exactly one
    member (their row) or unowned (this row), so the two partition all spend with
    no double-counting."""
    unowned = set(db.scalars(select(Account.id).where(Account.owner_user_id.is_(None))).all())
    base = visible_account_ids(db, user)
    return unowned if base is None else (unowned & base)


def resolved_account_scope(
    db: Session, user: User, *, view: str = "all", member_id: int | None = None
) -> set[int] | None:
    """The read scope for a request: a specific member's owned accounts when
    ``member_id`` is given, otherwise the Mine/Shared/All ``view`` toggle.
    ``member_id`` takes precedence — picking a person makes the view toggle moot."""
    if member_id is not None:
        return member_account_scope(db, user, member_id)
    return scoped_account_ids(db, user, view)


# --- FastAPI dependencies ---


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """The authenticated user for this request.

    The auth middleware resolves the user up front and stashes the id on
    ``request.state``; we re-load it on the route's own session. If state is
    absent (e.g. a route hit before the middleware, or in a unit test) we resolve
    directly.
    """
    uid = getattr(request.state, "user_id", None)
    if uid is not None:
        user = db.get(User, uid)
        if user is not None:
            return user
    user = resolve_current_user(db, request)
    db.commit()
    return user


def require_owner(user: User = Depends(get_current_user)) -> User:
    """Gate admin-only endpoints (user management, system actions)."""
    if not is_admin(user.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the owner (administrator) role.",
        )
    return user


def require_owner_step_up(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_owner),
) -> User:
    """Owner endpoints that also need a recent MFA step-up when the owner has MFA
    enabled (backlog #124 — "re-enter for admin stuff"). A no-op for owners
    without MFA. Signals ``step_up_required`` so the UI can prompt for a code."""
    if user.mfa_enabled:
        from app.services import mfa_service

        token = request.headers.get(SESSION_HEADER)
        session = mfa_service.get_valid_session(db, user.id, token)
        if not mfa_service.has_recent_step_up(session):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="step_up_required")
    return user


# --- User administration (owner only; guarded against losing the last owner) ---


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id)).all())


def list_members(db: Session) -> list[User]:
    """Approved household members for the per-member spend filter dropdown.
    Excludes pending/disabled accounts. Readable by any approved user — the data
    those members map to is still scoped per the caller's own visibility."""
    return list(
        db.scalars(select(User).where(User.status == "approved").order_by(User.id)).all()
    )


def approved_owner_count(db: Session) -> int:
    return db.scalar(
        select(func.count(User.id)).where(User.role == "owner", User.status == "approved")
    ) or 0


def _is_last_owner(db: Session, target: User) -> bool:
    return (
        target.role == "owner"
        and target.status == "approved"
        and approved_owner_count(db) <= 1
    )


def update_user(
    db: Session,
    *,
    actor: User,
    target: User,
    role: str | None = None,
    new_status: str | None = None,
    display_name: str | None = None,
    email: str | None = None,
) -> User:
    """Apply an owner-initiated change to ``target``. Raises ``ValueError`` on a
    bad value or if the change would strip the household's last active owner."""
    if role is not None and role not in ROLES:
        raise ValueError(f"role must be one of {list(ROLES)}")
    if new_status is not None and new_status not in STATUSES:
        raise ValueError(f"status must be one of {list(STATUSES)}")

    # Self-protection: you can't lock yourself out by disabling your own account
    # (even when other owners exist). Stepping *down* (demoting your own role) is
    # still allowed — you keep access as a member — and the last-owner guard below
    # stops the household losing its final owner.
    if actor.id == target.id and new_status == "disabled":
        raise ValueError("You can't disable your own account.")

    effective_role = role if role is not None else target.role
    effective_status = new_status if new_status is not None else target.status
    remains_active_owner = effective_role == "owner" and effective_status == "approved"
    if _is_last_owner(db, target) and not remains_active_owner:
        raise ValueError("Cannot demote, disable, or remove the last active owner.")

    changes: dict = {}
    if role is not None and role != target.role:
        changes["role"] = [target.role, role]
        target.role = role
    if new_status is not None and new_status != target.status:
        changes["status"] = [target.status, new_status]
        target.status = new_status
        target.is_active = new_status != "disabled"
    if display_name is not None and display_name.strip():
        target.display_name = display_name.strip()
    if email is not None:
        target.email = email.strip() or None

    audit_service.record(
        db,
        actor=actor.display_name,
        action="update_user",
        entity_type="user",
        entity_id=target.id,
        details={"changes": changes},
        household_id=target.household_id,
    )
    db.commit()
    db.refresh(target)
    return target


def delete_user(db: Session, *, actor: User, target: User) -> None:
    if actor.id == target.id:
        raise ValueError("You can't delete your own account.")
    if _is_last_owner(db, target):
        raise ValueError("Cannot delete the last active owner.")
    audit_service.record(
        db,
        actor=actor.display_name,
        action="delete_user",
        entity_type="user",
        entity_id=target.id,
        details={"display_name": target.display_name, "role": target.role},
        household_id=target.household_id,
    )
    db.delete(target)
    db.commit()
