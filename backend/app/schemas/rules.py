"""Schemas for the rules API (spec §24.7)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    priority: int
    enabled: bool
    condition_type: str
    condition_value: str
    action_type: str
    action_value: str | None
    created_from: str | None


class RuleCreate(BaseModel):
    condition_type: str
    condition_value: str
    action_type: str
    action_value: str | None = None
    name: str | None = None
    priority: int = 100
    enabled: bool = True


class RuleUpdate(BaseModel):
    name: str | None = None
    priority: int | None = None
    enabled: bool | None = None
    condition_type: str | None = None
    condition_value: str | None = None
    action_type: str | None = None
    action_value: str | None = None


class RuleTestRequest(BaseModel):
    condition_type: str
    condition_value: str
