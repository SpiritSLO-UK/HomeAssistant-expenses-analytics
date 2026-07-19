"""Schemas for the users / access-control API (spec §6, §12.2)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    email: str | None
    role: str
    status: str
    is_active: bool
    can_manage_settings: bool
    blocked_nav_keys: list[str] = []  # pages this user is restricted from (#108)
    mfa_enabled: bool = False
    mfa_policy: str = "optional"  # optional | required (admin-set, #157)
    external_id: str | None
    last_seen_at: datetime | None
    created_at: datetime


class MeOut(BaseModel):
    """The current user plus convenience flags the frontend gates the UI on."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    role: str
    status: str
    is_admin: bool
    can_write: bool
    can_manage_settings: bool
    blocked_nav_keys: list[str] = []  # pages this user is restricted from (#108)
    mfa_enabled: bool
    mfa_scope: str = "app_admin"  # app | app_admin — what MFA gates (#157)
    mfa_policy: str = "optional"  # optional | required (admin-set, #157)
    # True when the user has MFA on but this request lacks a valid session — the
    # frontend then shows the MFA entry gate.
    mfa_required: bool
    # True when an admin requires MFA but the user hasn't enrolled yet (#157) — the
    # frontend then shows the "set up MFA" gate.
    mfa_setup_required: bool = False
    # The user's custom sidebar layout (grouped nav, PR1/4), or null to use the
    # built-in default. Stored/normalised server-side; see routes_users.
    nav_layout: dict | None = None


class MemberOut(BaseModel):
    """Minimal member identity for the per-member spend filter dropdown."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    role: str


class NavLayoutItemIn(BaseModel):
    """One nav entry inside a group. ``path`` is validated against the server-side
    allowlist in the route; unknown paths are silently dropped."""

    path: str
    label: str | None = None
    icon: str | None = None
    hidden: bool | None = None


class NavLayoutGroupIn(BaseModel):
    id: str
    label: str | None = None
    icon: str | None = None
    items: list[NavLayoutItemIn] = []


class NavLayoutIn(BaseModel):
    """PUT body for a user's custom sidebar layout (grouped nav, PR1/4)."""

    v: int = 1
    groups: list[NavLayoutGroupIn] = []


class UserUpdate(BaseModel):
    role: str | None = None
    status: str | None = None  # pending | approved | disabled
    display_name: str | None = None
    email: str | None = None
    can_manage_settings: bool | None = None
    blocked_nav_keys: list[str] | None = None  # pages to restrict this user from (#108)
    mfa_policy: str | None = None  # optional | required (admin enforces MFA, #157)
