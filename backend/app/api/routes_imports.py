"""Import API routes (spec §24.3)."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Statement, User
from app.parsers import available_parsers
from app.schemas.imports import (
    ConfirmResponse,
    ImportListItem,
    ParserInfo,
    UploadResponse,
)
from app.services import audit_service, import_service
from app.services.auth_service import get_current_user
from app.services.import_service import ImportFailed

router = APIRouter(prefix="/imports", tags=["imports"])


@router.get("/parsers", response_model=list[ParserInfo])
def list_parsers() -> list[dict]:
    return available_parsers()


@router.post("/upload", response_model=UploadResponse, responses={400: {"description": "Bad request"}})
async def upload(
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    parser_id: Annotated[str | None, Form()] = None,
    account_id: Annotated[int | None, Form()] = None,
    mapping: Annotated[str | None, Form()] = None,
) -> dict:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    mapping_dict = None
    if mapping:
        try:
            mapping_dict = json.loads(mapping)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid mapping JSON: {exc}") from exc
    try:
        return import_service.create_import(
            db,
            filename=file.filename or "upload.csv",
            content=content,
            parser_id=parser_id or None,
            account_id=account_id,
            mapping=mapping_dict,
        )
    except ImportFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[ImportListItem])
def list_imports(db: Annotated[Session, Depends(get_db)]) -> list[Statement]:
    return list(db.scalars(select(Statement).order_by(Statement.id.desc())).all())


@router.get("/{import_id}", response_model=ImportListItem, responses={404: {"description": "Not found"}})
def get_import(import_id: int, db: Annotated[Session, Depends(get_db)]) -> Statement:
    statement = db.get(Statement, import_id)
    if statement is None:
        raise HTTPException(status_code=404, detail="Import not found")
    return statement


@router.post("/{import_id}/confirm", response_model=ConfirmResponse, responses={400: {"description": "Bad request"}})
def confirm(
    import_id: int, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]
) -> dict:
    try:
        result = import_service.confirm_import(db, import_id)
    except ImportFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    report = result.get("report") or {}
    audit_service.record(
        db,
        actor=user.display_name,
        action="import_statement",
        entity_type="statement",
        entity_id=import_id,
        details={"new": report.get("new"), "duplicates": report.get("duplicates")},
    )
    db.commit()
    return result


@router.delete("/{import_id}", status_code=204, responses={404: {"description": "Not found"}})
def delete(
    import_id: int, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]
) -> None:
    try:
        import_service.delete_import(db, import_id)
    except ImportFailed as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit_service.record(
        db,
        actor=user.display_name,
        action="delete_import",
        entity_type="statement",
        entity_id=import_id,
    )
    db.commit()
