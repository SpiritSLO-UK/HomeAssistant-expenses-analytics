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


def _get(db: Session, path: str, **kwargs):
    """Authenticated GET against the Paperless API. Raises on transport/HTTP error
    so the caller can surface a clear message (unlike the best-effort FX/price
    fetches, listing/importing is interactive and the user wants feedback)."""
    import httpx  # local import so the dependency is only needed when used

    base = _require_config(db)
    headers = {"Authorization": f"Token {env_settings.paperless_token}"}
    # follow_redirects=False (CR-SEC-3): a self-hosted Paperless is legitimately on
    # the LAN, so we don't block private hosts — but we must never follow a redirect,
    # which could bounce our API token to a different (attacker) host.
    resp = httpx.get(f"{base}{path}", headers=headers, timeout=_TIMEOUT, follow_redirects=False, **kwargs)
    resp.raise_for_status()
    return resp


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


def import_document(db: Session, doc_id: int) -> dict:
    """Pull a Paperless document into the receipts pipeline (dedup by content hash;
    OCR if enabled). Returns the resulting receipt id + whether it was new."""
    _require_config(db)
    filename, content = fetch_document(db, doc_id)
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
