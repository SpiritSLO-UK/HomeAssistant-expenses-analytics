"""Schemas for the activity-log (audit) viewer (spec §28.5, §38; backlog #92)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AuditLogOut(BaseModel):
    id: int
    created_at: datetime
    actor: str | None
    action: str
    entity_type: str | None
    entity_id: int | None
    details: dict | None
