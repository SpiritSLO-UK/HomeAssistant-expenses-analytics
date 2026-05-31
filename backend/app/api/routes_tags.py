"""Tags API routes (spec §18.3, §12.13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Tag
from app.schemas.tags import TagIn, TagOut, TagUpdate
from app.services import tag_service

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagOut])
def list_tags(db: Session = Depends(get_db)) -> list[Tag]:
    return tag_service.list_tags(db)


@router.post("", response_model=TagOut, status_code=201)
def create_tag(payload: TagIn, db: Session = Depends(get_db)) -> Tag:
    tag = tag_service.get_or_create(db, payload.name, payload.colour)
    db.commit()
    db.refresh(tag)
    return tag


@router.patch("/{tag_id}", response_model=TagOut)
def update_tag(tag_id: int, payload: TagUpdate, db: Session = Depends(get_db)) -> Tag:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return tag_service.update_tag(db, tag, name=payload.name, colour=payload.colour)


@router.delete("/{tag_id}", status_code=204)
def delete_tag(tag_id: int, db: Session = Depends(get_db)) -> None:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    tag_service.delete_tag(db, tag)
