"""Paperless-ngx import API (spec §21).

Outbound, one-directional: list documents from the user's Paperless instance and
pull selected ones into the receipts pipeline. Paperless never receives our data.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import paperless_service

router = APIRouter(prefix="/paperless", tags=["paperless"])


@router.get("/status")
def status() -> dict:
    return paperless_service.status()


@router.get("/documents", responses={400: {"description": "Bad request"}, 502: {"description": "Upstream error"}})
def list_documents(db: Annotated[Session, Depends(get_db)], query: str | None = None, limit: int = 25) -> list[dict]:
    try:
        return paperless_service.list_documents(db, query=query, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Paperless: {exc}") from exc


@router.post(
    "/documents/{doc_id}/import",
    responses={400: {"description": "Bad request"}, 502: {"description": "Upstream error"}},
)
def import_document(doc_id: int, db: Annotated[Session, Depends(get_db)]) -> dict:
    try:
        return paperless_service.import_document(db, doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Paperless: {exc}") from exc
