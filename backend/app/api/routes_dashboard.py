"""Dashboard API routes (spec §24.12)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.dashboard import (
    CategoryBreakdownItem,
    CountryBreakdownItem,
    DashboardSummary,
    MemberBreakdown,
    MonthlySeries,
    OutliersResponse,
    ProcessingStats,
    VendorBreakdownItem,
)
from app.schemas.projects import ProjectTotal
from app.schemas.subscriptions import DashboardSubscriptions
from app.services import (
    analytics_service,
    auth_service,
    dashboard_service,
    project_service,
    settings_service,
    subscription_service,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _ref(month: date | None) -> date:
    return month or date.today()


def _scope(
    request: Request, db: Session, view: str = "all", member_id: int | None = None
) -> set[int] | None:
    """The caller's visible account-id set, narrowed by the Mine/Shared/All toggle
    or — when ``member_id`` is given — to that member's own accounts (#66/#82)."""
    return auth_service.resolved_account_scope(
        db, auth_service.get_current_user(request, db), view=view, member_id=member_id
    )


@router.get("/summary", response_model=DashboardSummary)
def summary(
    request: Request, month: date | None = None, view: str = "all",
    member_id: int | None = None, db: Session = Depends(get_db),
) -> dict:
    return dashboard_service.summary(db, _ref(month), account_ids=_scope(request, db, view, member_id))


@router.get("/categories", response_model=list[CategoryBreakdownItem])
def categories(
    request: Request, month: date | None = None, view: str = "all",
    member_id: int | None = None, db: Session = Depends(get_db),
) -> list[dict]:
    return dashboard_service.category_breakdown(db, _ref(month), account_ids=_scope(request, db, view, member_id))


@router.get("/vendors", response_model=list[VendorBreakdownItem])
def vendors(
    request: Request, month: date | None = None, view: str = "all",
    member_id: int | None = None, db: Session = Depends(get_db),
) -> list[dict]:
    return dashboard_service.vendor_breakdown(db, _ref(month), account_ids=_scope(request, db, view, member_id))


@router.get("/by-country", response_model=list[CountryBreakdownItem])
def by_country(
    request: Request, month: date | None = None, view: str = "all",
    member_id: int | None = None, db: Session = Depends(get_db),
) -> list[dict]:
    """Spend by country for the month — the spend-by-location map (spec §16.3)."""
    return dashboard_service.country_breakdown(db, _ref(month), account_ids=_scope(request, db, view, member_id))


@router.get("/projects", response_model=list[ProjectTotal])
def projects(request: Request, member_id: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    """Per-project totals for the dashboard "Project totals" card (spec §25.1).
    Projects span time, so this is all-time spend, not a single month."""
    return project_service.totals(db, account_ids=_scope(request, db, member_id=member_id))


@router.get("/subscriptions", response_model=DashboardSubscriptions)
def subscriptions(request: Request, member_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    """Active subscriptions + monthly-equivalent total (spec §25.1)."""
    return subscription_service.dashboard_summary(db, account_ids=_scope(request, db, member_id=member_id))


@router.get("/monthly", response_model=MonthlySeries)
def monthly(
    request: Request, months: int = 6, month: date | None = None, view: str = "all",
    member_id: int | None = None, db: Session = Depends(get_db),
) -> dict:
    """Spend/income/net time-series for the last N months + a trend summary
    (backlog #146). ``months`` is clamped to a sane 2–24."""
    return analytics_service.monthly_series(
        db, _ref(month), months=max(2, min(months, 24)),
        account_ids=_scope(request, db, view, member_id),
    )


@router.get("/outliers", response_model=OutliersResponse)
def outliers(
    request: Request, month: date | None = None,
    member_id: int | None = None, db: Session = Depends(get_db),
) -> dict:
    """Heads-up list: large charges, category spikes, new merchants, budget
    alerts (backlog #150). Conservative + gated on having enough history."""
    return analytics_service.outliers(db, _ref(month), account_ids=_scope(request, db, member_id=member_id))


@router.get("/by-member", response_model=MemberBreakdown)
def by_member(request: Request, month: date | None = None, db: Session = Depends(get_db)) -> dict:
    """Spend/income/net for the month broken down per household member, plus a
    "Shared / unassigned" row for unowned accounts. Each member's figures cover
    the accounts they own intersected with the caller's own visibility, so a
    member can never see another's private spend (the same boundary as the
    per-member filter)."""
    user = auth_service.get_current_user(request, db)
    ref = _ref(month)

    def _money(account_ids: set[int] | None) -> dict:
        s = dashboard_service.summary(db, ref, account_ids=account_ids)
        return {"spend": s["spend_this_month"], "income": s["income_this_month"], "net": s["net_this_month"]}

    rows: list[dict] = []
    for m in auth_service.list_members(db):
        scope = auth_service.member_account_scope(db, user, m.id)
        rows.append({"member_id": m.id, "display_name": m.display_name, "role": m.role, **_money(scope)})

    shared_scope = auth_service.unowned_account_scope(db, user)
    if shared_scope:
        rows.append({"member_id": None, "display_name": "Shared / unassigned", "role": None, **_money(shared_scope)})

    return {"month": ref.isoformat(), "currency": settings_service.get_base_currency(db), "members": rows}


@router.get("/processing", response_model=ProcessingStats)
def processing(db: Session = Depends(get_db)) -> dict:
    """Pipeline status: files/transactions imported, receipt OCR progress, and how
    many enrichment calls went through AI (cloud vs local) + the average AI
    turnaround. Household-wide system metrics (not account-scoped)."""
    return dashboard_service.processing_stats(db)
