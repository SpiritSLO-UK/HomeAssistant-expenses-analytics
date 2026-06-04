"""Projects API routes (spec §24.8, §18)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Project
from app.schemas.projects import (
    STATUSES,
    ProjectIn,
    ProjectOut,
    ProjectSummary,
    ProjectUpdate,
)
from app.services import auth_service, project_service
from app.services.household_service import get_or_create_default_household

router = APIRouter(prefix="/projects", tags=["projects"])

_NOT_FOUND = "Project not found"


def _check_status(status: str | None) -> None:
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown status. One of: {sorted(STATUSES)}")


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Annotated[Session, Depends(get_db)]) -> list[Project]:
    return list(db.scalars(select(Project).order_by(Project.name)).all())


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectIn, db: Annotated[Session, Depends(get_db)]) -> Project:
    _check_status(payload.status)
    household = get_or_create_default_household(db)
    project = Project(
        household_id=household.id,
        name=payload.name,
        description=payload.description,
        status=payload.status,
        budget_amount=payload.budget_amount,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}/summary", response_model=ProjectSummary, responses={404: {"description": "Not found"}})
def project_summary(project_id: int, request: Request, db: Annotated[Session, Depends(get_db)]) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    scope = auth_service.visible_account_scope(request, db)
    return project_service.summary(db, project, account_ids=scope)


@router.patch("/{project_id}", response_model=ProjectOut, responses={404: {"description": "Not found"}})
def update_project(project_id: int, payload: ProjectUpdate, db: Annotated[Session, Depends(get_db)]) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    data = payload.model_dump(exclude_unset=True)
    _check_status(data.get("status"))
    for field, value in data.items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204, responses={404: {"description": "Not found"}})
def delete_project(project_id: int, db: Annotated[Session, Depends(get_db)]) -> None:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    db.delete(project)
    db.commit()
