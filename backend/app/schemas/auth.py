"""Schemas for the MFA / auth API (backlog #124)."""

from __future__ import annotations

from pydantic import BaseModel


class CodeIn(BaseModel):
    code: str


class EnableIn(BaseModel):
    code: str
    scope: str | None = None  # "app" | "app_admin" (#157); None keeps the default


class SetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class VerifyOut(BaseModel):
    token: str
    expires_in_seconds: int
