"""Paperless-ngx import API (spec §21).

Outbound, one-directional: list documents from the user's Paperless instance and
pull selected ones into the receipts pipeline. Paperless never receives our data.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import paperless_service

router = APIRouter(prefix="/paperless", tags=["paperless"])


@router.get("/status")
def status() -> dict:
    return paperless_service.status()


@router.get("/documents")
def list_documents(query: str | None = None, limit: int = 25, db: Session = Depends(get_db)) -> list[dict]:
    try:
        return paperless_service.list_documents(db, query=query, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Paperless: {exc}") from exc


@router.post("/documents/{doc_id}/import")
def import_document(doc_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return paperless_service.import_document(db, doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Paperless: {exc}") from exc
