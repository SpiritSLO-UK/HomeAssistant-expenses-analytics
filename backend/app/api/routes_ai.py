"""AI gateway API routes (spec §22, §24)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.models import AIRequest, Transaction, User
from app.schemas.ai import (
    AIRequestOut,
    AIStatus,
    ApplyRequest,
    ApplyResult,
    BatchResult,
    ClassifyResult,
    CloudBatchPreview,
    CloudBatchSendRequest,
    CloudBatchSendResult,
)
from app.services import ai_guard, ai_service, auth_service
from app.services.ai_provider import AIError
from app.services.ai_service import AIDisabled
from app.services.auth_service import get_current_user

# 429/413 come from the shared abuse guard below, so document them router-wide
# (same pattern as the MFA lockout in routes_auth).
router = APIRouter(
    prefix="/ai",
    tags=["ai"],
    responses={
        413: {"description": "Request body exceeds the AI payload cap"},
        429: {"description": "AI rate limit or daily AI budget reached"},
    },
)


def _scope(request: Request, db: Session) -> set[int] | None:
    return auth_service.visible_account_scope(request, db)


def _ai_guard(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Abuse guard on the AI-dispatching POST routes (services/ai_guard.py):
    payload-size cap (413), per-user rate limit (429), daily budget (429).
    Cheap local routes (/apply, /requests/{id}/reject) dispatch no AI call and
    are deliberately not guarded."""
    cap = ai_guard.oversize_payload_cap(request.headers.get("content-length"))
    if cap is not None:
        raise HTTPException(
            status_code=413,
            detail=f"Request body exceeds the AI payload cap ({cap} bytes).",
        )
    wait = ai_guard.rate_limit_wait_seconds(user.id)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=(
                f"AI rate limit reached ({settings.ai_rate_limit_per_minute} requests/minute). "
                f"Try again in about {wait} second(s)."
            ),
            headers={"Retry-After": str(wait)},
        )
    budget = ai_guard.daily_cap_reached(db)
    if budget is not None:
        raise HTTPException(status_code=429, detail=ai_guard.daily_budget_message(*budget))


@router.get("/status", response_model=AIStatus)
def ai_status(db: Annotated[Session, Depends(get_db)]) -> dict:
    return ai_service.status(db)


@router.post("/test", dependencies=[Depends(_ai_guard)])
def ai_test(db: Annotated[Session, Depends(get_db)]) -> dict:
    """Probe the configured AI endpoint/key/model with a tiny request and report
    ``{ok, message, ...}``. Always 200 — a provider error is reported, not raised."""
    return ai_service.test_connection(db)


@router.get("/requests", response_model=list[AIRequestOut])
def ai_requests(
    db: Annotated[Session, Depends(get_db)],
    include_archived: Annotated[bool, Query(description="Include archived (aged-out) entries")] = False,
):
    """The AI audit log (spec §22.6)."""
    return ai_service.list_requests(db, include_archived=include_archived)


@router.post(
    "/classify/{transaction_id}",
    response_model=ClassifyResult,
    dependencies=[Depends(_ai_guard)],
    responses={
        400: {"description": "Bad request"},
        404: {"description": "Not found"},
        502: {"description": "Upstream error"},
    },
)
def classify(transaction_id: int, request: Request, db: Annotated[Session, Depends(get_db)]) -> dict:
    """Ask AI to suggest a category (suggestion only — never applied here).
    In cloud_manual mode this returns ``approval_required``; approve it via
    ``/api/ai/requests/{id}/approve``."""
    txn = db.get(Transaction, transaction_id)
    scope = _scope(request, db)
    if txn is None or (scope is not None and txn.account_id is not None and txn.account_id not in scope):
        raise HTTPException(status_code=404, detail="Transaction not found")
    try:
        return ai_service.classify_transaction(db, txn)
    except AIDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _get_request(db: Session, request_id: int) -> AIRequest:
    req = db.get(AIRequest, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="AI request not found")
    return req


@router.post(
    "/requests/{request_id}/approve",
    response_model=ClassifyResult,
    dependencies=[Depends(_ai_guard)],
    responses={
        400: {"description": "Bad request"},
        404: {"description": "Not found"},
        502: {"description": "Upstream error"},
    },
)
def approve_request(request_id: int, db: Annotated[Session, Depends(get_db)]) -> dict:
    """Approve a pending cloud request and send it (spec §22.5)."""
    req = _get_request(db, request_id)
    try:
        return ai_service.run_request(db, req)
    except AIDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/requests/{request_id}/reject",
    response_model=AIRequestOut,
    responses={404: {"description": "Not found"}},
)
def reject_request(request_id: int, db: Annotated[Session, Depends(get_db)]) -> AIRequest:
    """Reject a pending cloud request — nothing is sent (spec §22.5)."""
    return ai_service.reject_request(db, _get_request(db, request_id))


@router.post(
    "/classify-batch",
    response_model=BatchResult,
    dependencies=[Depends(_ai_guard)],
    responses={400: {"description": "Bad request"}, 502: {"description": "Upstream error"}},
)
def classify_batch(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    scope: Annotated[str, Query(pattern="^(uncategorised|recheck)$")] = "uncategorised",
) -> dict:
    """Suggest categories in a batch (local_llm only). ``scope=uncategorised``
    (default) only fills blanks; ``scope=recheck`` re-examines auto-categorised
    rows too (never manual). Suggestions only — apply with /api/ai/apply."""
    try:
        return ai_service.classify_batch(db, limit=limit, scope=scope, account_ids=_scope(request, db))
    except AIDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/apply", response_model=ApplyResult)
def apply(payload: ApplyRequest, db: Annotated[Session, Depends(get_db)]) -> dict:
    """Apply user-approved AI category suggestions (treated as manual choices)."""
    items = [{"transaction_id": i.transaction_id, "category_id": i.category_id} for i in payload.items]
    return {"applied": ai_service.apply_suggestions(db, items)}


@router.post(
    "/cloud-batch/prepare",
    response_model=CloudBatchPreview,
    dependencies=[Depends(_ai_guard)],
    responses={400: {"description": "Bad request"}, 502: {"description": "Upstream error"}},
)
def cloud_batch_prepare(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    scope: Annotated[str, Query(pattern="^(uncategorised|recheck)$")] = "uncategorised",
) -> dict:
    """Stage 1 of a cloud batch (spec §22.3, §22.5): preview the redacted payloads
    that *would* be sent. ``scope=recheck`` also includes auto-categorised rows
    (never manual) for re-processing. Nothing is sent yet."""
    try:
        return ai_service.cloud_batch_prepare(db, limit=limit, scope=scope, account_ids=_scope(request, db))
    except AIDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/cloud-batch/send",
    response_model=CloudBatchSendResult,
    dependencies=[Depends(_ai_guard)],
    responses={400: {"description": "Bad request"}, 502: {"description": "Upstream error"}},
)
def cloud_batch_send(payload: CloudBatchSendRequest, db: Annotated[Session, Depends(get_db)]) -> dict:
    """Stage 2 of a cloud batch: send the approved redacted requests, reject the
    rest, and return suggestions to review (apply via /api/ai/apply)."""
    try:
        return ai_service.cloud_batch_send(db, approve_ids=payload.approve_ids, reject_ids=payload.reject_ids)
    except AIDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
