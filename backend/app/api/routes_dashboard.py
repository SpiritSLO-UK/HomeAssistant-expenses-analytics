"""Dashboard API routes (spec §24.12)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.dashboard import (
    CategoryBreakdownItem,
    DashboardSummary,
    VendorBreakdownItem,
)
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _ref(month: date | None) -> date:
    return month or date.today()


@router.get("/summary", response_model=DashboardSummary)
def summary(month: date | None = None, db: Session = Depends(get_db)) -> dict:
    return dashboard_service.summary(db, _ref(month))


@router.get("/categories", response_model=list[CategoryBreakdownItem])
def categories(month: date | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return dashboard_service.category_breakdown(db, _ref(month))


@router.get("/vendors", response_model=list[VendorBreakdownItem])
def vendors(month: date | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return dashboard_service.vendor_breakdown(db, _ref(month))
