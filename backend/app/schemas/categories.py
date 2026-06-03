"""Schemas for the categories API (spec §24.5)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: int | None
    library_id: str | None
    name: str
    path: str | None
    description: str | None
    icon: str | None
    colour: str | None
    is_system: bool
    is_active: bool
    is_budgetable: bool
    privacy_sensitivity: str


class CategoryCreate(BaseModel):
    name: str
    parent_id: int | None = None
    description: str | None = None
    icon: str | None = None
    colour: str | None = None
    is_budgetable: bool = True
    privacy_sensitivity: str = "normal"


class CategoryUpdate(BaseModel):
    name: str | None = None
    parent_id: int | None = None
    description: str | None = None
    icon: str | None = None
    colour: str | None = None
    is_active: bool | None = None
    is_budgetable: bool | None = None
    privacy_sensitivity: str | None = None


class CategoryMerge(BaseModel):
    target_id: int  # the category to merge this one into (it absorbs all references)
