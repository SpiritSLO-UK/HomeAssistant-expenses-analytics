"""Vendors API routes (spec §24.6)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.schemas.vendors import (
    AliasCreate,
    AliasOut,
    SetDefaultCategory,
    VendorCreate,
    VendorMerge,
    VendorOut,
    VendorUpdate,
    VendorWithStats,
)
from app.services import auth_service, vendor_service

router = APIRouter(prefix="/vendors", tags=["vendors"])

_NOT_FOUND = "Vendor not found"


@router.get("", response_model=list[VendorWithStats])
def list_vendors(db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    vendors = vendor_service.list_vendors(db)
    result = []
    for v in vendors:
        stats = vendor_service.vendor_stats(db, v.id)
        item = VendorWithStats.model_validate(v).model_dump()
        item.update(stats)
        result.append(item)
    return result


@router.post("", response_model=VendorOut, status_code=201)
def create_vendor(payload: VendorCreate, db: Annotated[Session, Depends(get_db)]):
    return vendor_service.create_vendor(db, payload.model_dump(exclude_unset=True))


@router.get("/{vendor_id}", response_model=VendorWithStats, responses={404: {"description": "Not found"}})
def get_vendor(vendor_id: int, db: Annotated[Session, Depends(get_db)]) -> dict:
    vendor = vendor_service.get_vendor(db, vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    item = VendorWithStats.model_validate(vendor).model_dump()
    item.update(vendor_service.vendor_stats(db, vendor_id))
    return item


@router.patch("/{vendor_id}", response_model=VendorOut, responses={404: {"description": "Not found"}})
def update_vendor(vendor_id: int, payload: VendorUpdate, db: Annotated[Session, Depends(get_db)]):
    vendor = vendor_service.update_vendor(db, vendor_id, payload.model_dump(exclude_unset=True))
    if vendor is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return vendor


@router.post(
    "/{vendor_id}/aliases", response_model=AliasOut, status_code=201, responses={404: {"description": "Not found"}}
)
def add_alias(vendor_id: int, payload: AliasCreate, db: Annotated[Session, Depends(get_db)]):
    alias = vendor_service.add_alias(db, vendor_id, payload.alias, payload.match_type)
    if alias is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return alias


@router.post(
    "/{vendor_id}/set-default-category", response_model=VendorOut, responses={404: {"description": "Not found"}}
)
def set_default_category(vendor_id: int, payload: SetDefaultCategory, db: Annotated[Session, Depends(get_db)]):
    vendor = vendor_service.set_default_category(db, vendor_id, payload.category_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return vendor


@router.delete("/{vendor_id}", status_code=204, responses={404: {"description": "Not found"}})
def delete_vendor(vendor_id: int, db: Annotated[Session, Depends(get_db)]) -> None:
    if not vendor_service.delete_vendor(db, vendor_id):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)


@router.post(
    "/{vendor_id}/merge",
    response_model=VendorOut,
    responses={400: {"description": "Bad request"}, 404: {"description": "Not found"}},
)
def merge_vendor(
    vendor_id: int,
    payload: VendorMerge,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(auth_service.require_owner)],
):
    """Merge one vendor's references (transactions, receipts, subscriptions,
    aliases) into another then delete it — structural/destructive, so owner only."""
    try:
        target = vendor_service.merge_vendor(db, vendor_id, payload.target_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if target is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return target
