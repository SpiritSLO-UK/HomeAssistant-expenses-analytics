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


class RecommendedTransaction(BaseModel):
    """A pre-filled transaction recommended for an unmatched receipt (the user
    adds it in one click). Present only when nothing matched and a total is set."""

    merchant: str
    transaction_date: date
    amount: Decimal  # signed (negative = money out)
    currency: str
    category_id: int | None = None
    category_name: str | None = None


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
    # Set when the receipt is unmatched and has a total — what to add in one click.
    recommended_transaction: RecommendedTransaction | None = None


class ReceiptUploadOut(ReceiptOut):
    # Upload only: True when a byte-identical receipt already existed (deduped by
    # content hash) so the UI can say "already imported" instead of looking fresh.
    already_imported: bool = False


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


class CreateTransactionRequest(BaseModel):
    """Create a transaction from an unmatched receipt. Either target an existing
    account (``account_id``) or set ``new_account`` to use/create a dedicated
    'Cash & receipts' account for receipt-derived transactions."""

    account_id: int | None = None
    new_account: bool = False


class CreateTransactionResult(BaseModel):
    transaction_id: int
    receipt: ReceiptOut
