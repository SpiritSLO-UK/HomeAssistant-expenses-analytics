"""CSV export API (spec §24.4, §25.1; backlog #132).

Exports the data behind the app's views: the (optionally filtered) transactions,
and the data behind the dashboard's category + trend charts. Responses carry a
UTF-8 BOM so they open cleanly in Excel, and a dated ``Content-Disposition``
filename.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import auth_service, export_service

router = APIRouter(prefix="/export", tags=["export"])


def _csv_response(text: str, stem: str) -> Response:
    filename = f"{stem}-{date.today().isoformat()}.csv"
    # utf-8-sig writes a BOM so Excel detects UTF-8 (£, é, …) correctly.
    body = text.encode("utf-8-sig")
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/transactions.csv")
def export_transactions(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    category_id: int | None = None,
    vendor_id: int | None = None,
    project_id: int | None = None,
    tag_id: int | None = None,
    member_id: int | None = None,
    needs_review: bool | None = None,
    uncategorised: bool | None = None,
    is_business: bool | None = None,
    amount_min: Decimal | None = None,
    amount_max: Decimal | None = None,
    search: str | None = None,
    include_archived: Annotated[bool, Query(description="Include archived (aged-out) transactions")] = False,
    ids: Annotated[
        list[int] | None,
        Query(description="Restrict export to these transaction ids (the ticked selection)"),
    ] = None,
) -> Response:
    """Export transactions as CSV, honouring the same filters as the list view.

    When ``ids`` is supplied the export is narrowed to that selection (still
    intersected with the caller's account scope); without it, all filtered rows
    are exported as before."""
    scope = auth_service.resolved_account_scope(db, auth_service.get_current_user(request, db), member_id=member_id)
    # Bound the selection so a hand-crafted request can't build an oversized query.
    selected_ids = ids[: export_service.MAX_EXPORT_IDS] if ids else None
    conditions = export_service.build_transaction_filters(
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        category_id=category_id,
        vendor_id=vendor_id,
        project_id=project_id,
        tag_id=tag_id,
        needs_review=needs_review,
        uncategorised=uncategorised,
        is_business=is_business,
        amount_min=amount_min,
        amount_max=amount_max,
        search=search,
        account_ids=scope,
        include_archived=include_archived,
        ids=selected_ids,
    )
    return _csv_response(export_service.transactions_csv(db, conditions), "transactions")


@router.get("/categories.csv")
def export_categories(request: Request, db: Annotated[Session, Depends(get_db)], month: date | None = None) -> Response:
    """Spending-by-category totals for a month (the data behind the chart)."""
    scope = auth_service.visible_account_scope(request, db)
    return _csv_response(
        export_service.category_breakdown_csv(db, month or date.today(), account_ids=scope), "categories"
    )


@router.get("/monthly.csv")
def export_monthly(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    months: Annotated[int, Query(ge=2, le=24)] = 6,
    month: date | None = None,
) -> Response:
    """The spend/income/net monthly trend series (the data behind the sparklines)."""
    scope = auth_service.visible_account_scope(request, db)
    return _csv_response(
        export_service.monthly_series_csv(db, month or date.today(), months, account_ids=scope), "monthly"
    )
