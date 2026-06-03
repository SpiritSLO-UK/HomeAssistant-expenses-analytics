"""Backup / restore and demo-data routes (spec §26.5; backlog #9, #10, #16)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.db.session import get_db
from app.models import User
from app.services import audit_service, backup_service, crypto_service, demo_service
from app.services.auth_service import get_current_user, require_owner
from app.services.backup_service import RestoreError
from app.services.crypto_service import DecryptError

router = APIRouter(prefix="/backup", tags=["backup"])


@router.get("/database")
def download_database() -> FileResponse:
    """Download a consistent snapshot of the SQLite database."""
    snapshot = backup_service.snapshot_database()
    # Delete the temp snapshot after it has been streamed to the client.
    return FileResponse(
        path=snapshot,
        filename="ha-finance-backup.db",
        media_type="application/octet-stream",
        background=BackgroundTask(snapshot.unlink, missing_ok=True),
    )


@router.post("/restore")
async def restore_database(
    file: UploadFile = File(...), _db: Session = Depends(get_db)
) -> dict:
    """Restore the database from an uploaded snapshot (destructive)."""
    content = await file.read()
    try:
        backup_service.restore_database(content)
    except RestoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "restored"}


@router.post("/database/encrypted")
def download_encrypted_database(passphrase: str = Form(...)) -> Response:
    """Download a passphrase-encrypted snapshot of the database (backlog #15).

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


@router.post("/restore/encrypted")
async def restore_encrypted_database(
    file: UploadFile = File(...), passphrase: str = Form(...), _db: Session = Depends(get_db)
) -> dict:
    """Decrypt an encrypted backup with the passphrase and restore it."""
    content = await file.read()
    try:
        plaintext = crypto_service.decrypt(content, passphrase)
    except DecryptError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        backup_service.restore_database(plaintext)
    except RestoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "restored"}


@router.get("/config")
def export_config(db: Session = Depends(get_db)) -> dict:
    """Export settings + category/vendor library as portable JSON."""
    return backup_service.export_config(db)


@router.post("/config")
async def import_config(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    content = await file.read()
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc
    return backup_service.import_config(db, data)


@router.post("/demo")
def load_demo(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Load fabricated demo data so the app is populated for a first look."""
    result = demo_service.load_demo(db)
    audit_service.record(db, actor=user.display_name, action="load_demo", details=result)
    db.commit()
    return result


@router.get("/demo")
def demo_status(db: Session = Depends(get_db)) -> dict:
    """Whether removable demo data is present (from a previous load)."""
    return {"has_demo_data": demo_service.has_demo_data(db)}


@router.delete("/demo")
def remove_demo(
    db: Session = Depends(get_db),
    user: User = Depends(require_owner),
) -> dict:
    """Remove everything a previous demo load created, leaving a clean database.
    Owner-only and destructive — only the demo's own rows are deleted (real
    imports and anything the user added afterwards are left untouched)."""
    result = demo_service.remove_demo(db)
    audit_service.record(db, actor=user.display_name, action="remove_demo", details=result)
    db.commit()
    return result
