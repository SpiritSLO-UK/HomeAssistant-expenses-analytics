"""Backup / restore and demo-data routes (spec §26.5; backlog #9, #10, #16)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.db.session import get_db
from app.services import backup_service, demo_service
from app.services.backup_service import RestoreError

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
def load_demo(db: Session = Depends(get_db)) -> dict:
    """Load fabricated demo data so the app is populated for a first look."""
    return demo_service.load_demo(db)
