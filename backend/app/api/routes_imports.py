"""Import API routes (spec §24.3)."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import uploads
from app.db.session import get_db
from app.models import Statement, User
from app.parsers import available_parsers
from app.parsers.base import ParseError, StandardTransaction, parse_amount, parse_date
from app.schemas.imports import (
    ConfirmResponse,
    FundingLinkOut,
    FundingLinkUpdate,
    ImportListItem,
    ImportProfileIn,
    ImportProfileOut,
    InspectResponse,
    ParserInfo,
    UploadResponse,
)
from app.services import (
    ai_service,
    audit_service,
    curve_link_service,
    import_profile_service,
    import_service,
    settings_service,
)
from app.services.ai_provider import AIError
from app.services.ai_service import AIDisabled
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
    content = await uploads.read_capped(file, uploads.IMPORT_MAX, label="Statement")
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


def _rows_from_ai(extracted: list[dict], base_currency: str) -> list[StandardTransaction]:
    """Turn the AI's [{date, description, amount}] into StandardTransaction rows,
    skipping any without a usable amount. Flagged needs_review (AI-extracted)."""
    from datetime import date as _date

    rows: list[StandardTransaction] = []
    for item in extracted:
        try:
            amount = parse_amount(str(item.get("amount", "")))
        except ParseError:
            continue  # no usable amount → skip the row
        try:
            txn_date = parse_date(str(item.get("date", "")))
        except ParseError:
            txn_date = _date.today()
        desc = (str(item.get("description") or "")).strip() or "(no description)"
        rows.append(StandardTransaction(
            transaction_date=txn_date, amount=amount, currency=base_currency,
            description_raw=desc, needs_review=True,
        ))
    return rows


@router.post("/ai-extract", response_model=UploadResponse,
             responses={400: {"description": "Bad request / AI off"}, 502: {"description": "AI error"}})
async def ai_extract(
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    account_id: Annotated[int | None, Form()] = None,
) -> dict:
    """Opt-in vision-AI fallback: extract transactions from a statement **image**
    the OCR parser couldn't read, and stage them as a normal import to review +
    confirm. The frontend warns before calling this (the image is sent to the AI,
    and an image can't be redacted)."""
    content = await uploads.read_capped(file, uploads.AI_IMAGE_MAX, label="Image")
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    mime = file.content_type or "image/jpeg"
    try:
        extracted = ai_service.extract_statement_image(db, content, mime)
    except AIDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status_code=502, detail=f"AI extraction failed: {exc}") from exc
    audit_service.record_image_sent(db, actor=user.display_name, kind="statement", size=len(content))
    db.commit()
    rows = _rows_from_ai(extracted, settings_service.get_base_currency(db))
    if not rows:
        raise HTTPException(status_code=400, detail="The AI didn't find any transactions in this image.")
    name = file.filename or "ai-image"
    fmt = name.rsplit(".", 1)[-1].lower() if "." in name else "image"
    return import_service.create_import_from_rows(
        db, name, content, rows, account_id=account_id, institution="AI-extracted", fmt=fmt
    )


@router.get("/funding-links", response_model=list[FundingLinkOut])
def list_funding_links(db: Annotated[Session, Depends(get_db)]) -> list:
    """Curve funding-card → account mappings (used for cross-account dedup)."""
    return curve_link_service.list_links(db)


@router.put(
    "/funding-links",
    response_model=list[FundingLinkOut],
    responses={400: {"description": "Bad request"}},
)
def set_funding_link(
    payload: FundingLinkUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> list:
    """Map a Curve ``Card Name`` to a real account (or clear it with a null
    ``account_id``). Returns the full updated list."""
    try:
        curve_link_service.set_link(db, payload.label, payload.account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_service.record(
        db,
        actor=user.display_name,
        action="set_curve_funding_link",
        entity_type="curve_funding_link",
        entity_id=None,
        details={"label": payload.label, "account_id": payload.account_id},
    )
    db.commit()
    return curve_link_service.list_links(db)


# --- Custom CSV mapping + saved import profiles (declared before /{import_id}) ---


@router.post("/inspect", response_model=InspectResponse, responses={400: {"description": "Bad request"}})
async def inspect_csv(file: Annotated[UploadFile, File()]) -> dict:
    """Return a CSV's headers + a few sample rows + a heuristic column mapping, so
    the user can map columns in the UI before importing (no data is stored)."""
    content = await uploads.read_capped(file, uploads.IMPORT_MAX, label="CSV")
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        return import_profile_service.inspect_csv(content)
    except (ValueError, ParseError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/profiles", response_model=list[ImportProfileOut])
def list_import_profiles(db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    """Saved CSV column-mapping profiles."""
    return import_profile_service.list_profiles(db)


@router.post("/profiles", response_model=ImportProfileOut, responses={400: {"description": "Bad request"}})
def create_import_profile(payload: ImportProfileIn, db: Annotated[Session, Depends(get_db)]) -> dict:
    try:
        return import_profile_service.create_profile(
            db, name=payload.name, mapping=payload.mapping, default_currency=payload.default_currency
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put(
    "/profiles/{profile_id}",
    response_model=ImportProfileOut,
    responses={400: {"description": "Bad request"}, 404: {"description": "Not found"}},
)
def update_import_profile(
    profile_id: int, payload: ImportProfileIn, db: Annotated[Session, Depends(get_db)]
) -> dict:
    try:
        result = import_profile_service.update_profile(
            db, profile_id, name=payload.name, mapping=payload.mapping, default_currency=payload.default_currency
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Import profile not found")
    return result


@router.delete("/profiles/{profile_id}", responses={404: {"description": "Not found"}})
def delete_import_profile(profile_id: int, db: Annotated[Session, Depends(get_db)]) -> dict:
    if not import_profile_service.delete_profile(db, profile_id):
        raise HTTPException(status_code=404, detail="Import profile not found")
    return {"status": "deleted"}


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
