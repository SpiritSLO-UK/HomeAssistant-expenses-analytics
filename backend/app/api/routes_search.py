"""Global search API (backlog: find any transaction/vendor/category/project)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import auth_service, search_service

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
def search(request: Request, q: str = "", limit: int = 8, db: Session = Depends(get_db)) -> dict:
    """Search transactions (scoped to the caller's visible accounts), vendors,
    categories and projects. Returns grouped results."""
    scope = auth_service.visible_account_scope(request, db)
    return search_service.search(db, q, account_ids=scope, limit=max(1, min(limit, 25)))
