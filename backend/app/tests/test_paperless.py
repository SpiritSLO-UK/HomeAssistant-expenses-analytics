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


def _configure(monkeypatch):
    monkeypatch.setattr(env_settings, "paperless_url", "http://paperless.test")
    monkeypatch.setattr(env_settings, "paperless_token", "tok-123")


def _unconfigure(monkeypatch):
    monkeypatch.setattr(env_settings, "paperless_url", None)
    monkeypatch.setattr(env_settings, "paperless_token", None)


def test_status_not_configured(client, monkeypatch):
    _unconfigure(monkeypatch)
    assert client.get("/api/paperless/status").json() == {
        "configured": False, "url": None, "token_present": False
    }


def test_status_configured(client, monkeypatch):
    _configure(monkeypatch)
    s = client.get("/api/paperless/status").json()
    assert s["configured"] is True
    assert s["token_present"] is True
    assert s["url"] == "http://paperless.test"


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
        if url.endswith("/api/documents/7/download/"):
            return _Resp(content=pdf, headers={"content-type": "application/pdf"})
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(httpx, "get", fake_get)

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
