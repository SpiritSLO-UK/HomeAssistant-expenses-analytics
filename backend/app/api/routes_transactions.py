"""Transactions API routes (spec §24.4)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Transaction
from app.schemas.transactions import (
    TransactionListResponse,
    TransactionOut,
    TransactionUpdate,
)

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("", response_model=TransactionListResponse)
def list_transactions(
    db: Session = Depends(get_db),
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    category_id: int | None = None,
    vendor_id: int | None = None,
    project_id: int | None = None,
    needs_review: bool | None = None,
    amount_min: Decimal | None = None,
    amount_max: Decimal | None = None,
    search: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    conditions = []
    if date_from is not None:
        conditions.append(Transaction.transaction_date >= date_from)
    if date_to is not None:
        conditions.append(Transaction.transaction_date <= date_to)
    if account_id is not None:
        conditions.append(Transaction.account_id == account_id)
    if category_id is not None:
        conditions.append(Transaction.category_id == category_id)
    if vendor_id is not None:
        conditions.append(Transaction.merchant_id == vendor_id)
    if project_id is not None:
        conditions.append(Transaction.project_id == project_id)
    if needs_review is not None:
        conditions.append(Transaction.needs_review.is_(needs_review))
    if amount_min is not None:
        conditions.append(Transaction.amount >= amount_min)
    if amount_max is not None:
        conditions.append(Transaction.amount <= amount_max)
    if search:
        like = f"%{search}%"
        conditions.append(
            or_(
                Transaction.description_raw.ilike(like),
                Transaction.merchant_raw.ilike(like),
            )
        )

    base = select(Transaction)
    if conditions:
        base = base.where(*conditions)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return {
        "items": list(rows),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{transaction_id}", response_model=TransactionOut)
def get_transaction(transaction_id: int, db: Session = Depends(get_db)) -> Transaction:
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return txn


@router.patch("/{transaction_id}", response_model=TransactionOut)
def update_transaction(
    transaction_id: int, payload: TransactionUpdate, db: Session = Depends(get_db)
) -> Transaction:
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(txn, field, value)
    db.commit()
    db.refresh(txn)
    return txn


@router.delete("/{transaction_id}", status_code=204)
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)) -> None:
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    db.delete(txn)
    db.commit()
