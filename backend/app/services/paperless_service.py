"""Paperless-ngx document import (spec §21; backlog: import from Paperless).

**One-directional and outbound only.** We *request* documents FROM the user's own
Paperless-ngx instance and pull them into the receipts pipeline; Paperless never
gets access to our finance data, and we never push anything to it. The only thing
that leaves the box is our API token (to authenticate the pull) and a document id.

Configured via env (``HAFI_PAPERLESS_URL`` + ``HAFI_PAPERLESS_TOKEN``) — the token
is a secret, handled like the AI key. The feature is off unless both are set.

An imported document is de-duplicated by content hash (``receipt_service.store_upload``)
and then OCR'd if OCR is enabled, exactly like an uploaded receipt — so a
re-import never creates a duplicate.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings as env_settings
from app.logging import get_logger
from app.services import receipt_service, settings_service

logger = get_logger(__name__)

_TIMEOUT = 15.0
_EXT_BY_TYPE = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
}


def is_configured() -> bool:
    return bool(env_settings.paperless_url) and bool(env_settings.paperless_token)


def status() -> dict:
    return {
        "configured": is_configured(),
        "url": env_settings.paperless_url or None,
        "token_present": bool(env_settings.paperless_token),
    }


def _require_config() -> str:
    if not is_configured():
        raise ValueError("Paperless is not configured (set HAFI_PAPERLESS_URL and HAFI_PAPERLESS_TOKEN).")
    return env_settings.paperless_url.rstrip("/")


def _get(path: str, **kwargs):
    """Authenticated GET against the Paperless API. Raises on transport/HTTP error
    so the caller can surface a clear message (unlike the best-effort FX/price
    fetches, listing/importing is interactive and the user wants feedback)."""
    import httpx  # local import so the dependency is only needed when used

    base = _require_config()
    headers = {"Authorization": f"Token {env_settings.paperless_token}"}
    resp = httpx.get(f"{base}{path}", headers=headers, timeout=_TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp


def list_documents(*, query: str | None = None, limit: int = 25) -> list[dict]:
    """Recent documents from Paperless (newest first), optionally full-text filtered."""
    limit = max(1, min(100, limit))
    params: dict[str, object] = {"ordering": "-created", "page_size": limit}
    if query:
        params["query"] = query
    data = _get("/api/documents/", params=params).json()
    out = []
    for d in data.get("results", []):
        out.append({
            "id": d.get("id"),
            "title": d.get("title") or f"Document {d.get('id')}",
            "created": d.get("created"),
        })
    return out


def fetch_document(doc_id: int) -> tuple[str, bytes]:
    """Return ``(filename, content)`` for a Paperless document."""
    meta = _get(f"/api/documents/{doc_id}/").json()
    title = meta.get("title") or f"paperless-{doc_id}"
    resp = _get(f"/api/documents/{doc_id}/download/")
    content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = _EXT_BY_TYPE.get(content_type, ".pdf")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(title).name)[:100] or f"paperless-{doc_id}"
    if not safe.lower().endswith(ext):
        safe = f"{safe}{ext}"
    return safe, resp.content


def import_document(db: Session, doc_id: int) -> dict:
    """Pull a Paperless document into the receipts pipeline (dedup by content hash;
    OCR if enabled). Returns the resulting receipt id + whether it was new."""
    _require_config()
    filename, content = fetch_document(doc_id)
    if not content:
        raise ValueError("Paperless returned an empty document")
    receipt, created = receipt_service.store_upload(db, filename, content)
    if created and settings_service.get_ocr_enabled(db):
        receipt_service.run_ocr(db, receipt, auto_match=True)
    return {
        "receipt_id": receipt.id,
        "created": created,
        "source": "paperless",
        "filename": filename,
    }
