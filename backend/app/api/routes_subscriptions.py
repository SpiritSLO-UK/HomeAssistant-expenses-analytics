"""Subscriptions API routes (spec §20, §24)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Subscription
from app.schemas.subscriptions import (
    FREQUENCIES,
    STATUSES,
    DetectResult,
    SubscriptionAlerts,
    SubscriptionOut,
    SubscriptionUpdate,
)
from app.services import auth_service, mqtt_service, subscription_service

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.get("", response_model=list[SubscriptionOut])
def list_subscriptions(request: Request, db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    # Show only subscriptions backed by a transaction the caller can see (#66/#82).
    visible = subscription_service.visible_subscription_ids(db, auth_service.visible_account_scope(request, db))
    subs = db.scalars(select(Subscription).order_by(Subscription.name)).all()
    return [subscription_service.to_dict(s) for s in subs if visible is None or s.id in visible]


@router.get("/alerts", response_model=SubscriptionAlerts)
def subscription_alerts(request: Request, db: Annotated[Session, Depends(get_db)], within_days: int = 7) -> dict:
    """Upcoming renewals + missed payments for active subscriptions (spec §20.3)."""
    scope = auth_service.visible_account_scope(request, db)
    return subscription_service.alerts(db, within_days=max(1, min(within_days, 60)), account_ids=scope)


@router.post("/detect", response_model=DetectResult)
def detect(db: Annotated[Session, Depends(get_db)]) -> dict:
    """Re-scan transactions for recurring payments (spec §20.1)."""
    result = subscription_service.detect(db)
    mqtt_service.publish_safe(db)  # subscriptions changed -> refresh sensor
    return result


@router.patch("/{subscription_id}", response_model=SubscriptionOut)
def update_subscription(
    subscription_id: int, payload: SubscriptionUpdate, db: Annotated[Session, Depends(get_db)]
) -> dict:
    sub = db.get(Subscription, subscription_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    data = payload.model_dump(exclude_unset=True)
    if data.get("status") is not None and data["status"] not in STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown status. One of: {sorted(STATUSES)}")
    if data.get("frequency") is not None and data["frequency"] not in FREQUENCIES:
        raise HTTPException(status_code=400, detail=f"Unknown frequency. One of: {sorted(FREQUENCIES)}")
    for field, value in data.items():
        setattr(sub, field, value)
    db.commit()
    db.refresh(sub)
    mqtt_service.publish_safe(db)
    return subscription_service.to_dict(sub)


@router.delete("/{subscription_id}", status_code=204)
def delete_subscription(subscription_id: int, db: Annotated[Session, Depends(get_db)]) -> None:
    sub = db.get(Subscription, subscription_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    db.delete(sub)
    db.commit()
    mqtt_service.publish_safe(db)
