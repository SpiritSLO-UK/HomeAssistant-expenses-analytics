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
from datetime import datetime, timezone

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
    approval_status: str, payload: dict, status: str,
) -> AIRequest:
    req = AIRequest(
        household_id=get_or_create_default_household(db).id,
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


def classify_transaction(db: Session, txn: Transaction, *, approved: bool = False, provider=None) -> dict:
    """Ask AI to suggest a category. Returns a suggestion (never applies it).

    For ``cloud_manual`` and ``approved=False``, returns ``status="approval_required"``
    with the exact redacted payload and a review item (spec §22.5)."""
    mode = settings_service.get_privacy_mode(db)
    if mode in OFF_MODES:
        raise AIDisabled(f"AI is disabled (privacy mode: {mode})")
    provider = provider or get_provider(db)
    if not provider.available():
        raise AIDisabled("No AI provider configured")

    is_cloud = mode in CLOUD_MODES
    cats = list(db.scalars(select(Category).where(Category.is_active.is_(True))).all())
    payload = {
        "description": txn.description_raw,
        "amount": str(txn.amount),
        "currency": txn.currency,
        "candidate_categories": [c.name for c in cats],
    }
    # The bytes that actually leave: redacted + minimal for cloud (spec §22.4).
    to_send = redaction.redact_for_cloud(payload) if is_cloud else payload

    if mode == "cloud_manual" and not approved:
        req = _audit(db, provider, mode, approval_status="pending", payload=to_send, status="pending")
        review_service.add(
            db, item_type="ai_request", item_id=req.id,
            reason="cloud_ai_approval_required", severity="warning",
            suggested_action="Approve sending this redacted payload to cloud AI.",
        )
        db.commit()
        return {"status": "approval_required", "ai_request_id": req.id, "payload": to_send}

    req = _audit(
        db, provider, mode,
        approval_status="not_required" if not is_cloud else "approved",
        payload=to_send, status="pending",
    )
    db.commit()

    try:
        result = provider.classify_transaction(
            description=to_send.get("description", ""),
            amount=str(to_send.get("amount", "")),
            currency=to_send.get("currency", ""),
            candidate_categories=to_send.get("candidate_categories", []),
        )
    except AIError as exc:
        req.status = "failed"
        req.error_message = str(exc)
        req.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise

    name = result.get("category")
    match = None
    if name:
        match = next((c for c in cats if c.name.strip().lower() == str(name).strip().lower()), None)

    req.status = "completed"
    req.response_payload = json.dumps(result)
    req.confidence_score = result.get("confidence")
    req.completed_at = datetime.now(timezone.utc)
    db.commit()

    # Suggestion only — the user accepts it via the normal categorise endpoint.
    return {
        "status": "ok",
        "ai_request_id": req.id,
        "category_id": match.id if match else None,
        "category_name": match.name if match else None,
        "confidence": result.get("confidence"),
        "rationale": result.get("rationale"),
    }


def list_requests(db: Session, limit: int = 50) -> list[AIRequest]:
    return list(
        db.scalars(select(AIRequest).order_by(AIRequest.created_at.desc()).limit(limit)).all()
    )
