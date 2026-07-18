"""Paperless-ngx document import (spec §21; backlog: import from Paperless).

**One-directional and outbound only.** We *request* documents FROM the user's own
Paperless-ngx instance and pull them into the receipts pipeline; Paperless never
gets access to our finance data, and we never push anything to it. The only thing
that leaves the box is our API token (to authenticate the pull) and a document id.

The base **URL** is editable in Settings → Integrations (non-secret), falling back
to ``HAFI_PAPERLESS_URL``. The **token** is a secret and stays env-only
(``HAFI_PAPERLESS_TOKEN``), handled like the AI key — storing it in the DB would
need at-rest encryption (deferred #15). The feature is off unless both are set.

An imported document is de-duplicated by content hash (``receipt_service.store_upload``)
and then OCR'd if OCR is enabled, exactly like an uploaded receipt — so a
re-import never creates a duplicate. A re-import of a document that was pulled in
*before* the OCR engine was available (so its receipt still has no OCR text) also
re-runs OCR now that the engine is present, without re-downloading or duplicating.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings as env_settings
from app.logging import get_logger
from app.services import receipt_service, settings_service

logger = get_logger(__name__)

_TIMEOUT = 15.0

# Bounded retry for transient upstream blips (connect/read timeouts, 429/5xx),
# mirroring the price/AI-provider policy (#356): small and capped so a flaky
# moment recovers without hammering Paperless. Unlike the best-effort FX/price
# fetches, listing/importing is interactive, so once retries are exhausted (or a
# permanent 4xx occurs) we re-raise for the caller to surface a clear message.
_RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 0.5

# OCR statuses that mean "no usable OCR text yet" — a receipt in one of these is
# eligible for (re-)OCR on re-import once OCR is enabled and the engine is present
# (fixes ~137 documents imported before an OCR engine was installed). "processing"
# and "processed" are excluded so we never race or redo a completed extraction.
_OCR_INCOMPLETE = frozenset({"not_processed", "skipped", "failed"})
_MB = 1024 * 1024
# Download cap for a pulled document — matches the receipts upload cap (15 MB,
# routes_receipts.MAX_BYTES) so Paperless imports can't smuggle in something the
# normal upload path would reject, and can't be buffered unbounded (CR-SEC-8).
_MAX_DOWNLOAD_BYTES = 15 * _MB
_EXT_BY_TYPE = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
}
# Extensions we're willing to derive from (untrusted) filename metadata.
_ALLOWED_EXTS = set(_EXT_BY_TYPE.values())
# Neutral extension for bytes we can't confidently classify — never mislabel an
# arbitrary blob as ``.pdf``.
_GENERIC_EXT = ".bin"


def effective_url(db: Session) -> str:
    """The base URL in effect — Settings value, else the env fallback, else ""."""
    return settings_service.get_paperless_url(db)


def is_configured(db: Session) -> bool:
    return bool(effective_url(db)) and bool(env_settings.paperless_token)


def status(db: Session) -> dict:
    url = effective_url(db)
    stored = (settings_service.get(db, settings_service.PAPERLESS_URL) or "").strip()
    env_url = (env_settings.paperless_url or "").strip()
    if stored:
        url_source = "settings"
    elif env_url:
        url_source = "env"
    else:
        url_source = None
    return {
        "configured": is_configured(db),
        "url": url or None,
        "url_source": url_source,
        "token_present": bool(env_settings.paperless_token),
    }


def _require_config(db: Session) -> str:
    url = effective_url(db)
    if not url or not env_settings.paperless_token:
        raise ValueError(
            "Paperless is not configured (set the URL in Settings → Integrations and the "
            "HAFI_PAPERLESS_TOKEN env var)."
        )
    return url.rstrip("/")


def _is_transient(exc: Exception) -> bool:
    """Whether an httpx error is worth retrying: a transport drop/timeout, or a
    server-side / rate-limit status (429/5xx). Permanent 4xx fail fast."""
    import httpx

    if isinstance(exc, httpx.TransportError):  # connect/read timeouts, drops
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRY_STATUS
    return False


def _with_retry(op, label: str):
    """Run an httpx call ``op`` with a small bounded retry on transient errors,
    then re-raise the last error so an interactive import/list still surfaces a
    clear message. Non-transient errors (4xx, parse) propagate immediately."""
    import httpx

    for attempt in range(_MAX_ATTEMPTS):
        try:
            return op()
        except httpx.HTTPError as exc:
            if not _is_transient(exc) or attempt + 1 >= _MAX_ATTEMPTS:
                raise
            logger.warning(
                "Paperless %s transient error (attempt %d/%d): %s",
                label, attempt + 1, _MAX_ATTEMPTS, exc,
            )
            time.sleep(_BACKOFF_BASE * (2**attempt))
    return None  # unreachable: the loop always returns or raises


def _get(db: Session, path: str, **kwargs):
    """Authenticated GET against the Paperless API. Raises on transport/HTTP error
    so the caller can surface a clear message (unlike the best-effort FX/price
    fetches, listing/importing is interactive and the user wants feedback).
    Transient failures (timeout/connect drop, 429/5xx) are retried first (#356)."""
    import httpx  # local import so the dependency is only needed when used

    base = _require_config(db)
    headers = {"Authorization": f"Token {env_settings.paperless_token}"}

    def op():
        # follow_redirects=False (CR-SEC-3): a self-hosted Paperless is legitimately
        # on the LAN, so we don't block private hosts — but we must never follow a
        # redirect, which could bounce our API token to a different (attacker) host.
        resp = httpx.get(
            f"{base}{path}", headers=headers, timeout=_TIMEOUT, follow_redirects=False, **kwargs
        )
        resp.raise_for_status()
        return resp

    return _with_retry(op, f"GET {path}")


def test_connection(db: Session) -> dict:
    """Verify the URL + token reach Paperless (a cheap 1-document query). Raises
    ValueError when unconfigured, or httpx.HTTPError on a transport/HTTP failure."""
    _get(db, "/api/documents/", params={"page_size": 1})
    return {"ok": True, "url": effective_url(db)}


def list_documents(db: Session, *, query: str | None = None, limit: int = 25) -> list[dict]:
    """Recent documents from Paperless (newest first), optionally full-text filtered."""
    limit = max(1, min(100, limit))
    params: dict[str, object] = {"ordering": "-created", "page_size": limit}
    if query:
        params["query"] = query
    data = _get(db, "/api/documents/", params=params).json()
    out = []
    for d in data.get("results", []):
        out.append({
            "id": d.get("id"),
            "title": d.get("title") or f"Document {d.get('id')}",
            "created": d.get("created"),
        })
    return out


def _ext_from_filename(name: str | None) -> str | None:
    """Extension from a (untrusted) filename, but only if it's one we accept."""
    if not name:
        return None
    suffix = Path(name).suffix.lower()
    if suffix == ".jpeg":
        suffix = ".jpg"
    return suffix if suffix in _ALLOWED_EXTS else None


def _resolve_extension(content_type: str, meta: dict) -> str:
    """Choose a file extension without blindly trusting the content-type header.

    Prefer an explicit content-type mapping; if the type is unknown or missing,
    fall back to the Paperless filename metadata; otherwise use a neutral
    extension so arbitrary bytes are never mislabelled as a PDF."""
    if content_type in _EXT_BY_TYPE:
        return _EXT_BY_TYPE[content_type]
    for key in ("archived_file_name", "original_file_name", "title"):
        ext = _ext_from_filename(meta.get(key))
        if ext:
            return ext
    return _GENERIC_EXT


def _download_capped(db: Session, doc_id: int) -> tuple[str, bytes]:
    """Stream a document's bytes, enforcing the receipt-sized download cap so an
    oversized/hostile Paperless response can't be buffered unbounded (CR-SEC-8).

    Returns ``(content_type, content)``; raises ``ValueError`` when the body
    exceeds the cap (by declared Content-Length or by actual bytes read)."""
    import httpx  # local import so the dependency is only needed when used

    base = _require_config(db)
    headers = {"Authorization": f"Token {env_settings.paperless_token}"}
    limit_mb = _MAX_DOWNLOAD_BYTES // _MB
    too_large = ValueError(f"Paperless document {doc_id} is too large (max {limit_mb} MB).")

    def op():
        # follow_redirects=False (CR-SEC-3): never bounce our token to another host.
        with httpx.stream(
            "GET",
            f"{base}/api/documents/{doc_id}/download/",
            headers=headers,
            timeout=_TIMEOUT,
            follow_redirects=False,
        ) as resp:
            resp.raise_for_status()
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > _MAX_DOWNLOAD_BYTES:
                raise too_large
            content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > _MAX_DOWNLOAD_BYTES:
                    raise too_large
                chunks.append(chunk)
        return content_type, b"".join(chunks)

    # A transient blip retries the whole streamed download (bounded); the size cap
    # (ValueError) is not an httpx error, so it propagates immediately, un-retried.
    return _with_retry(op, f"download {doc_id}")


def fetch_document(db: Session, doc_id: int) -> tuple[str, bytes]:
    """Return ``(filename, content)`` for a Paperless document."""
    meta = _get(db, f"/api/documents/{doc_id}/").json()
    title = meta.get("title") or f"paperless-{doc_id}"
    content_type, content = _download_capped(db, doc_id)
    ext = _resolve_extension(content_type, meta)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(title).name)[:100] or f"paperless-{doc_id}"
    if not safe.lower().endswith(ext):
        safe = f"{safe}{ext}"
    return safe, content


def _should_ocr(db: Session, receipt, created: bool) -> bool:
    """Whether to run OCR for a just-stored import. Always for a new receipt; for
    an existing one (re-import) only when it still has no OCR text and its original
    file is on disk — this back-fills receipts pulled in before an OCR engine was
    available, without re-downloading (OCR reads the stored file) or duplicating."""
    if not settings_service.get_ocr_enabled(db):
        return False
    if created:
        return True
    if receipt.ocr_status not in _OCR_INCOMPLETE:
        return False
    return receipt.storage_path is not None and Path(receipt.storage_path).exists()


def import_document(db: Session, doc_id: int) -> dict:
    """Pull a Paperless document into the receipts pipeline (dedup by content hash;
    OCR if enabled). Returns the resulting receipt id + whether it was new. A
    re-import whose receipt never got OCR text (e.g. imported before the OCR engine
    existed) is (re-)OCR'd now that the engine is available."""
    _require_config(db)
    filename, content = fetch_document(db, doc_id)
    if not content:
        raise ValueError("Paperless returned an empty document")
    receipt, created = receipt_service.store_upload(db, filename, content)
    if _should_ocr(db, receipt, created):
        receipt_service.run_ocr(db, receipt, auto_match=True)
    return {
        "receipt_id": receipt.id,
        "created": created,
        "source": "paperless",
        "filename": filename,
    }
