"""Schemas for the vendors API (spec §24.6)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AliasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alias: str
    match_type: str
    source: str | None


class VendorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    canonical_name: str
    display_name: str | None
    default_category_id: int | None
    service_type: str | None
    website: str | None
    notes: str | None
    last_seen_at: datetime | None
    aliases: list[AliasOut] = []


class VendorWithStats(VendorOut):
    transaction_count: int = 0
    total_amount: str = "0"


class VendorCreate(BaseModel):
    canonical_name: str
    display_name: str | None = None
    default_category_id: int | None = None
    service_type: str | None = None
    website: str | None = None
    notes: str | None = None
    alias: str | None = None
    match_type: str = "contains"


class VendorUpdate(BaseModel):
    canonical_name: str | None = None
    display_name: str | None = None
    default_category_id: int | None = None
    service_type: str | None = None
    website: str | None = None
    notes: str | None = None


class AliasCreate(BaseModel):
    alias: str
    match_type: str = "contains"


class SetDefaultCategory(BaseModel):
    category_id: int | None = None
