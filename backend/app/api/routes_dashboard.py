"""Dashboard API routes (spec §24.12)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.dashboard import (
    CategoryBreakdownItem,
    DashboardSummary,
    MonthlySeries,
    OutliersResponse,
    VendorBreakdownItem,
)
from app.schemas.projects import ProjectTotal
from app.schemas.subscriptions import DashboardSubscriptions
from app.services import (
    analytics_service,
    dashboard_service,
    project_service,
    subscription_service,
)

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


@router.get("/projects", response_model=list[ProjectTotal])
def projects(db: Session = Depends(get_db)) -> list[dict]:
    """Per-project totals for the dashboard "Project totals" card (spec §25.1).
    Projects span time, so this is all-time spend, not a single month."""
    return project_service.totals(db)


@router.get("/subscriptions", response_model=DashboardSubscriptions)
def subscriptions(db: Session = Depends(get_db)) -> dict:
    """Active subscriptions + monthly-equivalent total (spec §25.1)."""
    return subscription_service.dashboard_summary(db)


@router.get("/monthly", response_model=MonthlySeries)
def monthly(
    months: int = 6, month: date | None = None, db: Session = Depends(get_db)
) -> dict:
    """Spend/income/net time-series for the last N months + a trend summary
    (backlog #146). ``months`` is clamped to a sane 2–24."""
    return analytics_service.monthly_series(db, _ref(month), months=max(2, min(months, 24)))


@router.get("/outliers", response_model=OutliersResponse)
def outliers(month: date | None = None, db: Session = Depends(get_db)) -> dict:
    """Heads-up list: large charges, category spikes, new merchants, budget
    alerts (backlog #150). Conservative + gated on having enough history."""
    return analytics_service.outliers(db, _ref(month))
