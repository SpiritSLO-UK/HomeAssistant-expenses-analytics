"""Settings API (spec §24.2, §38; backlog #29)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.logging import set_level as set_log_level
from app.models import User
from app.services import auth_service, fx_service, mqtt_service, ocr_service, settings_service

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
    ocr_enabled: bool | None = None  # Settings → Services on/off for receipt OCR
    log_level: str | None = None  # DEBUG | INFO | WARNING | ERROR


@router.get("")
def get_settings(db: Session = Depends(get_db)) -> dict:
    return settings_service.get_all(db)


@router.get("/currencies")
def supported_currencies() -> list[dict]:
    """The curated base-currency choices for the Settings dropdown (top-10)."""
    return settings_service.SUPPORTED_CURRENCIES


@router.get("/services")
def services_status(db: Session = Depends(get_db)) -> dict:
    """Unified on/off + status for every service, for the Settings → Services panel
    (backlog §38). AI / OCR / online FX are runtime-toggleable; MQTT is configured
    in the add-on options, so it's shown read-only."""
    privacy = settings_service.get_privacy_mode(db)
    fx_mode = settings_service.get_fx_mode(db)
    ocr = ocr_service.status()
    mqtt = mqtt_service.status(db)
    ai_detail = (
        "Off — no AI calls" if privacy == "no_ai"
        else "On — local only, never sent to a cloud provider" if privacy == "strict_local"
        else f"On — mode: {privacy}"
    )
    return {
        "ai": {"enabled": privacy != "no_ai", "mode": privacy, "configurable": True, "detail": ai_detail},
        "ocr": {
            "enabled": settings_service.get_ocr_enabled(db),
            "configurable": True,
            "detail": "Engine ready" if ocr["available"] else "No OCR engine installed — manual entry only",
        },
        "fx": {
            "enabled": fx_mode == "frankfurter",
            "mode": fx_mode,
            "configurable": True,
            "detail": "Live rates (Frankfurter)" if fx_mode == "frankfurter" else "Manual rates only (no network)",
        },
        "mqtt": {
            "enabled": bool(mqtt.get("enabled")),
            "configurable": False,
            "detail": "Publishing to Home Assistant" if mqtt.get("enabled") else "Off — enable in the add-on options",
        },
    }


@router.put("")
def update_settings(
    payload: SettingsUpdate,
    db: Session = Depends(get_db),
    _user: User = Depends(auth_service.require_settings_manager),
) -> dict:
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

    if payload.ocr_enabled is not None:
        settings_service.set_value(db, settings_service.OCR_ENABLED, "true" if payload.ocr_enabled else "false")

    if payload.log_level is not None:
        level = payload.log_level.strip().upper()
        if level not in settings_service.LOG_LEVELS:
            raise HTTPException(
                status_code=400,
                detail=f"log_level must be one of {sorted(settings_service.LOG_LEVELS)}",
            )
        settings_service.set_value(db, settings_service.LOG_LEVEL, level)
        set_log_level(level)  # take effect immediately

    if payload.base_currency is not None:
        new_base = payload.base_currency.strip().upper()
        old_base = settings_service.get_base_currency(db)
        # Must be one of the curated choices (the Settings dropdown). The current
        # base is always allowed so an unusual legacy value can never lock you out.
        if new_base not in settings_service.SUPPORTED_CURRENCY_CODES and new_base != old_base:
            raise HTTPException(
                status_code=400,
                detail=f"base_currency must be one of {sorted(settings_service.SUPPORTED_CURRENCY_CODES)}",
            )
        settings_service.set_value(db, settings_service.BASE_CURRENCY, new_base)
        if new_base != old_base:
            # Re-convert everything against the new base (backlog #29).
            recompute = fx_service.recompute_all(db, new_base, settings_service.get_fx_mode(db))

    result = settings_service.get_all(db)
    if recompute is not None:
        result["recompute"] = recompute
    return result
