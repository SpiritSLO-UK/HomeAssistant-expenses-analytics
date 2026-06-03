"""Schemas for the receipts API (spec §24.10, §21)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class ReceiptMatchOut(BaseModel):
    transaction_id: int
    match_score: float | None
    match_status: str
    matched_by: str | None


class ReceiptOut(BaseModel):
    id: int
    source_filename: str | None
    receipt_date: date | None
    merchant_raw: str | None
    total_amount: Decimal | None
    vat_amount: Decimal | None
    currency: str | None
    ocr_status: str
    ocr_confidence: float | None
    needs_review: bool
    has_file: bool = False  # the original is still on disk (viewable), not dropped by retention
    matches: list[ReceiptMatchOut]


class ReceiptUpdate(BaseModel):
    """Manual entry / correction of receipt fields (spec §21.3)."""

    merchant_raw: str | None = Field(default=None, max_length=300)
    receipt_date: date | None = None
    total_amount: Decimal | None = Field(default=None, ge=0)
    vat_amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=3)


class MatchCandidate(BaseModel):
    transaction_id: int
    score: int
    breakdown: dict
    transaction_date: date
    amount: Decimal
    description: str


class MatchResult(BaseModel):
    status: str  # suggested | auto_confirmed | unmatched
    best_score: int
    candidates: list[MatchCandidate]


class ConfirmMatchRequest(BaseModel):
    transaction_id: int
