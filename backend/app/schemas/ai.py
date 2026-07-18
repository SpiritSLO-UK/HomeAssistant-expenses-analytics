"""Schemas for the AI API (spec §22, §24)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AIStatus(BaseModel):
    privacy_mode: str
    enabled: bool
    is_cloud: bool
    provider: str | None
    base_url: str | None
    model: str | None
    configured: bool
    has_api_key: bool
    # Where the resolved key comes from: "env" (HAFI_AI_API_KEY override), "stored"
    # (encrypted in the DB, set via the UI), or "none". The key value is never sent.
    key_source: str


class ClassifyResult(BaseModel):
    status: str  # ok | approval_required
    ai_request_id: int
    transaction_id: int | None = None
    category_id: int | None = None
    category_name: str | None = None
    confidence: float | None = None
    rationale: str | None = None
    country: str | None = None  # ISO-3166-1 alpha-2, when the AI could infer it (folded into ✨ suggest)
    vendor: str | None = None  # clean merchant name the AI inferred, to create + link (folded into ✨ suggest)
    payload: dict | None = None  # set when approval is required (preview)


class BatchSuggestion(BaseModel):
    transaction_id: int
    description: str
    amount: str
    category_id: int
    category_name: str
    confidence: float | None
    rationale: str | None
    already_ai_processed: bool = False  # has a prior completed AIRequest


class BatchResult(BaseModel):
    considered: int
    count: int
    suggestions: list[BatchSuggestion]


class CloudBatchItem(BaseModel):
    ai_request_id: int
    transaction_id: int
    description: str  # redacted — exactly what would be sent
    amount: str
    currency: str
    payload: dict  # the full redacted payload that would leave the device
    already_ai_processed: bool = False  # has a prior completed AIRequest → user can skip re-sending


class CloudBatchPreview(BaseModel):
    considered: int
    count: int
    items: list[CloudBatchItem]


class CloudBatchSendRequest(BaseModel):
    approve_ids: list[int]
    reject_ids: list[int] = []


class CloudBatchSendResult(BaseModel):
    count: int
    suggestions: list[BatchSuggestion]
    failed: list[int]
    rejected: int


class ApplyItem(BaseModel):
    transaction_id: int
    category_id: int


class ApplyRequest(BaseModel):
    items: list[ApplyItem]


class ApplyResult(BaseModel):
    applied: int


class AIRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    transaction_id: int | None
    provider: str
    model: str | None
    task_type: str
    privacy_mode: str
    approval_status: str
    status: str
    confidence_score: float | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
