"""Business / VAT expenses API (backlog: corporate receipts).

Read-only business-expense analytics (account-scoped, archived-excluded). The CSV
export reuses ``/api/export/transactions.csv?is_business=true`` (which now carries
the VAT + business columns).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import auth_service, business_service

router = APIRouter(prefix="/business", tags=["business"])


@router.get("/summary")
def summary(request: Request, db: Session = Depends(get_db)) -> dict:
    return business_service.summary(db, account_ids=auth_service.visible_account_scope(request, db))
