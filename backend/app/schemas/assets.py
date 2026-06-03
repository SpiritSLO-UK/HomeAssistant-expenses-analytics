"""Schemas for the assets API (car/home dashboards; spec §25.1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

ASSET_KINDS = {"car", "home", "other"}
DISTANCE_UNITS = {"mi", "km"}
LOG_KINDS = {"refuel", "service", "expense", "reading", "note"}


class AssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = "car"  # car | home | other
    identifier: str | None = Field(default=None, max_length=100)
    distance_unit: str = "mi"  # mi | km


class AssetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    identifier: str | None = None
    distance_unit: str | None = None
    is_active: bool | None = None


class AssetLogCreate(BaseModel):
    log_date: date
    kind: str = "refuel"  # refuel | service | expense | reading | note
    note: str | None = Field(default=None, max_length=300)
    cost: Decimal | None = Field(default=None, ge=0)
    # car
    odometer: Decimal | None = Field(default=None, ge=0)
    litres: Decimal | None = Field(default=None, gt=0)
    is_full_tank: bool = True
    fuel_type: str | None = Field(default=None, max_length=20)
    # home (PR D)
    meter: str | None = Field(default=None, max_length=40)
    reading: Decimal | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=16)
    transaction_id: int | None = None
