"""Investments & pensions API: accounts, value snapshots, and holdings (§12.4, §27).

Investment and pension accounts are scoped exactly like every other account
(shared vs private; #66/#82): reads filter to the caller's visible set and writes
404 on an out-of-scope account so existence never leaks.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Holding
from app.schemas.investments import (
    HoldingCreate,
    HoldingOut,
    HoldingUpdate,
    InvestmentAccountCreate,
    InvestmentAccountOut,
    InvestmentHistory,
    InvestmentSummary,
    ValueAdjust,
    ValueCreate,
    ValueOut,
)
from app.services import auth_service, investment_service

router = APIRouter(prefix="/investments", tags=["investments"])


def _require_visible(request: Request, db: Session, account_id: int) -> None:
    """404 if the caller may not see this account (avoids leaking existence)."""
    scope = auth_service.visible_account_scope(request, db)
    if scope is not None and account_id not in scope:
        raise HTTPException(status_code=404, detail="Not an investment account")


def _holding_in_scope(request: Request, db: Session, holding_id: int) -> Holding:
    try:
        holding = investment_service.get_holding(db, holding_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _require_visible(request, db, holding.account_id)
    return holding


def _value_tracked_account(request: Request, db: Session, account_id: int):
    """Fetch a visible investment/pension account for the cash-value endpoints.

    Investment accounts (stocks/shares/ISA) are valued by their holdings × price —
    a typed-in cash value or +/- contribution is a pension/cash model, so we reject
    it with 400 here. Pensions keep the statement-value flow."""
    _require_visible(request, db, account_id)
    try:
        account = investment_service.get_investment_account(db, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if account.account_type == "investment":
        raise HTTPException(
            status_code=400,
            detail="Investment accounts are valued by their holdings — add holdings instead of a cash value.",
        )
    return account


@router.get("/summary", response_model=InvestmentSummary)
def summary(request: Request, db: Annotated[Session, Depends(get_db)]) -> dict:
    return investment_service.summary(db, account_ids=auth_service.visible_account_scope(request, db))


@router.get("/history", response_model=InvestmentHistory)
def history(request: Request, db: Annotated[Session, Depends(get_db)], days: int = 365) -> dict:
    """Portfolio value over time + day/month/year change (for the charts)."""
    return investment_service.history(db, account_ids=auth_service.visible_account_scope(request, db), days=days)


# --- Price feed (optional; spec §27) ---


@router.get("/price-status")
def price_status(db: Annotated[Session, Depends(get_db)]) -> dict:
    """The configured price source + whether a sync can run (no network call)."""
    return investment_service.price_status(db)


@router.post("/sync-prices")
def sync_prices(request: Request, db: Annotated[Session, Depends(get_db)]) -> dict:
    """Fetch the latest quotes for the caller's visible holdings (no-op when the
    price source is manual / unconfigured). Only ticker symbols leave the box."""
    scope = auth_service.visible_account_scope(request, db)
    return investment_service.sync_prices(db, account_ids=scope)


# --- Accounts ---


@router.get("/accounts", response_model=list[InvestmentAccountOut])
def list_accounts(request: Request, db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    scope = auth_service.visible_account_scope(request, db)
    return [investment_service.account_to_dict(db, a) for a in investment_service.list_accounts(db, account_ids=scope)]


@router.post(
    "/accounts", response_model=InvestmentAccountOut, status_code=201, responses={400: {"description": "Bad request"}}
)
def create_account(payload: InvestmentAccountCreate, db: Annotated[Session, Depends(get_db)]) -> dict:
    try:
        account = investment_service.create_account(
            db,
            name=payload.name,
            institution=payload.institution,
            currency=payload.currency,
            account_type=payload.account_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return investment_service.account_to_dict(db, account)


# --- Value snapshots ---


@router.get(
    "/accounts/{account_id}/values", response_model=list[ValueOut], responses={404: {"description": "Not found"}}
)
def value_history(account_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    _require_visible(request, db, account_id)
    try:
        investment_service.get_investment_account(db, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return investment_service.value_history(db, account_id)


@router.post(
    "/accounts/{account_id}/values",
    response_model=ValueOut,
    status_code=201,
    responses={400: {"description": "Bad request"}, 404: {"description": "Not found"}},
)
def record_value(account_id: int, payload: ValueCreate, request: Request, db: Annotated[Session, Depends(get_db)]):
    _value_tracked_account(request, db, account_id)
    return investment_service.record_value(
        db, account_id, as_of=payload.as_of_date, value=payload.value, note=payload.note
    )


@router.post(
    "/accounts/{account_id}/adjust",
    response_model=ValueOut,
    status_code=201,
    responses={400: {"description": "Bad request"}, 404: {"description": "Not found"}},
)
def adjust_value(account_id: int, payload: ValueAdjust, request: Request, db: Annotated[Session, Depends(get_db)]):
    """Record a contribution/withdrawal — a new snapshot at latest ± amount."""
    _value_tracked_account(request, db, account_id)
    if payload.direction not in ("contribution", "withdrawal"):
        raise HTTPException(status_code=400, detail="direction must be 'contribution' or 'withdrawal'")
    delta = payload.amount if payload.direction == "contribution" else -payload.amount
    return investment_service.adjust_value(db, account_id, delta=delta, note=payload.note)


# --- Holdings ---


@router.get(
    "/accounts/{account_id}/holdings", response_model=list[HoldingOut], responses={404: {"description": "Not found"}}
)
def list_holdings(account_id: int, request: Request, db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    _require_visible(request, db, account_id)
    try:
        investment_service.get_investment_account(db, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [investment_service.holding_to_dict(h) for h in investment_service.list_holdings(db, account_id)]


@router.post(
    "/accounts/{account_id}/holdings",
    response_model=HoldingOut,
    status_code=201,
    responses={404: {"description": "Not found"}},
)
def create_holding(
    account_id: int, payload: HoldingCreate, request: Request, db: Annotated[Session, Depends(get_db)]
) -> dict:
    _require_visible(request, db, account_id)
    try:
        holding = investment_service.create_holding(
            db,
            account_id,
            symbol=payload.symbol,
            name=payload.name,
            units=payload.units,
            avg_cost=payload.avg_cost,
            last_price=payload.last_price,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return investment_service.holding_to_dict(holding)


@router.patch("/holdings/{holding_id}", response_model=HoldingOut, responses={404: {"description": "Not found"}})
def update_holding(
    holding_id: int, payload: HoldingUpdate, request: Request, db: Annotated[Session, Depends(get_db)]
) -> dict:
    holding = _holding_in_scope(request, db, holding_id)
    holding = investment_service.update_holding(db, holding, **payload.model_dump(exclude_unset=True))
    return investment_service.holding_to_dict(holding)


@router.delete("/holdings/{holding_id}", status_code=204, responses={404: {"description": "Not found"}})
def delete_holding(holding_id: int, request: Request, db: Annotated[Session, Depends(get_db)]) -> None:
    holding = _holding_in_scope(request, db, holding_id)
    investment_service.delete_holding(db, holding)
