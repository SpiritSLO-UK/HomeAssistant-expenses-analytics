"""AI gateway (spec §22).

The single place that decides whether AI may run, redacts cloud payloads, audits
every call (``AIRequest``, spec §22.6) and turns provider output into a
**suggestion** — it never writes a category itself (AI is not the source of
truth, spec §22.1, §43). Routing order is rules → vendor → keyword first; AI is
only invoked on explicit request.

Privacy gating (spec §7, §22):
- ``strict_local`` / ``no_ai`` → AI refused.
- ``local_llm``  → call the local endpoint, payload stays on-device (still minimal).
- ``cloud_manual`` → needs per-call approval; payload is redacted (spec §22.4).
- ``cloud_auto`` → call cloud automatically; payload is redacted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings as env_settings
from app.models import AIRequest, Category, Transaction
from app.services import redaction, review_service, settings_service
from app.services.ai_provider import AIError, AIProvider, NoAIProvider, OpenAICompatibleProvider
from app.services.household_service import get_or_create_default_household

CLOUD_MODES = {"cloud_manual", "cloud_auto"}
OFF_MODES = {"strict_local", "no_ai"}


class AIDisabled(RuntimeError):
    """AI is off or not configured for this privacy mode."""


def get_provider(db: Session) -> AIProvider:
    mode = settings_service.get_privacy_mode(db)
    if mode in OFF_MODES:
        return NoAIProvider()
    if settings_service.get(db, settings_service.AI_PROVIDER) != "openai_compatible":
        return NoAIProvider()
    base = settings_service.get(db, settings_service.AI_BASE_URL) or ""
    model = settings_service.get(db, settings_service.AI_MODEL) or ""
    if not base or not model:
        return NoAIProvider()
    return OpenAICompatibleProvider(
        base, model, api_key=env_settings.ai_api_key, timeout=env_settings.ai_timeout_seconds
    )


def status(db: Session) -> dict:
    mode = settings_service.get_privacy_mode(db)
    provider = get_provider(db)
    return {
        "privacy_mode": mode,
        "enabled": provider.available() and mode not in OFF_MODES,
        "is_cloud": mode in CLOUD_MODES,
        "provider": settings_service.get(db, settings_service.AI_PROVIDER),
        "base_url": settings_service.get(db, settings_service.AI_BASE_URL),
        "model": settings_service.get(db, settings_service.AI_MODEL),
        "configured": provider.available(),
        "has_api_key": bool(env_settings.ai_api_key),
    }


def _audit(
    db: Session, provider: AIProvider, mode: str, *,
    approval_status: str, payload: dict, status: str, transaction_id: int | None,
) -> AIRequest:
    req = AIRequest(
        household_id=get_or_create_default_household(db).id,
        transaction_id=transaction_id,
        provider=provider.name,
        model=getattr(provider, "model", None),
        task_type="classify_transaction",
        privacy_mode=mode,
        approval_status=approval_status,
        redacted_payload=json.dumps(payload),
        status=status,
    )
    db.add(req)
    db.flush()
    return req


def _candidate_categories(db: Session) -> list[Category]:
    return list(db.scalars(select(Category).where(Category.is_active.is_(True))).all())


def _suggest(req: AIRequest, result: dict, cats: list[Category]) -> dict:
    name = result.get("category")
    match = None
    if name:
        match = next((c for c in cats if c.name.strip().lower() == str(name).strip().lower()), None)
    return {
        "status": "ok",
        "ai_request_id": req.id,
        "transaction_id": req.transaction_id,
        "category_id": match.id if match else None,
        "category_name": match.name if match else None,
        "confidence": result.get("confidence"),
        "rationale": result.get("rationale"),
    }


def _run(db: Session, req: AIRequest, provider: AIProvider, payload: dict, cats: list[Category]) -> dict:
    """Call the provider and record the outcome on ``req`` (no commit). On a
    provider error, marks the request failed and re-raises."""
    try:
        result = provider.classify_transaction(
            description=payload.get("description", ""),
            amount=str(payload.get("amount", "")),
            currency=payload.get("currency", ""),
            candidate_categories=payload.get("candidate_categories", []),
        )
    except AIError as exc:
        req.status = "failed"
        req.error_message = str(exc)
        req.completed_at = datetime.now(UTC)
        raise
    req.status = "completed"
    req.response_payload = json.dumps(result)
    req.confidence_score = result.get("confidence")
    req.completed_at = datetime.now(UTC)
    return _suggest(req, result, cats)


def classify_transaction(db: Session, txn: Transaction, *, provider=None) -> dict:
    """Ask AI to suggest a category. Returns a suggestion (never applies it).

    ``cloud_manual`` always returns ``status="approval_required"`` with the exact
    redacted payload + a review item (spec §22.5) — call :func:`run_request` to
    actually send it once the user approves."""
    mode = settings_service.get_privacy_mode(db)
    if mode in OFF_MODES:
        raise AIDisabled(f"AI is disabled (privacy mode: {mode})")
    provider = provider or get_provider(db)
    if not provider.available():
        raise AIDisabled("No AI provider configured")

    is_cloud = mode in CLOUD_MODES
    # Sensitive-category blocking (spec §28): never send a never-cloud category
    # to a cloud provider.
    if is_cloud and txn.category_id is not None:
        cat = db.get(Category, txn.category_id)
        if cat is not None and cat.privacy_sensitivity == "never_cloud":
            raise AIDisabled("This transaction's category is marked never-cloud; cloud AI is blocked for it.")

    cats = _candidate_categories(db)
    payload = {
        "description": txn.description_raw,
        "amount": str(txn.amount),
        "currency": txn.currency,
        "candidate_categories": [c.name for c in cats],
    }
    # The bytes that actually leave: redacted + minimal for cloud (spec §22.4).
    to_send = redaction.redact_for_cloud(payload) if is_cloud else payload

    if mode == "cloud_manual":
        req = _audit(db, provider, mode, approval_status="pending", payload=to_send,
                     status="pending", transaction_id=txn.id)
        review_service.add(
            db, item_type="ai_request", item_id=req.id,
            reason="cloud_ai_approval_required", severity="warning",
            suggested_action="Approve sending this redacted payload to cloud AI.",
        )
        db.commit()
        return {"status": "approval_required", "ai_request_id": req.id,
                "transaction_id": txn.id, "payload": to_send}

    req = _audit(db, provider, mode,
                 approval_status="not_required" if not is_cloud else "approved",
                 payload=to_send, status="pending", transaction_id=txn.id)
    try:
        result = _run(db, req, provider, to_send, cats)
    except AIError:
        db.commit()
        raise
    db.commit()
    return result


def run_request(db: Session, ai_request: AIRequest, *, provider=None) -> dict:
    """Approve and send a pending request (spec §22.5). Stores the response,
    resolves its review item, and returns the suggestion."""
    if ai_request.status != "pending":
        raise AIDisabled("This AI request is not awaiting approval.")
    provider = provider or get_provider(db)
    if not provider.available():
        raise AIDisabled("No AI provider configured")
    ai_request.approval_status = "approved"
    payload = json.loads(ai_request.redacted_payload or "{}")
    try:
        result = _run(db, ai_request, provider, payload, _candidate_categories(db))
    except AIError:
        db.commit()
        raise
    review_service.resolve_for(db, item_type="ai_request", item_id=ai_request.id,
                               reason="cloud_ai_approval_required")
    db.commit()
    return result


def reject_request(db: Session, ai_request: AIRequest) -> AIRequest:
    """Reject a pending cloud request — nothing is sent (spec §22.5)."""
    ai_request.status = "rejected"
    ai_request.approval_status = "rejected"
    ai_request.completed_at = datetime.now(UTC)
    review_service.resolve_for(db, item_type="ai_request", item_id=ai_request.id,
                               reason="cloud_ai_approval_required")
    db.commit()
    return ai_request


def classify_batch(db: Session, *, limit: int = 25, provider=None) -> dict:
    """Suggest categories for many uncategorised transactions at once.

    **local_llm only** — keeps everything on-device. Auto-batching to a cloud
    provider would bypass the per-call approval cloud modes require, so it's
    refused. Returns suggestions; nothing is applied here (the user approves and
    applies via :func:`apply_suggestions`). Bounded by ``limit`` to cap LLM calls.
    """
    mode = settings_service.get_privacy_mode(db)
    if mode != "local_llm":
        raise AIDisabled("Batch AI categorisation runs in local_llm mode only (keeps data on-device).")
    provider = provider or get_provider(db)
    if not provider.available():
        raise AIDisabled("No AI provider configured")

    txns = _uncategorised_for_batch(db, limit)

    suggestions = []
    for txn in txns:
        try:
            res = classify_transaction(db, txn, provider=provider)
        except AIError:
            continue  # skip a failed item, keep going through the batch
        if res.get("status") == "ok" and res.get("category_id"):
            suggestions.append(
                {
                    "transaction_id": txn.id,
                    "description": txn.description_raw,
                    "amount": str(txn.amount),
                    "category_id": res["category_id"],
                    "category_name": res["category_name"],
                    "confidence": res.get("confidence"),
                    "rationale": res.get("rationale"),
                }
            )
    return {"considered": len(txns), "count": len(suggestions), "suggestions": suggestions}


def _uncategorised_for_batch(db: Session, limit: int) -> list[Transaction]:
    return list(
        db.scalars(
            select(Transaction)
            .where(
                Transaction.category_id.is_(None),
                Transaction.is_transfer.is_(False),
                Transaction.is_duplicate.is_(False),
                Transaction.is_split.is_(False),
            )
            .order_by(Transaction.transaction_date.desc())
            .limit(limit)
        ).all()
    )


def cloud_batch_prepare(db: Session, *, limit: int = 25, provider=None) -> dict:
    """Stage 1 of a cloud batch (spec §22.3, §22.5; backlog #154).

    Builds the **redacted** payload that *would* be sent for each uncategorised
    transaction and records a pending :class:`AIRequest` per item — but sends
    nothing. The user reviews the whole list (what leaves the device) and approves
    in one go via :func:`cloud_batch_send`. This is the batch sibling of the
    per-call ``cloud_manual`` approval flow; no per-item review-queue entries are
    created (the batch panel itself is the approval surface).
    """
    mode = settings_service.get_privacy_mode(db)
    if mode not in CLOUD_MODES:
        raise AIDisabled("Cloud batch needs a cloud privacy mode (cloud_manual / cloud_auto).")
    provider = provider or get_provider(db)
    if not provider.available():
        raise AIDisabled("No AI provider configured")

    cat_names = [c.name for c in _candidate_categories(db)]
    txns = _uncategorised_for_batch(db, limit)
    items = []
    for txn in txns:
        payload = {
            "description": txn.description_raw,
            "amount": str(txn.amount),
            "currency": txn.currency,
            "candidate_categories": cat_names,
        }
        to_send = redaction.redact_for_cloud(payload)
        req = _audit(db, provider, mode, approval_status="pending", payload=to_send,
                     status="pending", transaction_id=txn.id)
        items.append({
            "ai_request_id": req.id,
            "transaction_id": txn.id,
            "description": to_send.get("description", ""),
            "amount": str(txn.amount),
            "currency": txn.currency,
            "payload": to_send,
        })
    db.commit()
    return {"considered": len(txns), "count": len(items), "items": items}


def _batch_suggestion(db: Session, req: AIRequest, payload: dict, res: dict) -> dict:
    txn = db.get(Transaction, req.transaction_id) if req.transaction_id else None
    return {
        "transaction_id": req.transaction_id,
        "description": txn.description_raw if txn else payload.get("description", ""),
        "amount": str(txn.amount) if txn else payload.get("amount", ""),
        "category_id": res["category_id"],
        "category_name": res["category_name"],
        "confidence": res.get("confidence"),
        "rationale": res.get("rationale"),
    }


def _send_one_approved(
    db: Session, rid: int, provider: AIProvider, cats: list[Category],
    suggestions: list[dict], failed: list[int],
) -> None:
    """Send a single approved pending request; record its suggestion or failure."""
    req = db.get(AIRequest, rid)
    if req is None or req.status != "pending":
        return  # already sent/rejected, or unknown — skip defensively
    req.approval_status = "approved"
    payload = json.loads(req.redacted_payload or "{}")
    try:
        res = _run(db, req, provider, payload, cats)
    except AIError:
        failed.append(rid)
        return
    if res.get("category_id"):
        suggestions.append(_batch_suggestion(db, req, payload, res))


def _reject_pending(db: Session, rid: int) -> bool:
    req = db.get(AIRequest, rid)
    if req is not None and req.status == "pending":
        req.status = "rejected"
        req.approval_status = "rejected"
        req.completed_at = datetime.now(UTC)
        return True
    return False


def cloud_batch_send(
    db: Session, *, approve_ids: list[int], reject_ids: list[int] | None = None, provider=None
) -> dict:
    """Stage 2 of a cloud batch: send the **approved** pending requests to the
    cloud provider (redacted payload already stored + audited), reject the rest,
    and return suggestions to review. Still applies nothing — the user ticks and
    applies via :func:`apply_suggestions`."""
    provider = provider or get_provider(db)
    if not provider.available():
        raise AIDisabled("No AI provider configured")
    cats = _candidate_categories(db)

    suggestions: list[dict] = []
    failed: list[int] = []
    for rid in approve_ids:
        _send_one_approved(db, rid, provider, cats, suggestions, failed)

    rejected = sum(_reject_pending(db, rid) for rid in reject_ids or [])

    db.commit()
    return {"count": len(suggestions), "suggestions": suggestions, "failed": failed, "rejected": rejected}


def apply_suggestions(db: Session, items: list[dict]) -> int:
    """Apply user-approved AI category suggestions. Treated as a manual decision
    (confidence 1.0) — the user signed off, so rules won't later override it."""
    applied = 0
    for item in items:
        txn = db.get(Transaction, item["transaction_id"])
        category = db.get(Category, item["category_id"])
        if txn is None or category is None:
            continue
        txn.category_id = category.id
        txn.confidence_score = 1.0
        applied += 1
    db.commit()
    return applied


def list_requests(db: Session, limit: int = 50) -> list[AIRequest]:
    return list(
        db.scalars(select(AIRequest).order_by(AIRequest.created_at.desc()).limit(limit)).all()
    )
