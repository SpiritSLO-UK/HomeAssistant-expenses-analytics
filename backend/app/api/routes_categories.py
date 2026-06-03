"""Categories API routes (spec §24.5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Category
from app.schemas.categories import (
    CategoryCreate,
    CategoryMerge,
    CategoryOut,
    CategoryPrivacyLevel,
    CategoryUpdate,
)
from app.services import category_service

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryOut])
def list_categories(
    include_inactive: bool = False, db: Session = Depends(get_db)
) -> list[Category]:
    return category_service.list_categories(db, include_inactive=include_inactive)


def _check_privacy(value: str | None) -> None:
    if value is not None and value not in category_service.PRIVACY_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"privacy_sensitivity must be one of {list(category_service.PRIVACY_LEVELS)}",
        )


@router.post("", response_model=CategoryOut, status_code=201)
def create_category(payload: CategoryCreate, db: Session = Depends(get_db)) -> Category:
    _check_privacy(payload.privacy_sensitivity)
    return category_service.create_category(db, payload.model_dump(exclude_unset=True))


@router.post("/import-library", response_model=dict)
def import_library(db: Session = Depends(get_db)) -> dict:
    created = category_service.import_library(db)
    return {"created": created}


@router.get("/privacy", response_model=dict)
def get_privacy_default(db: Session = Depends(get_db)) -> dict:
    """The household default cloud-AI privacy level (backlog #28)."""
    return {"level": category_service.get_privacy_default(db)}


@router.post("/privacy", response_model=dict)
def set_all_privacy(payload: CategoryPrivacyLevel, db: Session = Depends(get_db)) -> dict:
    """Apply one cloud-AI privacy level to every category at once + make it the
    default new categories inherit."""
    try:
        updated = category_service.set_all_privacy(db, payload.level)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"updated": updated, "level": payload.level}


@router.get("/{category_id}", response_model=CategoryOut)
def get_category(category_id: int, db: Session = Depends(get_db)) -> Category:
    category = category_service.get_category(db, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


@router.patch("/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: int, payload: CategoryUpdate, db: Session = Depends(get_db)
) -> Category:
    _check_privacy(payload.privacy_sensitivity)
    category = category_service.update_category(
        db, category_id, payload.model_dump(exclude_unset=True)
    )
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


@router.delete("/{category_id}", status_code=204)
def delete_category(category_id: int, db: Session = Depends(get_db)) -> None:
    if not category_service.delete_category(db, category_id):
        raise HTTPException(status_code=404, detail="Category not found")


@router.post("/{category_id}/merge", response_model=CategoryOut)
def merge_category(category_id: int, payload: CategoryMerge, db: Session = Depends(get_db)) -> Category:
    try:
        target = category_service.merge_category(db, category_id, payload.target_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if target is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return target
