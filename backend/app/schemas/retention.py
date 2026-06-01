"""Schemas for the data-retention API (spec §28; backlog #78)."""

from __future__ import annotations

from pydantic import BaseModel


class RetentionPolicyUpdate(BaseModel):
    """Owner-supplied retention changes. All optional — send only what changed.

    ``policy`` is a permissive dict keyed by data type (validated server-side by
    ``retention_service.validate_policy``); ``backup_trim`` carries the
    ``max_age_days`` / ``max_total_mb`` / ``min_keep`` limits.
    """

    policy: dict | None = None
    receipt_delete_after_processing: bool | None = None
    backup_trim: dict | None = None
