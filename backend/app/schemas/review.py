"""Schemas for the review queue API (spec §23, §12.18)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

STATUSES = {"open", "resolved", "ignored"}


class ReviewItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_type: str
    item_id: int | None
    reason: str
    severity: str
    status: str
    suggested_action: str | None
    created_at: datetime
    resolved_at: datetime | None


class ReviewStatusUpdate(BaseModel):
    status: str  # open | resolved | ignored
