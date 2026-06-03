"""FX rates API (backlog #29)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import FxRate, Transaction
from app.services import fx_service, settings_service

router = APIRouter(prefix="/fx", tags=["fx"])


class FxRateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rate_date: date
    base: str
    quote: str
    rate: Decimal
    source: str


class ManualRate(BaseModel):
    rate_date: date
    quote: str  # the transaction/source currency
    rate: Decimal  # how many base-currency units per 1 quote unit
    base: str | None = None  # defaults to the household base currency


@router.get("/rates", response_model=list[FxRateOut])
def list_rates(db: Annotated[Session, Depends(get_db)]) -> list[FxRate]:
    return list(db.scalars(select(FxRate).order_by(desc(FxRate.rate_date), FxRate.quote)).all())


@router.post("/rates", response_model=FxRateOut, status_code=201)
def add_rate(payload: ManualRate, db: Annotated[Session, Depends(get_db)]) -> FxRate:
    base = (payload.base or settings_service.get_base_currency(db)).upper()
    return fx_service.set_manual_rate(db, payload.rate_date, base, payload.quote.upper(), payload.rate)


@router.post("/backfill")
def backfill(db: Annotated[Session, Depends(get_db)]) -> dict:
    base = settings_service.get_base_currency(db)
    mode = settings_service.get_fx_mode(db)
    return fx_service.backfill_missing(db, base, mode)


@router.get("/missing")
def missing(db: Annotated[Session, Depends(get_db)]) -> dict:
    count = db.scalar(select(func.count()).select_from(Transaction).where(Transaction.needs_rate.is_(True))) or 0
    return {"needs_rate": int(count)}
