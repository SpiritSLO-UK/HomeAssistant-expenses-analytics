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

import base64
import json
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings as env_settings
from app.db.session import SessionLocal
from app.logging import get_logger
from app.models import AIRequest, Category, Transaction
from app.services import ai_guard, redaction, review_service, settings_service
from app.services.ai_provider import AIError, AIProvider, NoAIProvider, OpenAICompatibleProvider
from app.services.household_service import get_or_create_default_household
from app.services.rule_service import MANUAL_CONFIDENCE

logger = get_logger("app.ai")

CLOUD_MODES = {"cloud_manual", "cloud_auto"}
BATCH_SCOPES = {"uncategorised", "recheck"}
OFF_MODES = {"strict_local", "no_ai"}

_NO_AI_PROVIDER = "No AI provider configured"
# Request ids currently being dispatched by a background cloud-batch worker. A
# second "send" for the same rows is filtered against this so a batch can't be
# re-triggered mid-run (double-send guard, on top of the per-item status check).
# In-memory is sufficient: a single backend process owns the (SQLite) DB, and
# nothing is ever in flight across a restart.
_inflight_batch_ids: set[int] = set()
# Hard cap on the raw image bytes we will send to a vision model. Mirrors the
# route-level upload cap (uploads.AI_IMAGE_MAX = 15 MB) so a direct service /
# API caller can't push an unbounded payload to the provider (SR-D1).
_MAX_IMAGE_BYTES = 15 * 1024 * 1024


class AIDisabled(RuntimeError):
    """AI is off or not configured for this privacy mode."""


class AIRateLimited(RuntimeError):
    """The per-user AI rate limit is exceeded. Carries ``retry_after`` seconds so
    the route can raise the same 429 (+Retry-After) the classify routes do (#29)."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(
            f"AI rate limit reached ({env_settings.ai_rate_limit_per_minute} requests/minute). "
            f"Try again in about {retry_after} second(s)."
        )


def _resolve_api_key(db: Session) -> str | None:
    """The AI API key to use. The environment (``HAFI_AI_API_KEY``) WINS; else the
    key stored (encrypted at rest) via the UI on a standalone instance (backlog
    #9). Never logged or surfaced — only its presence is reported."""
    return env_settings.ai_api_key or settings_service.get_ai_api_key(db)


def _key_source(db: Session) -> str:
    """Where the resolved AI key comes from: "env" | "stored" | "none"."""
    if env_settings.ai_api_key:
        return "env"
    if settings_service.has_stored_ai_api_key(db):
        return "stored"
    return "none"


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
        base,
        model,
        api_key=_resolve_api_key(db),
        timeout=env_settings.ai_timeout_seconds,
        # Cloud modes must talk to a public endpoint (SSRF / key-leak guard,
        # CR-SEC-3); local_llm may legitimately be on localhost/LAN.
        require_public_host=mode in CLOUD_MODES,
    )


def status(db: Session) -> dict:
    mode = settings_service.get_privacy_mode(db)
    provider = get_provider(db)
    source = _key_source(db)
    return {
        "privacy_mode": mode,
        "enabled": provider.available() and mode not in OFF_MODES,
        "is_cloud": mode in CLOUD_MODES,
        "provider": settings_service.get(db, settings_service.AI_PROVIDER),
        "base_url": settings_service.get(db, settings_service.AI_BASE_URL),
        "model": settings_service.get(db, settings_service.AI_MODEL),
        "configured": provider.available(),
        # True if EITHER the env var or a stored (encrypted) key is present. The
        # raw key is never included — only its presence + where it comes from.
        "has_api_key": source != "none",
        "key_source": source,
    }


def test_connection(db: Session) -> dict:
    """Validate the configured AI provider with a tiny synthetic request — a
    diagnostic for the Settings → AI card. Touches no real data and is NOT audited
    (it isn't a categorisation). Never raises: a provider error is reported as
    ``ok=False`` with the message."""
    mode = settings_service.get_privacy_mode(db)
    if mode in OFF_MODES:
        return {"ok": False, "reason": "off",
                "message": f"AI is off ({mode}). Pick a local or cloud mode first."}
    provider = get_provider(db)
    if not provider.available():
        return {"ok": False, "reason": "not_configured",
                "message": "Not configured — set the provider, base URL and model (and an API key for cloud)."}
    try:
        result = provider.classify_transaction(
            description="Tesco groceries weekly shop",
            amount="-42.50",
            currency=settings_service.get_base_currency(db),
            candidate_categories=["Groceries", "Transport", "Eating out"],
        )
    except AIError as exc:
        return {"ok": False, "reason": "error", "message": str(exc)}
    model = getattr(provider, "model", None)
    return {
        "ok": True,
        "reason": "ok",
        "message": f"Connected to {provider.name}" + (f" · model {model}" if model else ""),
        "sample_category": result.get("category"),
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


def _valid_country(value: object) -> str | None:
    """Accept only a clean ISO-3166-1 alpha-2 code from the model; ignore prose,
    'null', full country names, etc. (folds country into the ✨ suggest, Feat)."""
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if len(code) == 2 and code.isalpha() else None


def _valid_vendor(value: object) -> str | None:
    """Accept a clean vendor name from the model; drop empty / 'null' / overlong."""
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or name.lower() in {"null", "none"}:
        return None
    return name[:120]


def _match_category_name(name: object, cats: list[Category]) -> Category | None:
    """Resolve a model-returned category name to a candidate Category (exact,
    case-insensitive), or None."""
    if not name:
        return None
    target = str(name).strip().lower()
    return next((c for c in cats if c.name.strip().lower() == target), None)


def _suggest(req: AIRequest, result: dict, cats: list[Category]) -> dict:
    match = _match_category_name(result.get("category"), cats)
    return {
        "status": "ok",
        "ai_request_id": req.id,
        "transaction_id": req.transaction_id,
        "category_id": match.id if match else None,
        "category_name": match.name if match else None,
        "confidence": result.get("confidence"),
        "rationale": result.get("rationale"),
        "country": _valid_country(result.get("country")),
        "vendor": _valid_vendor(result.get("vendor")),
    }


def _run(req: AIRequest, provider: AIProvider, payload: dict, cats: list[Category]) -> dict:
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


def _never_cloud_category_reason(db: Session, txn: Transaction) -> str | None:
    """Block reason if ``txn`` is categorised into a never-cloud category (spec
    §28.4), else None. An uncategorised row has no category, so this returns
    None — the ``contains_sensitive`` text gate covers those (CR-SEC-10)."""
    if txn.category_id is None:
        return None
    cat = db.get(Category, txn.category_id)
    if cat is not None and cat.privacy_sensitivity == "never_cloud":
        return "This transaction's category is marked never-cloud; cloud AI is blocked for it."
    return None


def _cloud_block_reason(db: Session, txn: Transaction, mode: str) -> str | None:
    """Decide whether ``txn`` may be sent to cloud AI under ``mode``.

    Fires **regardless of whether the row is categorised** (CR-SEC-10 + SR-D1):
    the previous gate only ran the never-cloud check for already-categorised rows,
    so an *uncategorised* transaction — the main AI target — bypassed it entirely.
    Now a never-cloud category always blocks, and under an *automatic* cloud mode a
    transaction whose raw text still looks sensitive (``redaction.contains_sensitive``)
    is refused rather than auto-sent — the user must route it through the manual
    approval path instead."""
    if mode not in CLOUD_MODES:
        return None
    reason = _never_cloud_category_reason(db, txn)
    if reason:
        return reason
    if mode == "cloud_auto" and redaction.contains_sensitive(txn.description_raw):
        return (
            "This transaction's text still looks sensitive after redaction checks; "
            "cloud_auto would send it automatically — approve it manually (cloud_manual) instead."
        )
    return None


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
        raise AIDisabled(_NO_AI_PROVIDER)

    is_cloud = mode in CLOUD_MODES
    # Sensitive blocking (spec §28): never auto-send never-cloud categories or
    # obviously-sensitive uncategorised rows to a cloud provider.
    block = _cloud_block_reason(db, txn, mode)
    if block:
        raise AIDisabled(block)

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
        result = _run(req, provider, to_send, cats)
    except AIError:
        db.commit()
        raise
    db.commit()
    return result


def _staged_request_block_reason(db: Session, ai_request: AIRequest) -> str | None:
    """Re-validate a staged request against the CURRENT privacy mode at send time,
    returning a block reason (or None to proceed).

    A payload staged earlier must not be flushed after the user changed mode, and
    the target category may since have been marked never-cloud (SR-D1). Shared by
    the single-request send (:func:`run_request`) and the batch send
    (:func:`_send_one_approved`) so both paths stay in lockstep (#19)."""
    mode = settings_service.get_privacy_mode(db)
    if mode in OFF_MODES:
        return f"AI is disabled (privacy mode: {mode}); refusing to send this stored request."
    if ai_request.privacy_mode in CLOUD_MODES and mode not in CLOUD_MODES:
        return (
            "This request was prepared for a cloud mode, but the current privacy "
            "mode no longer permits cloud AI — refusing to send it."
        )
    if mode in CLOUD_MODES and ai_request.transaction_id is not None:
        txn = db.get(Transaction, ai_request.transaction_id)
        return _never_cloud_category_reason(db, txn) if txn is not None else None
    return None


def run_request(db: Session, ai_request: AIRequest, *, provider=None) -> dict:
    """Approve and send a pending request (spec §22.5). Stores the response,
    resolves its review item, and returns the suggestion."""
    if ai_request.status != "pending":
        raise AIDisabled("This AI request is not awaiting approval.")
    reason = _staged_request_block_reason(db, ai_request)
    if reason:
        raise AIDisabled(reason)
    provider = provider or get_provider(db)
    if not provider.available():
        raise AIDisabled(_NO_AI_PROVIDER)
    ai_request.approval_status = "approved"
    payload = json.loads(ai_request.redacted_payload or "{}")
    try:
        result = _run(ai_request, provider, payload, _candidate_categories(db))
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


def classify_batch(
    db: Session, *, limit: int = 25, provider=None, account_ids: set[int] | None = None,
    scope: str = "uncategorised",
) -> dict:
    """Suggest categories for many transactions at once.

    **local_llm only** — keeps everything on-device. Auto-batching to a cloud
    provider would bypass the per-call approval cloud modes require, so it's
    refused. Returns suggestions; nothing is applied here (the user approves and
    applies via :func:`apply_suggestions`). Bounded by ``limit`` to cap LLM calls.
    ``scope="recheck"`` also re-examines already auto-categorised rows (never
    manual ones) and only surfaces a suggestion when it *differs* from the current
    category — so re-running finds new stuff instead of repeating what's there.
    """
    mode = settings_service.get_privacy_mode(db)
    if mode != "local_llm":
        raise AIDisabled("Batch AI categorisation runs in local_llm mode only (keeps data on-device).")
    provider = provider or get_provider(db)
    if not provider.available():
        raise AIDisabled(_NO_AI_PROVIDER)

    txns = _select_for_batch(db, limit, scope=scope, account_ids=account_ids)
    done = _already_ai_processed(db, [t.id for t in txns])  # before this run

    suggestions = []
    for txn in txns:
        try:
            res = classify_transaction(db, txn, provider=provider)
        except AIError:
            continue  # skip a failed item, keep going through the batch
        except Exception:  # noqa: BLE001 - one bad item must not kill the batch (SR-D1)
            logger.exception("classify_batch: unexpected error on transaction %s", txn.id)
            db.rollback()  # drop this item's uncommitted audit row; keep committed successes
            continue
        # Surface only a *new* category — re-checking shouldn't repeat the one already set.
        if res.get("status") == "ok" and res.get("category_id") and res["category_id"] != txn.category_id:
            suggestions.append(
                {
                    "transaction_id": txn.id,
                    "description": txn.description_raw,
                    "amount": str(txn.amount),
                    "category_id": res["category_id"],
                    "category_name": res["category_name"],
                    "confidence": res.get("confidence"),
                    "rationale": res.get("rationale"),
                    "already_ai_processed": txn.id in done,
                }
            )
    return {"considered": len(txns), "count": len(suggestions), "suggestions": suggestions}


def _already_ai_processed(db: Session, txn_ids: list[int]) -> set[int]:
    """Transaction ids that already have a **completed** AIRequest — so the batch
    UI can flag them and let the user skip re-sending (saves cloud cost + privacy)."""
    ids = [t for t in txn_ids if t is not None]
    if not ids:
        return set()
    rows = db.scalars(
        select(AIRequest.transaction_id).where(
            AIRequest.transaction_id.in_(ids), AIRequest.status == "completed"
        )
    ).all()
    return {t for t in rows if t is not None}


# --- vision image extraction (opt-in fallback when OCR finds nothing) --------
#
# Sends the *image* to a vision model. Unlike classify/batch, the payload is an
# image, so redaction can't apply — the FE warns per send (with a dismissible
# tick). Gated on AI being on + a provider configured. Every send is audited
# (the image isn't stored in the audit row, only a note that one was sent).

_STATEMENT_VISION_SYSTEM = (
    "You read a photo or scan of a bank/card statement and extract its transactions. "
    'Respond ONLY with JSON: {"transactions": [{"date": "YYYY-MM-DD", '
    '"description": "<text>", "amount": "<signed number, negative for money out>"}]}. '
    "No prose, no code fences."
)
_RECEIPT_VISION_SYSTEM = (
    "You read a photo of a purchase receipt and extract its summary. Respond ONLY with "
    'JSON: {"merchant": "<name>", "date": "YYYY-MM-DD or null", "total": "<number>", '
    '"currency": "<ISO code or null>", "category": "<one of the provided categories or null>"}. '
    "No prose, no code fences."
)


def _require_vision(db: Session, *, approved: bool = False, user_id: int | None = None) -> tuple[AIProvider, str]:
    mode = settings_service.get_privacy_mode(db)
    if mode in OFF_MODES:
        raise AIDisabled("AI is off — enable a local or cloud mode to extract images.")
    # The image-extract routes live outside routes_ai's abuse guard, so enforce
    # the same abuse guards here too, before any provider dispatch — EXCEPT the
    # 100 KB payload cap, which would 413 a legitimate 15 MB image (that has its
    # own separate 15 MB limit via _check_image_size). The per-user rate limit and
    # the daily budget still apply (see services/ai_guard.py; #29).
    if user_id is not None:
        wait = ai_guard.rate_limit_wait_seconds(user_id)
        if wait > 0:
            raise AIRateLimited(wait)
    budget = ai_guard.daily_cap_reached(db)
    if budget is not None:
        raise AIDisabled(ai_guard.daily_budget_message(*budget))
    # A raw statement/receipt image can't be redacted, so an automatic cloud mode
    # would leak the whole image on a frontend-bypassable call. Refuse cloud_auto
    # unless this specific request was explicitly approved (CR-SEC-10).
    if mode == "cloud_auto" and not approved:
        raise AIDisabled(
            "Vision image-extract won't auto-send a raw image to cloud AI in cloud_auto "
            "(the image can't be redacted) — approve the send explicitly to proceed."
        )
    provider = get_provider(db)
    if not provider.available():
        raise AIDisabled(_NO_AI_PROVIDER)
    return provider, mode


def _check_image_size(content: bytes) -> None:
    """Refuse an over-large image before it reaches the provider (SR-D1)."""
    if len(content) > _MAX_IMAGE_BYTES:
        raise AIDisabled(
            f"Image is too large for vision AI ({len(content)} bytes > {_MAX_IMAGE_BYTES})."
        )


def _audit_image(db: Session, provider: AIProvider, mode: str, *, kind: str, size: int) -> AIRequest:
    req = AIRequest(
        household_id=get_or_create_default_household(db).id,
        provider=provider.name,
        model=getattr(provider, "model", None),
        task_type=f"extract_image_{kind}",
        privacy_mode=mode,
        approval_status="not_required",
        # An image can't be redacted — record only that one was sent, never the image.
        redacted_payload=json.dumps({"image_bytes": size, "note": "image sent to AI (not redactable)"}),
        status="pending",
    )
    db.add(req)
    db.flush()
    return req


def _run_image(db: Session, req: AIRequest, provider: AIProvider, content: bytes, mime: str,
               *, system: str, instruction: str) -> dict:
    image_b64 = base64.b64encode(content).decode("ascii")
    try:
        result = provider.extract_from_image(image_b64, mime, system=system, instruction=instruction)
    except AIError as exc:
        req.status = "failed"
        req.error_message = str(exc)
        req.completed_at = datetime.now(UTC)
        db.commit()
        raise
    # Validate the shape before any caller does result.get(...) on it (SR-D1).
    if not isinstance(result, dict):
        req.status = "failed"
        req.error_message = "AI vision response was not a JSON object"
        req.completed_at = datetime.now(UTC)
        db.commit()
        raise AIError("AI vision response was not a JSON object.")
    req.status = "completed"
    req.completed_at = datetime.now(UTC)
    db.commit()
    return result


def extract_statement_image(
    db: Session, content: bytes, mime: str, *, approved: bool = False, user_id: int | None = None
) -> list[dict]:
    """Vision-extract statement transactions from an image. Returns a list of
    ``{date, description, amount}`` dicts (the route turns them into an import).

    ``approved`` must be True to run under ``cloud_auto`` (the raw image can't be
    redacted, so an automatic cloud send needs explicit per-request approval).
    ``user_id`` enforces the per-user AI rate limit (#29)."""
    _check_image_size(content)
    provider, mode = _require_vision(db, approved=approved, user_id=user_id)
    req = _audit_image(db, provider, mode, kind="statement", size=len(content))
    result = _run_image(db, req, provider, content, mime,
                        system=_STATEMENT_VISION_SYSTEM, instruction="Extract every transaction in this statement.")
    txns = result.get("transactions")
    if not isinstance(txns, list):
        return []
    # Keep only well-shaped rows — the model can return junk / non-dict entries.
    return [t for t in txns if isinstance(t, dict)]


def extract_receipt_image(
    db: Session, content: bytes, mime: str, *, approved: bool = False, user_id: int | None = None
) -> dict:
    """Vision-extract a receipt's merchant/date/total/currency from an image, and —
    in the *same* call — a suggested category (backlog #110). The candidate
    category names are listed in the instruction; the returned name is resolved to
    a category id (``category_id``/``category_name``, None when unmatched) so the
    transaction matched to / created from this receipt can reuse it instead of a
    separate AI classification call.

    ``approved`` must be True to run under ``cloud_auto`` (see
    :func:`extract_statement_image`). ``user_id`` enforces the per-user AI rate
    limit (#29)."""
    _check_image_size(content)
    provider, mode = _require_vision(db, approved=approved, user_id=user_id)
    cats = _candidate_categories(db)
    names = ", ".join(c.name for c in cats)
    instruction = (
        "Extract this receipt's summary. For \"category\", choose the single best match "
        f"from this list, or null if none fit: {names}."
    )
    req = _audit_image(db, provider, mode, kind="receipt", size=len(content))
    result = _run_image(db, req, provider, content, mime,
                        system=_RECEIPT_VISION_SYSTEM, instruction=instruction)
    match = _match_category_name(result.get("category"), cats)
    result["category_id"] = match.id if match else None
    result["category_name"] = match.name if match else None
    return result


def _select_for_batch(
    db: Session, limit: int, *, scope: str = "uncategorised", account_ids: set[int] | None = None
) -> list[Transaction]:
    """Pick transactions for an AI batch.

    ``scope="uncategorised"`` (default) → only rows with no category.
    ``scope="recheck"`` → re-process candidates: uncategorised **and** anything
    auto-categorised (rule/vendor/keyword/AI, confidence < 1.0), so the user can
    re-run AI after plugging in a model to find new/better categories — but
    **never** a manual choice (confidence 1.0 is left untouched)."""
    from app.services.scope import account_scope_condition, archived_condition

    conditions = [
        Transaction.is_transfer.is_(False),
        Transaction.is_duplicate.is_(False),
        Transaction.is_split.is_(False),
        *account_scope_condition(account_ids),
        *archived_condition(),
    ]
    if scope == "recheck":
        conditions.append(
            or_(Transaction.confidence_score.is_(None), Transaction.confidence_score < MANUAL_CONFIDENCE)
        )
    else:
        conditions.append(Transaction.category_id.is_(None))

    return list(
        db.scalars(
            select(Transaction)
            .where(*conditions)
            .order_by(Transaction.transaction_date.desc())
            .limit(limit)
        ).all()
    )


def cloud_batch_prepare(
    db: Session, *, limit: int = 25, provider=None, account_ids: set[int] | None = None,
    scope: str = "uncategorised",
) -> dict:
    """Stage 1 of a cloud batch (spec §22.3, §22.5; backlog #154).

    Builds the **redacted** payload that *would* be sent for each candidate
    transaction and records a pending :class:`AIRequest` per item — but sends
    nothing. The user reviews the whole list (what leaves the device) and approves
    in one go via :func:`cloud_batch_send`. This is the batch sibling of the
    per-call ``cloud_manual`` approval flow; no per-item review-queue entries are
    created (the batch panel itself is the approval surface). ``scope="recheck"``
    also includes already auto-categorised rows (never manual) for re-processing.
    """
    mode = settings_service.get_privacy_mode(db)
    if mode not in CLOUD_MODES:
        raise AIDisabled("Cloud batch needs a cloud privacy mode (cloud_manual / cloud_auto).")
    provider = provider or get_provider(db)
    if not provider.available():
        raise AIDisabled(_NO_AI_PROVIDER)

    cat_names = [c.name for c in _candidate_categories(db)]
    txns = _select_for_batch(db, limit, scope=scope, account_ids=account_ids)
    done = _already_ai_processed(db, [t.id for t in txns])  # completed before this batch
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
            "already_ai_processed": txn.id in done,
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
    # Re-validate at send time exactly as run_request does — a payload staged
    # before the user turned AI off / left cloud mode, or whose category is now
    # never-cloud, must be refused here too rather than dispatched (#19).
    reason = _staged_request_block_reason(db, req)
    if reason:
        req.status = "failed"
        req.error_message = reason
        req.completed_at = datetime.now(UTC)
        failed.append(rid)
        return
    req.approval_status = "approved"
    payload = json.loads(req.redacted_payload or "{}")
    try:
        res = _run(req, provider, payload, cats)
    except AIError:
        failed.append(rid)
        return
    except Exception:  # noqa: BLE001 - one bad item must not kill the batch (SR-D1)
        logger.exception("cloud_batch_send: unexpected error on request %s", rid)
        req.status = "failed"
        req.error_message = "unexpected error"
        req.completed_at = datetime.now(UTC)
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
        raise AIDisabled(_NO_AI_PROVIDER)
    cats = _candidate_categories(db)

    suggestions: list[dict] = []
    failed: list[int] = []
    for rid in approve_ids:
        _send_one_approved(db, rid, provider, cats, suggestions, failed)

    rejected = sum(_reject_pending(db, rid) for rid in reject_ids or [])

    db.commit()
    return {"count": len(suggestions), "suggestions": suggestions, "failed": failed, "rejected": rejected}


# --- non-blocking cloud batch send (background worker + polled status) ---------
#
# The synchronous ``cloud_batch_send`` above dispatches every approved request in
# one request/response, which blocks the UI for a long batch. The pair below runs
# the same per-item dispatch in the BACKGROUND instead: ``cloud_batch_start``
# queues the approved ids (rejecting the rest synchronously) and returns at once;
# ``run_cloud_batch`` is scheduled to send them one-by-one; the FE polls
# ``cloud_batch_status`` (derived from the AIRequest rows — no new column) until
# ``done`` and then reviews the suggestions it carries.


def _dispatchable_ids(db: Session, approve_ids: list[int]) -> list[int]:
    """The subset of ``approve_ids`` sendable right now: a still-``pending``
    request that isn't already being dispatched by a running worker (double-send
    guard). De-duplicated, order-preserving."""
    seen: set[int] = set()
    out: list[int] = []
    for rid in approve_ids:
        if rid in seen or rid in _inflight_batch_ids:
            continue
        seen.add(rid)
        req = db.get(AIRequest, rid)
        if req is not None and req.status == "pending":
            out.append(rid)
    return out


def cloud_batch_start(
    db: Session, *, approve_ids: list[int], reject_ids: list[int] | None = None
) -> dict:
    """Stage 2, non-blocking: queue the approved requests for a BACKGROUND send
    and reject the rest, returning immediately with how many were queued.

    Nothing is dispatched here — the caller schedules :func:`run_cloud_batch` on
    the returned ``queue`` and the FE polls :func:`cloud_batch_status`. Rejecting
    is cheap (no network) so it happens synchronously. The queued ids are reserved
    in an in-flight set before returning, so a second call can't re-dispatch the
    same rows while the worker is running."""
    provider = get_provider(db)
    if not provider.available():
        raise AIDisabled(_NO_AI_PROVIDER)
    queue = _dispatchable_ids(db, approve_ids)
    _inflight_batch_ids.update(queue)  # reserve before returning (double-send guard)
    rejected = sum(_reject_pending(db, rid) for rid in reject_ids or [])
    db.commit()
    return {"queued": len(queue), "rejected": rejected, "queue": queue}


def run_cloud_batch(request_ids: list[int], *, provider: AIProvider | None = None) -> None:
    """Background worker: dispatch each approved-pending request SEQUENTIALLY, but
    on a FRESH short-lived session per item (committing per item), so one DB
    connection is never pinned for the whole batch — SQLite's pool is bounded
    (``db/session``). Wrapped so a failure on one item, or the run as a whole, can
    never bubble up to crash the app; each outcome is recorded on its AIRequest
    row and read back via :func:`cloud_batch_status`.

    The per-item send re-runs every guard the synchronous path applies (the
    send-time never-cloud / mode re-validation via ``_send_one_approved``); the
    route-level rate-limit / daily-budget guards ran when the send was queued."""
    try:
        for rid in request_ids:
            try:
                with SessionLocal() as db:
                    prov = provider or get_provider(db)
                    # Throwaway result lists: outcomes live on the AIRequest rows.
                    _send_one_approved(db, rid, prov, _candidate_categories(db), [], [])
                    db.commit()
            except Exception:  # noqa: BLE001 - isolate one item; keep the batch going
                logger.exception("run_cloud_batch: error dispatching request %s", rid)
            finally:
                _inflight_batch_ids.discard(rid)
    except Exception:  # noqa: BLE001 - a background task must never crash the server
        logger.exception("run_cloud_batch: unexpected failure")
    finally:
        # Belt-and-braces: clear any ids still reserved (e.g. if the loop raised
        # before reaching them) so a later batch isn't blocked.
        _inflight_batch_ids.difference_update(request_ids)


def _batch_status_counts(db: Session, ids: list[int]) -> dict[str, int]:
    """AIRequest status → count for the rows named by ``ids`` (one grouped COUNT)."""
    rows = db.execute(
        select(AIRequest.status, func.count())
        .where(AIRequest.id.in_(ids))
        .group_by(AIRequest.status)
    ).all()
    return {status: int(n) for status, n in rows}


def _suggestions_for(db: Session, ids: list[int]) -> list[dict]:
    """Rebuild the review-stage suggestions from the COMPLETED requests among
    ``ids`` — the async send stores each outcome on its AIRequest row, so the FE
    review/apply stage is unchanged from the old synchronous return. Skips a
    completed row whose category no longer resolves (same rule the send path
    applied: only surface a matched category)."""
    cats = _candidate_categories(db)
    rows = db.scalars(
        select(AIRequest).where(AIRequest.id.in_(ids), AIRequest.status == "completed")
    ).all()
    out: list[dict] = []
    for req in rows:
        result = json.loads(req.response_payload or "{}")
        match = _match_category_name(result.get("category"), cats)
        if match is None:
            continue
        txn = db.get(Transaction, req.transaction_id) if req.transaction_id else None
        out.append({
            "transaction_id": req.transaction_id,
            "description": txn.description_raw if txn else "",
            "amount": str(txn.amount) if txn else "",
            "category_id": match.id,
            "category_name": match.name,
            "confidence": result.get("confidence"),
            "rationale": result.get("rationale"),
        })
    return out


def cloud_batch_status(db: Session, ids: list[int]) -> dict:
    """Progress of a cloud batch, derived purely from the AIRequest rows named by
    ``ids`` (the ids the FE queued) — a couple of COUNT queries, cheap enough to
    poll. ``done`` is true once none are still pending; the ``suggestions`` (built
    from the completed rows) are populated only then so the FE moves straight to
    the review/apply stage."""
    ids = list(dict.fromkeys(ids))  # de-dupe, keep order
    if not ids:
        return {"total": 0, "sent": 0, "pending": 0, "failed": 0,
                "rejected": 0, "done": True, "running": False, "suggestions": []}
    counts = _batch_status_counts(db, ids)
    sent = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    pending = counts.get("pending", 0)
    rejected = counts.get("rejected", 0)
    done = pending == 0
    running = any(rid in _inflight_batch_ids for rid in ids)
    suggestions = _suggestions_for(db, ids) if done else []
    return {"total": sent + failed + pending + rejected, "sent": sent, "pending": pending,
            "failed": failed, "rejected": rejected, "done": done, "running": running,
            "suggestions": suggestions}


def apply_suggestions(db: Session, items: list[dict], *, account_ids: set[int] | None = None) -> int:
    """Apply user-approved AI category suggestions. Treated as a manual decision
    (confidence 1.0) — the user signed off, so rules won't later override it.

    ``account_ids`` scopes the write to the caller's visible accounts (``None`` =
    unrestricted owner/admin): a transaction in an out-of-scope account is skipped,
    never written — the same visibility guard every sibling write path applies (#17)."""
    applied = 0
    for item in items:
        txn = db.get(Transaction, item["transaction_id"])
        category = db.get(Category, item["category_id"])
        if txn is None or category is None:
            continue
        # Out-of-scope account → skip (never write). Orphan rows (account_id None)
        # are owner-only, matching account_scope_condition (SR-E7).
        if account_ids is not None and txn.account_id is not None and txn.account_id not in account_ids:
            continue
        # Never overwrite a manual choice — even on a re-process (spec §15.1).
        if txn.confidence_score is not None and txn.confidence_score >= MANUAL_CONFIDENCE:
            continue
        txn.category_id = category.id
        txn.confidence_score = 1.0
        # Categorising clears the queue items a missing category caused, so a bulk
        # apply resolves them just like the per-row "categorise" action does.
        review_service.resolve_for(db, item_type="transaction", item_id=txn.id, reason="unknown_category")
        review_service.resolve_for(db, item_type="transaction", item_id=txn.id, reason="unknown_vendor")
        applied += 1
    db.commit()
    return applied


def list_requests(db: Session, limit: int = 50, *, include_archived: bool = False) -> list[AIRequest]:
    stmt = select(AIRequest).order_by(AIRequest.created_at.desc())
    if not include_archived:
        stmt = stmt.where(AIRequest.archived_at.is_(None))
    return list(db.scalars(stmt.limit(limit)).all())
