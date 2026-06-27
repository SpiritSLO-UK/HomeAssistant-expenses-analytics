"""Schemas for the MFA / auth API (backlog #124)."""

from __future__ import annotations

from pydantic import BaseModel


class CodeIn(BaseModel):
    code: str


class SetupIn(BaseModel):
    # Only needed to *re-enrol* an already-enabled user: the current authenticator
    # code, proving the caller holds the device before a new secret is issued (SR-1).
    code: str | None = None


class EnableIn(BaseModel):
    code: str
    scope: str | None = None  # "app" | "app_admin" (#157); None keeps the default


class SetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class VerifyOut(BaseModel):
    token: str
    expires_in_seconds: int
