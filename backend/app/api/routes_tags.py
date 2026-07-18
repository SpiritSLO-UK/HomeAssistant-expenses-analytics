"""Tags API routes (spec §18.3, §12.13)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Tag
from app.schemas.tags import (
    DeletedCount,
    MergeTagsRequest,
    TagIn,
    TagOut,
    TagUpdate,
    TagUsage,
)
from app.services import tag_service

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagOut])
def list_tags(db: Annotated[Session, Depends(get_db)]) -> list[Tag]:
    return tag_service.list_tags(db)


# Static sub-paths are declared before the dynamic /{tag_id} routes so "usage" and
# "unused" are never mistaken for a tag id.
@router.get("/usage", response_model=list[TagUsage])
def tag_usage(db: Annotated[Session, Depends(get_db)]) -> list[TagUsage]:
    counts = tag_service.usage_counts(db)
    return [TagUsage(id=tag_id, count=count) for tag_id, count in counts.items()]


@router.post("/merge", response_model=TagOut, responses={400: {"description": "Bad request"}})
def merge_tags(payload: MergeTagsRequest, db: Annotated[Session, Depends(get_db)]) -> Tag:
    try:
        return tag_service.merge_tags(db, payload.source_id, payload.target_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/unused", response_model=DeletedCount)
def delete_unused_tags(db: Annotated[Session, Depends(get_db)]) -> DeletedCount:
    return DeletedCount(deleted=tag_service.delete_unused(db))


@router.post("", response_model=TagOut, status_code=201, responses={400: {"description": "Bad request"}})
def create_tag(payload: TagIn, db: Annotated[Session, Depends(get_db)]) -> Tag:
    try:
        tag = tag_service.get_or_create(db, payload.name, payload.colour)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(tag)
    return tag


@router.patch(
    "/{tag_id}",
    response_model=TagOut,
    responses={400: {"description": "Bad request"}, 404: {"description": "Not found"}},
)
def update_tag(tag_id: int, payload: TagUpdate, db: Annotated[Session, Depends(get_db)]) -> Tag:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    try:
        return tag_service.update_tag(db, tag, name=payload.name, colour=payload.colour)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{tag_id}", status_code=204, responses={404: {"description": "Not found"}})
def delete_tag(tag_id: int, db: Annotated[Session, Depends(get_db)]) -> None:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    tag_service.delete_tag(db, tag)
