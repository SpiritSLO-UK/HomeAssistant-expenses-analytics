"""Shared upload-size guard (backlog CR-SEC-8).

Several endpoints used to do an unbounded ``await file.read()`` — a large upload
could balloon memory. ``read_capped`` rejects oversized uploads with 413 via a
fast declared-size (Content-Length) check plus a bounded chunked read, so the
handler never materialises more than the cap in memory.

Per-endpoint caps live here so they're in one place (and patchable in tests).
"""

from __future__ import annotations

from fastapi import HTTPException, UploadFile

MB = 1024 * 1024

# Per-endpoint caps. Statements/images are modest; a DB restore is generous (a
# real household DB is tens of MB, but we allow plenty of headroom); config JSON
# is tiny.
IMPORT_MAX = 25 * MB        # statement CSV / PDF / image
AI_IMAGE_MAX = 15 * MB      # vision-AI statement image (matches the receipt cap)
RECEIPT_MAX = 15 * MB       # uploaded receipt image / PDF
RESTORE_MAX = 500 * MB      # uploaded SQLite DB backup
CONFIG_MAX = 10 * MB        # settings/library JSON export


async def read_capped(file: UploadFile, max_bytes: int, *, label: str = "File") -> bytes:
    """Read an upload, raising 413 if it exceeds ``max_bytes``. Checks the declared
    size first (cheap reject), then reads in 1 MB chunks and stops the moment the
    cap is passed — so an oversized body can't be fully buffered into memory here."""
    limit_mb = max(1, max_bytes // MB)
    too_large = HTTPException(status_code=413, detail=f"{label} too large (max {limit_mb} MB).")
    if file.size is not None and file.size > max_bytes:
        raise too_large
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(MB)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)
