"""Assets API: cars/home/other and their log timelines (spec §25.1).

Assets are household-level (like projects/budgets), so reads are open to approved
users and writes follow the global write-role gate (enforced in middleware).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.assets import AssetCreate, AssetLogCreate, AssetUpdate
from app.services import asset_service

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("")
def list_assets(db: Annotated[Session, Depends(get_db)], kind: str | None = None) -> list[dict]:
    return [asset_service.asset_to_dict(db, a) for a in asset_service.list_assets(db, kind=kind)]


@router.post("", status_code=201, responses={400: {"description": "Bad request"}})
def create_asset(payload: AssetCreate, db: Annotated[Session, Depends(get_db)]) -> dict:
    try:
        asset = asset_service.create_asset(
            db,
            name=payload.name,
            kind=payload.kind,
            identifier=payload.identifier,
            distance_unit=payload.distance_unit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return asset_service.asset_to_dict(db, asset)


def _get(db: Session, asset_id: int):
    try:
        return asset_service.get_asset(db, asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{asset_id}")
def get_asset(asset_id: int, db: Annotated[Session, Depends(get_db)]) -> dict:
    asset = _get(db, asset_id)
    return asset_service.asset_to_dict(db, asset, with_logs=True)


@router.patch("/{asset_id}", responses={400: {"description": "Bad request"}})
def update_asset(asset_id: int, payload: AssetUpdate, db: Annotated[Session, Depends(get_db)]) -> dict:
    asset = _get(db, asset_id)
    try:
        asset = asset_service.update_asset(db, asset, **payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return asset_service.asset_to_dict(db, asset)


@router.delete("/{asset_id}", status_code=204)
def delete_asset(asset_id: int, db: Annotated[Session, Depends(get_db)]) -> None:
    asset_service.delete_asset(db, _get(db, asset_id))


@router.get("/{asset_id}/logs")
def list_logs(asset_id: int, db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    _get(db, asset_id)
    return [asset_service.log_to_dict(lg) for lg in asset_service.list_logs(db, asset_id)]


@router.post("/{asset_id}/logs", status_code=201, responses={400: {"description": "Bad request"}})
def add_log(asset_id: int, payload: AssetLogCreate, db: Annotated[Session, Depends(get_db)]) -> dict:
    _get(db, asset_id)
    try:
        log = asset_service.add_log(db, asset_id, **payload.model_dump(exclude_unset=False))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return asset_service.log_to_dict(log)


@router.delete("/logs/{log_id}", status_code=204, responses={404: {"description": "Not found"}})
def delete_log(log_id: int, db: Annotated[Session, Depends(get_db)]) -> None:
    try:
        log = asset_service.get_log(db, log_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    asset_service.delete_log(db, log)
