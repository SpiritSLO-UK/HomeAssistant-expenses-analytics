"""Schemas for the energy-cost offset API (HA)."""

from __future__ import annotations

from pydantic import BaseModel


class EnergyConfig(BaseModel):
    source: str  # off | ha_api | mqtt
    production_entities: list[str]
    production_topics: list[str]
    tariff_per_kwh: str  # "" = derive from utility-meter readings
    energy_category_id: int | None
    production_semantics: str  # cumulative | interval (how the sensor reports, for the trend)


class EnergyConfigUpdate(BaseModel):
    """Partial update — only the fields sent are changed (model_dump(exclude_unset))."""

    source: str | None = None
    production_entities: list[str] | None = None
    production_topics: list[str] | None = None
    tariff_per_kwh: str | None = None
    energy_category_id: int | None = None
    production_semantics: str | None = None
