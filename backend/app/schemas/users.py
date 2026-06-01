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


class UserUpdate(BaseModel):
    role: str | None = None
    status: str | None = None  # pending | approved | disabled
    display_name: str | None = None
    email: str | None = None
