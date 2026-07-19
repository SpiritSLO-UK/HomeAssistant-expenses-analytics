"""Upload size caps (backlog CR-SEC-8).

``read_capped`` rejects oversized uploads with 413 — both by the declared size
(fast path) and by a bounded read when the size isn't declared — so an unbounded
``file.read()`` can't balloon memory. Wired into the import / AI-extract / restore
/ config-import endpoints.
"""

from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.api import uploads


def _upload(data: bytes, size: int | None = None) -> UploadFile:
    return UploadFile(filename="x.bin", file=io.BytesIO(data), size=size)


def test_read_capped_allows_within_limit():
    data = b"x" * 100
    assert asyncio.run(uploads.read_capped(_upload(data), 1000)) == data


def test_read_capped_rejects_via_bounded_read():
    # Size not declared → the chunked read must catch it.
    with pytest.raises(HTTPException) as ei:
        asyncio.run(uploads.read_capped(_upload(b"x" * 500), 100))
    assert ei.value.status_code == 413


def test_read_capped_rejects_via_declared_size():
    # Declared (Content-Length) size over the cap → fast reject, no read.
    with pytest.raises(HTTPException) as ei:
        asyncio.run(uploads.read_capped(_upload(b"x" * 10, size=10_000), 100))
    assert ei.value.status_code == 413


def test_import_upload_rejects_oversized(client, monkeypatch):
    """The import endpoint honours the cap (413) instead of reading unbounded."""
    monkeypatch.setattr(uploads, "IMPORT_MAX", 50)
    r = client.post(
        "/api/imports/upload",
        files={"file": ("big.csv", b"x" * 500, "text/csv")},
    )
    assert r.status_code == 413


def test_receipt_upload_rejects_oversized(client, monkeypatch):
    """The receipt upload endpoint caps via read_capped (413) instead of buffering the
    whole body then checking length (#25)."""
    monkeypatch.setattr(uploads, "RECEIPT_MAX", 50)
    r = client.post(
        "/api/receipts/upload",
        files={"file": ("big.png", b"x" * 500, "image/png")},
    )
    assert r.status_code == 413


def test_transaction_receipt_attach_rejects_oversized(client, monkeypatch):
    """The per-transaction receipt attach endpoint honours the same receipt cap (#25)."""
    up = client.post(
        "/api/imports/upload",
        files={"file": ("s.csv", b"Date,Description,Amount,Currency,Card,Category\n2026-05-02,SHOP,-1.00,GBP,Visa,\n", "text/csv")},
        data={"parser_id": "curve_csv"},
    ).json()
    client.post(f"/api/imports/{up['import_id']}/confirm")
    txn_id = client.get("/api/transactions").json()["items"][0]["id"]

    monkeypatch.setattr(uploads, "RECEIPT_MAX", 50)
    r = client.post(
        f"/api/transactions/{txn_id}/receipts",
        files={"file": ("big.png", b"x" * 500, "image/png")},
    )
    assert r.status_code == 413
