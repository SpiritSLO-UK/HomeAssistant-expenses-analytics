"""Transactions API routes (spec §24.4)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models import Project, Transaction, User
from app.schemas.tags import SetTagsRequest
from app.schemas.transactions import (
    SetSplitsRequest,
    SplitsResponse,
    TransactionDetailOut,
    TransactionListResponse,
    TransactionOut,
    TransactionUpdate,
)
from app.services import (
    audit_service,
    export_service,
    import_service,
    rule_service,
    split_service,
    tag_service,
    vendor_service,
)
from app.services.auth_service import get_current_user, visible_account_scope
from app.services.split_service import SplitError, SplitInput

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _get_visible_txn(request: Request, db: Session, transaction_id: int) -> Transaction:
    """Fetch a transaction the caller may see, or 404. Uses 404 (not 403) for a
    private transaction owned by someone else so existence isn't leaked (#66/#82)."""
    txn = db.get(Transaction, transaction_id)
    scope = visible_account_scope(request, db)
    if txn is None or (scope is not None and txn.account_id is not None and txn.account_id not in scope):
        raise HTTPException(status_code=404, detail="Transaction not found")
    return txn


class CategoriseRequest(BaseModel):
    category_id: int | None = None
    # Manual-correction learning (spec §15.3):
    learn_vendor: bool = False  # remember this category for the vendor
    learn_rule: bool = False  # also create a description rule so future ones auto-categorise
    rule_match_value: str | None = None  # optional override of the rule's match text


class BatchCategoriseRequest(BaseModel):
    transaction_ids: list[int]
    category_id: int | None = None


class RecategoriseRequest(BaseModel):
    only_uncategorised: bool = True


@router.get("", response_model=TransactionListResponse)
def list_transactions(
    request: Request,
    db: Session = Depends(get_db),
    transaction_id: int | None = Query(None, description="Narrow to a single transaction (focus deep-link)"),
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    category_id: int | None = None,
    vendor_id: int | None = None,
    project_id: int | None = None,
    tag_id: int | None = None,
    needs_review: bool | None = None,
    uncategorised: bool | None = Query(None, description="True = only rows with no category; False = only categorised"),
    is_business: bool | None = None,
    amount_min: Decimal | None = None,
    amount_max: Decimal | None = None,
    search: str | None = None,
    include_archived: bool = Query(False, description="Include archived (aged-out) transactions"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    conditions = export_service.build_transaction_filters(
        transaction_id=transaction_id,
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
        account_ids=visible_account_scope(request, db),
        include_archived=include_archived,
    )

    base = select(Transaction)
    if conditions:
        base = base.where(*conditions)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.options(selectinload(Transaction.tags))  # eager-load tags (no N+1)
        .order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return {
        "items": list(rows),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/recategorise")
def recategorise(payload: RecategoriseRequest, db: Session = Depends(get_db)) -> dict:
    """Re-run vendor + keyword categorisation (spec §15, §3.3 re-run rules)."""
    count = import_service.recategorise(db, only_uncategorised=payload.only_uncategorised)
    return {"recategorised": count}


@router.post("/categorise-batch")
def categorise_batch(payload: BatchCategoriseRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Bulk-assign a category to many transactions (spec §25.3)."""
    scope = visible_account_scope(request, db)
    rows = db.scalars(
        select(Transaction).where(Transaction.id.in_(payload.transaction_ids))
    ).all()
    visible = [t for t in rows if scope is None or t.account_id is None or t.account_id in scope]
    for txn in visible:
        txn.category_id = payload.category_id
        txn.confidence_score = 1.0  # manual assignment (spec §15.2)
    db.commit()
    return {"updated": len(visible)}


@router.get("/{transaction_id}", response_model=TransactionDetailOut)
def get_transaction(transaction_id: int, request: Request, db: Session = Depends(get_db)) -> Transaction:
    return _get_visible_txn(request, db, transaction_id)


@router.patch("/{transaction_id}", response_model=TransactionOut)
def update_transaction(
    transaction_id: int, payload: TransactionUpdate, request: Request, db: Session = Depends(get_db)
) -> Transaction:
    txn = _get_visible_txn(request, db, transaction_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("project_id") is not None and db.get(Project, data["project_id"]) is None:
        raise HTTPException(status_code=400, detail="Unknown project")
    for field, value in data.items():
        setattr(txn, field, value)
    db.commit()
    db.refresh(txn)
    return txn


@router.post("/{transaction_id}/unarchive", response_model=TransactionOut)
def unarchive_transaction(
    transaction_id: int, request: Request, db: Session = Depends(get_db)
) -> Transaction:
    """Restore an archived transaction so it reappears in lists and aggregates
    (retention, backlog #78). Write-gated by the auth middleware."""
    txn = _get_visible_txn(request, db, transaction_id)
    txn.archived_at = None
    db.commit()
    db.refresh(txn)
    return txn


@router.post("/{transaction_id}/categorise", response_model=TransactionOut)
def categorise(
    transaction_id: int, payload: CategoriseRequest, request: Request, db: Session = Depends(get_db)
) -> Transaction:
    txn = _get_visible_txn(request, db, transaction_id)
    txn.category_id = payload.category_id
    txn.confidence_score = 1.0  # manual assignment (spec §15.2)
    if payload.category_id is not None:
        if payload.learn_vendor:
            vendor_service.learn_vendor_category(
                db, txn.description_raw, txn.merchant_raw, payload.category_id
            )
        if payload.learn_rule:
            rule_service.create_rule_from_correction(
                db, txn, payload.category_id, payload.rule_match_value
            )
    db.commit()
    db.refresh(txn)
    return txn


def _splits_response(txn: Transaction) -> dict:
    return {
        "transaction_id": txn.id,
        "is_split": txn.is_split,
        "currency": txn.currency,
        "total": txn.amount,
        "splits": list(txn.splits),
    }


@router.get("/{transaction_id}/splits", response_model=SplitsResponse)
def get_splits(transaction_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    return _splits_response(_get_visible_txn(request, db, transaction_id))


@router.post("/{transaction_id}/split", response_model=SplitsResponse)
def set_splits(
    transaction_id: int, payload: SetSplitsRequest, request: Request, db: Session = Depends(get_db)
) -> dict:
    """Split a transaction across categories/projects (spec §17.2 validation)."""
    txn = _get_visible_txn(request, db, transaction_id)
    parts = [
        SplitInput(
            amount=s.amount,
            category_id=s.category_id,
            project_id=s.project_id,
            description=s.description,
            notes=s.notes,
        )
        for s in payload.splits
    ]
    try:
        split_service.set_splits(db, txn, parts)
    except SplitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _splits_response(txn)


@router.delete("/{transaction_id}/split", response_model=SplitsResponse)
def clear_splits(transaction_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    """Remove a transaction's splits (spec §17.3); its own category applies again."""
    txn = _get_visible_txn(request, db, transaction_id)
    split_service.clear_splits(db, txn)
    return _splits_response(txn)


@router.post("/{transaction_id}/tags", response_model=TransactionDetailOut)
def set_tags(
    transaction_id: int, payload: SetTagsRequest, request: Request, db: Session = Depends(get_db)
) -> Transaction:
    """Replace a transaction's tags (spec §18.3); unknown names are created."""
    txn = _get_visible_txn(request, db, transaction_id)
    tag_service.set_transaction_tags(db, txn, payload.tags)
    return txn


@router.delete("/{transaction_id}", status_code=204)
def delete_transaction(
    transaction_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    txn = _get_visible_txn(request, db, transaction_id)
    audit_service.record(
        db,
        actor=user.display_name,
        action="delete_transaction",
        entity_type="transaction",
        entity_id=transaction_id,
        details={"description": txn.description_raw, "amount": str(txn.amount)},
    )
    db.delete(txn)
    db.commit()
