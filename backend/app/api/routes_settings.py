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
    receipt_match_mode: str | None = None  # suggest | auto
    # AI (spec §22). privacy_mode gates AI entirely; off by default.
    privacy_mode: str | None = None
    ai_provider: str | None = None  # none | openai_compatible
    ai_base_url: str | None = None
    ai_model: str | None = None


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

    if payload.receipt_match_mode is not None:
        if payload.receipt_match_mode not in settings_service.RECEIPT_MATCH_MODES:
            raise HTTPException(status_code=400, detail="receipt_match_mode must be 'suggest' or 'auto'")
        settings_service.set_value(db, settings_service.RECEIPT_MATCH_MODE, payload.receipt_match_mode)

    if payload.privacy_mode is not None:
        if payload.privacy_mode not in settings_service.PRIVACY_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"privacy_mode must be one of {sorted(settings_service.PRIVACY_MODES)}",
            )
        settings_service.set_value(db, settings_service.PRIVACY_MODE, payload.privacy_mode)

    if payload.ai_provider is not None:
        if payload.ai_provider not in settings_service.AI_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"ai_provider must be one of {sorted(settings_service.AI_PROVIDERS)}",
            )
        settings_service.set_value(db, settings_service.AI_PROVIDER, payload.ai_provider)

    if payload.ai_base_url is not None:
        settings_service.set_value(db, settings_service.AI_BASE_URL, payload.ai_base_url.strip())

    if payload.ai_model is not None:
        settings_service.set_value(db, settings_service.AI_MODEL, payload.ai_model.strip())

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
