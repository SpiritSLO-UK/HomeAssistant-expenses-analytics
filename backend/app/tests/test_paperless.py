"""Paperless-ngx import: status, listing, and pull-into-receipts (spec §21).

All HTTP is monkeypatched so tests never touch a real Paperless instance.
"""

from __future__ import annotations

import httpx

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
