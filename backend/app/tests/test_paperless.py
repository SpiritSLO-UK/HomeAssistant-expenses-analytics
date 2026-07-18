"""Paperless-ngx import: status, listing, and pull-into-receipts (spec §21).

All HTTP is monkeypatched so tests never touch a real Paperless instance.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import settings as env_settings
from app.services import paperless_service  # noqa: F401  (ensures module import)


class _Resp:
    def __init__(self, *, json_data=None, content=b"", headers=None):
        self._json = json_data
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._json


class _StreamResp:
    """Fake of an ``httpx.stream(...)`` context manager for download tests."""

    def __init__(self, *, content=b"", headers=None):
        self._content = content
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self, chunk_size=None):
        # Emit in small chunks so the byte-count cap logic is actually exercised.
        step = chunk_size or (1024 * 1024)
        for i in range(0, len(self._content), step):
            yield self._content[i:i + step]


def _stream_download(content=b"", headers=None):
    """Return an ``httpx.stream`` replacement serving one download body."""
    def fake_stream(method, url, **kw):
        assert method == "GET"
        assert url.endswith("/download/")
        return _StreamResp(content=content, headers=headers)

    return fake_stream


def _configure(monkeypatch):
    monkeypatch.setattr(env_settings, "paperless_url", "http://paperless.test")
    monkeypatch.setattr(env_settings, "paperless_token", "tok-123")


def _unconfigure(monkeypatch):
    monkeypatch.setattr(env_settings, "paperless_url", None)
    monkeypatch.setattr(env_settings, "paperless_token", None)


def test_status_not_configured(client, monkeypatch):
    _unconfigure(monkeypatch)
    assert client.get("/api/paperless/status").json() == {
        "configured": False, "url": None, "url_source": None, "token_present": False
    }


def test_status_configured(client, monkeypatch):
    _configure(monkeypatch)
    s = client.get("/api/paperless/status").json()
    assert s["configured"] is True
    assert s["token_present"] is True
    assert s["url"] == "http://paperless.test"
    assert s["url_source"] == "env"


def test_url_from_settings_overrides_env(client, monkeypatch):
    # Token from env, URL entered in Settings → configured, url_source "settings".
    monkeypatch.setattr(env_settings, "paperless_url", None)
    monkeypatch.setattr(env_settings, "paperless_token", "tok-123")
    assert client.put("/api/settings", json={"paperless_url": "http://docs.local/"}).status_code == 200
    s = client.get("/api/paperless/status").json()
    assert s["configured"] is True
    assert s["url"] == "http://docs.local"  # trailing slash trimmed
    assert s["url_source"] == "settings"
    # A non-http(s) value is rejected.
    assert client.put("/api/settings", json={"paperless_url": "ftp://nope"}).status_code == 400
    # "" clears it → back to the (absent) env fallback → not configured.
    assert client.put("/api/settings", json={"paperless_url": ""}).status_code == 200
    assert client.get("/api/paperless/status").json()["configured"] is False


def test_test_connection(client, monkeypatch):
    _configure(monkeypatch)

    def fake_get(url, **kw):
        assert url == "http://paperless.test/api/documents/"
        assert kw["params"] == {"page_size": 1}
        return _Resp(json_data={"results": []})

    monkeypatch.setattr(httpx, "get", fake_get)
    assert client.post("/api/paperless/test").json() == {"ok": True, "url": "http://paperless.test"}


def test_test_connection_requires_config(client, monkeypatch):
    _unconfigure(monkeypatch)
    assert client.post("/api/paperless/test").status_code == 400


def test_list_requires_config(client, monkeypatch):
    _unconfigure(monkeypatch)
    assert client.get("/api/paperless/documents").status_code == 400


def test_list_documents(client, monkeypatch):
    _configure(monkeypatch)

    def fake_get(url, **kw):
        assert "/api/documents/" in url
        assert kw["headers"]["Authorization"] == "Token tok-123"
        return _Resp(json_data={"results": [
            {"id": 7, "title": "Tesco receipt", "created": "2026-05-01T10:00:00Z"},
            {"id": 8, "title": None, "created": "2026-05-02T10:00:00Z"},
        ]})

    monkeypatch.setattr(httpx, "get", fake_get)
    docs = client.get("/api/paperless/documents?limit=10").json()
    assert [d["id"] for d in docs] == [7, 8]
    assert docs[1]["title"] == "Document 8"  # null title → fallback


def test_import_creates_receipt_and_dedups(client, monkeypatch):
    _configure(monkeypatch)
    pdf = b"%PDF-1.4 fake content"

    def fake_get(url, **kw):
        if url.endswith("/api/documents/7/"):
            return _Resp(json_data={"id": 7, "title": "Tesco receipt"})
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "stream", _stream_download(pdf, {"content-type": "application/pdf"}))

    r1 = client.post("/api/paperless/documents/7/import").json()
    assert r1["created"] is True
    assert r1["filename"].endswith(".pdf")
    assert any(rc["id"] == r1["receipt_id"] for rc in client.get("/api/receipts").json())

    # Re-import the same document → de-duplicated by content hash, no new receipt.
    r2 = client.post("/api/paperless/documents/7/import").json()
    assert r2["created"] is False
    assert r2["receipt_id"] == r1["receipt_id"]


def test_import_requires_config(client, monkeypatch):
    _unconfigure(monkeypatch)
    assert client.post("/api/paperless/documents/1/import").status_code == 400


def test_unknown_content_type_falls_back_to_metadata_extension(db, monkeypatch):
    """An unknown/empty content-type must not be labelled ``.pdf``; the extension
    is derived from the Paperless filename metadata instead."""

    _configure(monkeypatch)

    def fake_get(url, **kw):
        return _Resp(json_data={
            "id": 9, "title": "Groceries",
            "original_file_name": "scan_0001.PNG",
        })

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(
        httpx, "stream",
        _stream_download(b"\x89PNG fake", {"content-type": "application/octet-stream"}),
    )

    filename, content = paperless_service.fetch_document(db, 9)
    assert filename.endswith(".png")
    assert content == b"\x89PNG fake"


def test_unknown_content_type_and_no_metadata_uses_generic_extension(db, monkeypatch):
    """No usable content-type and no filename metadata → neutral ``.bin``, never
    a mislabelled ``.pdf``."""

    _configure(monkeypatch)

    def fake_get(url, **kw):
        return _Resp(json_data={"id": 9, "title": "mystery"})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "stream", _stream_download(b"\x00\x01\x02", {}))

    filename, content = paperless_service.fetch_document(db, 9)
    assert filename.endswith(".bin")
    assert content == b"\x00\x01\x02"


def test_download_exceeding_cap_by_actual_bytes_is_rejected(db, monkeypatch):
    """A body larger than the cap (with no declared Content-Length) is aborted."""
    import pytest


    _configure(monkeypatch)
    monkeypatch.setattr(paperless_service, "_MAX_DOWNLOAD_BYTES", 8)

    def fake_get(url, **kw):
        return _Resp(json_data={"id": 9, "title": "big"})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(
        httpx, "stream",
        _stream_download(b"x" * 64, {"content-type": "application/pdf"}),
    )

    with pytest.raises(ValueError, match="too large"):
        paperless_service.fetch_document(db, 9)


# --- transient-error retry (#356) ---------------------------------------

def _http_status_error(code: int) -> httpx.HTTPStatusError:
    """An ``httpx.HTTPStatusError`` as ``raise_for_status`` would raise it."""
    req = httpx.Request("GET", "http://paperless.test/api/documents/")
    resp = httpx.Response(code, request=req)
    return httpx.HTTPStatusError(f"HTTP {code}", request=req, response=resp)


def test_list_retries_transient_then_succeeds(db, monkeypatch):
    """A transient blip (connect drop, then a 503) is retried; the third attempt
    succeeds and the caller never sees the error. Backoff sleep is neutralised."""
    _configure(monkeypatch)
    monkeypatch.setattr(paperless_service, "_BACKOFF_BASE", 0.0)
    calls = {"n": 0}

    def flaky_get(url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        if calls["n"] == 2:
            raise _http_status_error(503)
        return _Resp(json_data={"results": [{"id": 3, "title": "ok", "created": "x"}]})

    monkeypatch.setattr(httpx, "get", flaky_get)
    docs = paperless_service.list_documents(db)
    assert [d["id"] for d in docs] == [3]
    assert calls["n"] == 3  # two retries then success


def test_list_retry_exhausted_reraises(db, monkeypatch):
    """A persistently transient upstream is retried up to the cap, then the last
    error propagates so the interactive caller can surface a clear message."""
    _configure(monkeypatch)
    monkeypatch.setattr(paperless_service, "_BACKOFF_BASE", 0.0)
    calls = {"n": 0}

    def always_503(url, **kw):
        calls["n"] += 1
        raise _http_status_error(503)

    monkeypatch.setattr(httpx, "get", always_503)
    with pytest.raises(httpx.HTTPStatusError):
        paperless_service.list_documents(db)
    assert calls["n"] == paperless_service._MAX_ATTEMPTS


def test_permanent_4xx_is_not_retried(db, monkeypatch):
    """A 404 (permanent) fails fast: a single attempt, no retry."""
    _configure(monkeypatch)
    monkeypatch.setattr(paperless_service, "_BACKOFF_BASE", 0.0)
    calls = {"n": 0}

    def not_found(url, **kw):
        calls["n"] += 1
        raise _http_status_error(404)

    monkeypatch.setattr(httpx, "get", not_found)
    with pytest.raises(httpx.HTTPStatusError):
        paperless_service.list_documents(db)
    assert calls["n"] == 1


def test_download_retries_transient_then_succeeds(db, monkeypatch):
    """A dropped download connection is retried; the streamed body + size cap are
    unaffected. Metadata GET is stubbed; only the stream call is flaky."""
    _configure(monkeypatch)
    monkeypatch.setattr(paperless_service, "_BACKOFF_BASE", 0.0)

    monkeypatch.setattr(httpx, "get", lambda url, **kw: _Resp(json_data={"id": 9, "title": "r"}))
    calls = {"n": 0}
    ok_stream = _stream_download(b"%PDF ok", {"content-type": "application/pdf"})

    def flaky_stream(method, url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("slow")
        return ok_stream(method, url, **kw)

    monkeypatch.setattr(httpx, "stream", flaky_stream)
    filename, content = paperless_service.fetch_document(db, 9)
    assert content == b"%PDF ok"
    assert filename.endswith(".pdf")
    assert calls["n"] == 2


# --- back-fill OCR on re-import (#137) ----------------------------------

def test_reimport_backfills_ocr_when_engine_now_available(db, monkeypatch):
    """A document imported while OCR was off/unavailable leaves its receipt
    un-OCR'd. A later re-import, once OCR is enabled and an engine is present,
    re-OCRs the SAME receipt (no duplicate, no re-download of a new receipt)."""
    from app.models import Receipt
    from app.services import ocr_service, settings_service

    _configure(monkeypatch)
    pdf = b"%PDF-1.4 fake receipt"
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _Resp(json_data={"id": 7, "title": "Tesco"}))
    monkeypatch.setattr(httpx, "stream", _stream_download(pdf, {"content-type": "application/pdf"}))

    # First import with OCR turned off → stored but never OCR'd.
    settings_service.set_value(db, settings_service.OCR_ENABLED, "false")
    first = paperless_service.import_document(db, 7)
    assert first["created"] is True
    receipt = db.get(Receipt, first["receipt_id"])
    assert receipt.ocr_status == "not_processed"  # no OCR text yet

    # Engine now present + OCR enabled → re-import re-OCRs the same receipt.
    settings_service.set_value(db, settings_service.OCR_ENABLED, "true")
    monkeypatch.setattr(ocr_service, "can_handle", lambda name: True)
    monkeypatch.setattr(ocr_service, "extract_text", lambda path: ("TESCO STORES header", 0.8))

    second = paperless_service.import_document(db, 7)
    assert second["created"] is False  # dedup: no duplicate receipt
    assert second["receipt_id"] == first["receipt_id"]
    db.refresh(receipt)
    assert receipt.ocr_status == "processed"


def test_reimport_skips_ocr_when_already_processed(db, monkeypatch):
    """A re-import of an already-OCR'd receipt does NOT re-run OCR (no redo)."""
    from app.models import Receipt
    from app.services import ocr_service, receipt_service, settings_service

    _configure(monkeypatch)
    pdf = b"%PDF-1.4 processed receipt"
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _Resp(json_data={"id": 8, "title": "Boots"}))
    monkeypatch.setattr(httpx, "stream", _stream_download(pdf, {"content-type": "application/pdf"}))
    settings_service.set_value(db, settings_service.OCR_ENABLED, "true")
    monkeypatch.setattr(ocr_service, "can_handle", lambda name: True)
    monkeypatch.setattr(ocr_service, "extract_text", lambda path: ("BOOTS header", 0.8))

    first = paperless_service.import_document(db, 8)
    receipt = db.get(Receipt, first["receipt_id"])
    assert receipt.ocr_status == "processed"

    calls = {"n": 0}
    orig_run = receipt_service.run_ocr

    def counting_run(*a, **kw):
        calls["n"] += 1
        return orig_run(*a, **kw)

    monkeypatch.setattr(receipt_service, "run_ocr", counting_run)
    second = paperless_service.import_document(db, 8)
    assert second["created"] is False
    assert calls["n"] == 0  # already processed → not re-OCR'd


def test_download_exceeding_cap_by_content_length_is_rejected(db, monkeypatch):
    """An oversized declared Content-Length is rejected cheaply, before reading."""
    import pytest


    _configure(monkeypatch)
    monkeypatch.setattr(paperless_service, "_MAX_DOWNLOAD_BYTES", 8)

    def fake_get(url, **kw):
        return _Resp(json_data={"id": 9, "title": "big"})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(
        httpx, "stream",
        _stream_download(b"x" * 4, {"content-type": "application/pdf", "content-length": "999"}),
    )

    with pytest.raises(ValueError, match="too large"):
        paperless_service.fetch_document(db, 9)
