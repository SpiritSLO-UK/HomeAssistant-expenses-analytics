"""Settings API (spec §24.2, §38; backlog #29)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import fx_service, settings_service

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    base_currency: str | None = None
    fx_mode: str | None = None  # manual | frankfurter


@router.get("")
def get_settings(db: Session = Depends(get_db)) -> dict:
    return settings_service.get_all(db)


@router.put("")
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)) -> dict:
    recompute = None
    if payload.fx_mode is not None:
        if payload.fx_mode not in settings_service.FX_MODES:
            raise HTTPException(status_code=400, detail="fx_mode must be 'manual' or 'frankfurter'")
        settings_service.set_value(db, settings_service.FX_MODE, payload.fx_mode)

    if payload.base_currency is not None:
        new_base = payload.base_currency.strip().upper()
        if len(new_base) != 3:
            raise HTTPException(status_code=400, detail="base_currency must be a 3-letter code")
        old_base = settings_service.get_base_currency(db)
        settings_service.set_value(db, settings_service.BASE_CURRENCY, new_base)
        if new_base != old_base:
            # Re-convert everything against the new base (backlog #29).
            recompute = fx_service.recompute_all(db, new_base, settings_service.get_fx_mode(db))

    result = settings_service.get_all(db)
    if recompute is not None:
        result["recompute"] = recompute
    return result
