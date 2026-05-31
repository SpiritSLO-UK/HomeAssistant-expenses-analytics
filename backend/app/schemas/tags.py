"""Schemas for the tags API (spec §24, §18.3)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    colour: str | None


class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    colour: str | None = Field(default=None, max_length=16)


class TagUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    colour: str | None = Field(default=None, max_length=16)


class SetTagsRequest(BaseModel):
    """Replace a transaction's tags with these names (new ones are created)."""

    tags: list[str]
