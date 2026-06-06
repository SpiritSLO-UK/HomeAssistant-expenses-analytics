"""MQTT publishing API (spec §27). Status, a manual publish, and a payload
preview that works whether or not a broker is configured."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.models import User
from app.services import auth_service, mqtt_service, settings_service

router = APIRouter(prefix="/mqtt", tags=["mqtt"])


@router.get("/status")
def mqtt_status(db: Annotated[Session, Depends(get_db)]) -> dict:
    return mqtt_service.status(db)


@router.get("/sensors")
def mqtt_sensors(db: Annotated[Session, Depends(get_db)]) -> dict:
    """The publishable sensors + which groups/sensors are currently disabled, so the
    Settings UI can show per-group and per-sensor toggles."""
    return mqtt_service.list_sensors(db)


class PublishSelectionIn(BaseModel):
    disabled_groups: list[str] = []
    disabled_sensors: list[str] = []


@router.put("/sensors")
def set_mqtt_sensors(
    payload: PublishSelectionIn,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(auth_service.require_settings_manager)],
) -> dict:
    """Choose what gets published to MQTT (backlog): a per-group and/or per-sensor
    denylist. Manager-gated. Returns the refreshed sensor list."""
    settings_service.set_mqtt_publish_selection(
        db, groups=payload.disabled_groups, sensors=payload.disabled_sensors
    )
    return mqtt_service.list_sensors(db)


@router.get("/preview")
def mqtt_preview(db: Annotated[Session, Depends(get_db)]) -> dict:
    """The sensor state + discovery messages that would be published. Useful for
    the UI and for verifying without a live broker."""
    return {
        "state": mqtt_service.build_state(db),
        "discovery": mqtt_service.build_discovery(db),
    }


@router.post("/publish", responses={400: {"description": "Bad request"}, 502: {"description": "Upstream error"}})
def mqtt_publish(db: Annotated[Session, Depends(get_db)]) -> dict:
    """Publish now (spec §27.1 manual trigger)."""
    if not settings.mqtt_enabled:
        raise HTTPException(status_code=400, detail="MQTT is disabled (set mqtt_enabled).")
    try:
        return mqtt_service.publish_all(db)
    except Exception as exc:  # connection/auth/broker errors
        raise HTTPException(status_code=502, detail=f"MQTT publish failed: {exc}") from exc
