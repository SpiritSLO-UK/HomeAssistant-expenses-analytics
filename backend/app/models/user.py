"""User model (spec §6, §12.2).

Identity comes from Home Assistant ingress (the reverse proxy sets
``X-Remote-User-*`` headers); standalone/dev falls back to a single local owner.
Users therefore *appear* on first request rather than being created by a form —
the owner then manages their role and approval status (backlog #82, #126).
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# Roles (most→least privileged). ``owner`` is the administrator.
#   owner  — full admin: user management, settings, destructive/system actions
#   member — read + write their own/shared finance data
#   viewer — read-only
#   child  — read-only, intended for a limited budget/savings view (frontend nav)
ROLES = ("owner", "member", "viewer", "child")
WRITE_ROLES = ("owner", "member")  # may perform non-GET requests
ADMIN_ROLES = ("owner",)  # may manage users / system

# Account lifecycle (backlog #126: a new user is pending until approved).
STATUSES = ("pending", "approved", "disabled")

# MFA scope (#157) — what a user's two-factor gates:
#   app        — a code only when opening the app (entry challenge)
#   app_admin  — entry challenge AND a step-up to confirm admin actions (default;
#                preserves the original behaviour)
MFA_SCOPES = ("app", "app_admin")
# MFA policy (#157) — admin enforcement per user:
#   optional   — the user chooses (default)
#   required   — the user is blocked from the app until they enrol in MFA
MFA_POLICIES = ("optional", "required")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    # Stable identifier from the HA ingress header (``X-Remote-User-Id``), or
    # ``"local"`` for the standalone single-user fallback. Used to match a
    # returning user to their row.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="owner")
    # pending | approved | disabled — the gate the API enforces.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="approved")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Owner-granted permission to manage the general Settings + tab customisation
    # (backlog #28 RBAC). Owners can always manage; this lets a non-owner member be
    # given the same. Defaults off.
    can_manage_settings: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Optional app-level MFA (TOTP, backlog #124). ``mfa_secret`` is the base32
    # seed; ``mfa_enabled`` flips true only after the user confirms a code.
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # A not-yet-confirmed secret held during (re-)enrolment. The live ``mfa_secret``
    # and ``mfa_enabled`` stay untouched until a code for this pending secret is
    # confirmed via ``enable`` — so starting enrolment can never silently downgrade
    # an already-active factor (SR-1).
    mfa_pending_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # What MFA gates for this user + whether an admin requires it (backlog #157).
    mfa_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="app_admin")
    mfa_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="optional")
    # Highest TOTP timestep already accepted for app-entry, for one-time-use /
    # anti-replay (CR-SEC-5): a code at a counter <= this is a replay and refused.
    # Reset on (re-)enrolment since a new secret has its own timeline.
    mfa_last_counter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Owner-set list of app pages this (non-admin) user may NOT reach (backlog #108),
    # JSON array of nav-page keys (e.g. ["budgets","investments"]). NULL/empty =
    # unrestricted. Enforced server-side by the auth guard + hidden in the sidebar.
    blocked_nav: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-user customised sidebar layout (grouped nav, backlog PR1/4), JSON object
    # of {v, groups:[{id,label?,icon?,items:[{path,label?,hidden?}]}]}. NULL = use
    # the built-in default layout. Set self-service via PUT /users/me/nav-layout.
    nav_layout: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def blocked_nav_keys(self) -> list[str]:
        """The parsed list of blocked nav-page keys (empty when unset/invalid)."""
        if not self.blocked_nav:
            return []
        try:
            data = json.loads(self.blocked_nav)
        except (ValueError, TypeError):
            return []
        return [str(k) for k in data] if isinstance(data, list) else []

    @property
    def nav_layout_obj(self) -> dict | None:
        """The parsed custom nav layout, or ``None`` when unset/invalid.

        Defensive like ``blocked_nav_keys``: NULL/empty or malformed JSON (or a
        non-object payload) collapses to ``None`` so the frontend falls back to the
        built-in default layout."""
        if not self.nav_layout:
            return None
        try:
            data = json.loads(self.nav_layout)
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None
