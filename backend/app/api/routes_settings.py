"""Settings API (spec §24.2, §38; backlog #29)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.logging import set_level as set_log_level
from app.models import User
from app.services import ai_service, auth_service, fx_service, mqtt_service, ocr_service, settings_service

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
    investment_price_source: str | None = None  # manual | stooq | alphavantage
    default_vendor_country: str | None = None  # ISO-3166-1 alpha-2, or "" to clear
    paperless_url: str | None = None  # Paperless-ngx base URL (non-secret), or "" to clear


@router.get("")
def get_settings(db: Annotated[Session, Depends(get_db)]) -> dict:
    return settings_service.get_all(db)


@router.get("/currencies")
def supported_currencies() -> list[dict]:
    """The curated base-currency choices for the Settings dropdown (top-10)."""
    return settings_service.SUPPORTED_CURRENCIES


@router.get("/countries")
def supported_countries() -> list[dict]:
    """ISO-3166-1 alpha-2 countries (code + name) for the vendor / trip country
    pickers. Sorted by name; the "EU" pseudo-code is not a country, so it's omitted."""
    from app.services import geo

    return sorted(
        ({"code": c, "name": n} for c, n in geo.COUNTRY_NAMES.items() if c != "EU"),
        key=lambda x: x["name"],
    )


@router.get("/services")
def services_status(db: Annotated[Session, Depends(get_db)]) -> dict:
    """Unified on/off + status for every service, for the Settings → Services panel
    (backlog §38). AI / OCR / online FX are runtime-toggleable; MQTT is configured
    in the add-on options, so it's shown read-only."""
    fx_mode = settings_service.get_fx_mode(db)
    ocr = ocr_service.status()
    mqtt = mqtt_service.status(db)

    # AI: in the engine, strict_local AND no_ai both refuse AI (ai_service.OFF_MODES).
    # "On" therefore means a real mode is selected (local LLM / cloud); enabling needs
    # a mode + provider chosen in the AI card, so here we only report + offer "turn off".
    ai = ai_service.status(db)
    mode = ai["privacy_mode"]
    active = mode not in ai_service.OFF_MODES
    if not active:
        ai_detail = "Off — the assistant makes no suggestions (no model is called)"
    elif not ai["configured"]:
        ai_detail = f"Set to '{mode}', but no AI provider is configured yet — finish it in AI settings"
    elif ai["is_cloud"]:
        ai_detail = f"On — cloud ({mode}); payloads are redacted and every call is audited"
    else:
        ai_detail = "On — local LLM, on-device only"

    ocr_on = settings_service.get_ocr_enabled(db)
    if not ocr_on:
        ocr_detail = "Off — receipts are entered manually"
    elif ocr["available"]:
        ocr_detail = "On — OCR engine ready"
    else:
        ocr_detail = "On, but no OCR engine is installed — manual entry only"

    return {
        "ai": {
            "enabled": active,
            "mode": mode,
            "configured": ai["configured"],
            "configurable": True,
            "detail": ai_detail,
        },
        "ocr": {"enabled": ocr_on, "configurable": True, "detail": ocr_detail},
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


def _apply_fx_and_receipt(db: Session, payload: SettingsUpdate) -> None:
    """Validate + persist the FX mode and receipt-match mode fields."""
    if payload.fx_mode is not None:
        if payload.fx_mode not in settings_service.FX_MODES:
            raise HTTPException(status_code=400, detail="fx_mode must be 'manual' or 'frankfurter'")
        settings_service.set_value(db, settings_service.FX_MODE, payload.fx_mode)

    if payload.receipt_match_mode is not None:
        if payload.receipt_match_mode not in settings_service.RECEIPT_MATCH_MODES:
            raise HTTPException(status_code=400, detail="receipt_match_mode must be 'suggest' or 'auto'")
        settings_service.set_value(db, settings_service.RECEIPT_MATCH_MODE, payload.receipt_match_mode)


def _apply_ai_settings(db: Session, payload: SettingsUpdate) -> None:
    """Validate + persist the AI fields (privacy mode, provider, base URL, model)."""
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


def _apply_ocr_and_price_source(db: Session, payload: SettingsUpdate) -> None:
    """Validate + persist the OCR toggle and investment price-source fields."""
    if payload.ocr_enabled is not None:
        settings_service.set_value(db, settings_service.OCR_ENABLED, "true" if payload.ocr_enabled else "false")

    if payload.investment_price_source is not None:
        if payload.investment_price_source not in settings_service.INVESTMENT_PRICE_SOURCES:
            raise HTTPException(
                status_code=400,
                detail=f"investment_price_source must be one of {sorted(settings_service.INVESTMENT_PRICE_SOURCES)}",
            )
        settings_service.set_value(db, settings_service.INVESTMENT_PRICE_SOURCE, payload.investment_price_source)


def _apply_default_vendor_country(db: Session, payload: SettingsUpdate) -> None:
    """Validate + persist the default vendor country. ``""`` clears it; any other
    value must be a valid ISO-3166-1 alpha-2 code (the "EU" pseudo-code is not a
    country)."""
    if payload.default_vendor_country is None:
        return
    from app.services import geo

    code = payload.default_vendor_country.strip().upper()
    if code and (code == "EU" or code not in geo.COUNTRY_NAMES):
        raise HTTPException(
            status_code=400,
            detail="default_vendor_country must be a valid ISO-3166-1 alpha-2 country code",
        )
    settings_service.set_value(db, settings_service.DEFAULT_VENDOR_COUNTRY, code)


def _apply_paperless_url(db: Session, payload: SettingsUpdate) -> None:
    """Validate + persist the Paperless base URL (non-secret). ``""`` clears it
    (falls back to the env var); otherwise it must be an http(s) URL."""
    if payload.paperless_url is None:
        return
    url = payload.paperless_url.strip()
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="paperless_url must start with http:// or https://")
    settings_service.set_value(db, settings_service.PAPERLESS_URL, url.rstrip("/"))


def _apply_log_level(db: Session, payload: SettingsUpdate) -> None:
    """Validate + persist the log level and apply it immediately."""
    if payload.log_level is None:
        return
    level = payload.log_level.strip().upper()
    if level not in settings_service.LOG_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"log_level must be one of {sorted(settings_service.LOG_LEVELS)}",
        )
    settings_service.set_value(db, settings_service.LOG_LEVEL, level)
    set_log_level(level)  # take effect immediately


def _apply_base_currency(db: Session, payload: SettingsUpdate) -> dict | None:
    """Validate + persist the base currency; recompute conversions when it changes.

    Returns the recompute result when the base actually changed, else None."""
    if payload.base_currency is None:
        return None
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
        return fx_service.recompute_all(db, new_base, settings_service.get_fx_mode(db))
    return None


@router.put("", responses={400: {"description": "Bad request"}})
def update_settings(
    payload: SettingsUpdate,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(auth_service.require_settings_manager)],
) -> dict:
    _apply_fx_and_receipt(db, payload)
    _apply_ai_settings(db, payload)
    _apply_ocr_and_price_source(db, payload)
    _apply_default_vendor_country(db, payload)
    _apply_paperless_url(db, payload)
    _apply_log_level(db, payload)
    recompute = _apply_base_currency(db, payload)

    result: dict[str, object] = {**settings_service.get_all(db)}
    if recompute is not None:
        result["recompute"] = recompute
    return result
