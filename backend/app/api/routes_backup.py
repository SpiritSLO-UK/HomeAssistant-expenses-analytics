"""Backup / restore and demo-data routes (spec §26.5; backlog #9, #10, #16)."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.api import uploads
from app.db.session import get_db
from app.models import User
from app.services import audit_service, backup_service, crypto_service, demo_service
from app.services.auth_service import get_current_user, require_owner
from app.services.backup_service import RestoreError
from app.services.crypto_service import DecryptError

router = APIRouter(prefix="/backup", tags=["backup"])


@router.get("/database")
def download_database(_owner: Annotated[User, Depends(require_owner)]) -> FileResponse:
    """Download a consistent snapshot of the SQLite database. Owner-only — this is
    the entire household database (full data exfiltration if exposed)."""
    snapshot = backup_service.snapshot_database()
    # Delete the temp snapshot after it has been streamed to the client.
    return FileResponse(
        path=snapshot,
        filename="ha-finance-backup.db",
        media_type="application/octet-stream",
        background=BackgroundTask(snapshot.unlink, missing_ok=True),
    )


@router.post("/restore", responses={400: {"description": "Bad request"}})
async def restore_database(
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    _owner: Annotated[User, Depends(require_owner)],
) -> dict:
    """Restore the database from an uploaded snapshot (destructive). Owner-only —
    an attacker-crafted but valid DB could otherwise grant itself owner access.
    The POST is recorded by the api_call audit middleware (into the restored DB)."""
    content = await uploads.read_capped(file, uploads.RESTORE_MAX, label="Backup")
    # Release this request's DB connection (opened by the owner-gate) before the
    # service swaps the file out — a live handle blocks deleting the WAL on Windows.
    db.close()
    try:
        backup_service.restore_database(content)
    except RestoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "restored"}


@router.post("/database/encrypted", responses={400: {"description": "Bad request"}})
def download_encrypted_database(
    passphrase: Annotated[str, Form()], _owner: Annotated[User, Depends(require_owner)]
) -> Response:
    """Download a passphrase-encrypted snapshot of the database (backlog #15).
    Owner-only — it is still the entire household database, just encrypted.

    AES-256-GCM; only someone with the passphrase can read it. There is NO
    recovery if the passphrase is lost.
    """
    if not passphrase:
        raise HTTPException(status_code=400, detail="A passphrase is required.")
    snapshot = backup_service.snapshot_database()
    try:
        blob = crypto_service.encrypt(snapshot.read_bytes(), passphrase)
    finally:
        snapshot.unlink(missing_ok=True)
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="ha-finance-backup.db.enc"'},
    )


@router.post("/restore/encrypted", responses={400: {"description": "Bad request"}})
async def restore_encrypted_database(
    file: Annotated[UploadFile, File()],
    passphrase: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
    _owner: Annotated[User, Depends(require_owner)],
) -> dict:
    """Decrypt an encrypted backup with the passphrase and restore it. Owner-only
    (destructive); recorded by the api_call audit middleware."""
    content = await uploads.read_capped(file, uploads.RESTORE_MAX, label="Backup")
    try:
        plaintext = crypto_service.decrypt(content, passphrase)
    except DecryptError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Release this request's DB connection (owner-gate) before the file swap so the
    # WAL can be removed on Windows.
    db.close()
    try:
        backup_service.restore_database(plaintext)
    except RestoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "restored"}


@router.get("/config")
def export_config(
    db: Annotated[Session, Depends(get_db)], _owner: Annotated[User, Depends(require_owner)]
) -> dict:
    """Export settings + category/vendor library as portable JSON. Owner-only —
    includes every household setting."""
    return backup_service.export_config(db)


@router.post("/config", responses={400: {"description": "Bad request"}})
async def import_config(
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    owner: Annotated[User, Depends(require_owner)],
) -> dict:
    """Import a config export (settings + library). Owner-only — it writes
    arbitrary settings, so a non-owner must never reach it."""
    content = await uploads.read_capped(file, uploads.CONFIG_MAX, label="Config")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc
    result = backup_service.import_config(db, data)
    audit_service.record(db, actor=owner.display_name, action="config_import", details=result)
    db.commit()
    return result


@router.post("/demo")
def load_demo(db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]) -> dict:
    """Load fabricated demo data so the app is populated for a first look."""
    result = demo_service.load_demo(db)
    audit_service.record(db, actor=user.display_name, action="load_demo", details=result)
    db.commit()
    return result


@router.get("/demo")
def demo_status(db: Annotated[Session, Depends(get_db)]) -> dict:
    """Whether removable demo data is present (from a previous load)."""
    return {"has_demo_data": demo_service.has_demo_data(db)}


@router.delete("/demo")
def remove_demo(db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require_owner)]) -> dict:
    """Remove everything a previous demo load created, leaving a clean database.
    Owner-only and destructive — only the demo's own rows are deleted (real
    imports and anything the user added afterwards are left untouched)."""
    result = demo_service.remove_demo(db)
    audit_service.record(db, actor=user.display_name, action="remove_demo", details=result)
    db.commit()
    return result
