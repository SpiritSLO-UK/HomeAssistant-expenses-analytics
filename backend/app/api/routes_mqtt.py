"""MQTT publishing API (spec §27). Status, a manual publish, and a payload
preview that works whether or not a broker is configured."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.services import mqtt_service

router = APIRouter(prefix="/mqtt", tags=["mqtt"])


@router.get("/status")
def mqtt_status(db: Session = Depends(get_db)) -> dict:
    return mqtt_service.status(db)


@router.get("/preview")
def mqtt_preview(db: Session = Depends(get_db)) -> dict:
    """The sensor state + discovery messages that would be published. Useful for
    the UI and for verifying without a live broker."""
    return {
        "state": mqtt_service.build_state(db),
        "discovery": mqtt_service.build_discovery(db),
    }


@router.post("/publish")
def mqtt_publish(db: Session = Depends(get_db)) -> dict:
    """Publish now (spec §27.1 manual trigger)."""
    if not settings.mqtt_enabled:
        raise HTTPException(status_code=400, detail="MQTT is disabled (set mqtt_enabled).")
    try:
        return mqtt_service.publish_all(db)
    except Exception as exc:  # connection/auth/broker errors
        raise HTTPException(status_code=502, detail=f"MQTT publish failed: {exc}") from exc
