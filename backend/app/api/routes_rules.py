"""Rules API routes (spec §24.7)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Rule
from app.schemas.rules import RuleCreate, RuleOut, RuleTestRequest, RuleUpdate
from app.services import rule_service

router = APIRouter(prefix="/rules", tags=["rules"])


def _validate(condition_type: str | None, action_type: str | None) -> None:
    if condition_type is not None and condition_type not in rule_service.CONDITION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported condition_type. Allowed: {sorted(rule_service.CONDITION_TYPES)}",
        )
    if action_type is not None and action_type not in rule_service.ACTION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported action_type. Allowed: {sorted(rule_service.ACTION_TYPES)}",
        )


@router.get("", response_model=list[RuleOut])
def list_rules(db: Annotated[Session, Depends(get_db)]) -> list[Rule]:
    return rule_service.list_rules(db)


@router.post("", response_model=RuleOut, status_code=201)
def create_rule(payload: RuleCreate, db: Annotated[Session, Depends(get_db)]) -> Rule:
    _validate(payload.condition_type, payload.action_type)
    return rule_service.create_rule(db, payload.model_dump(exclude_unset=True))


@router.post("/test")
def test_rule(payload: RuleTestRequest, db: Annotated[Session, Depends(get_db)]) -> dict:
    _validate(payload.condition_type, None)
    return rule_service.test_rule(db, payload.condition_type, payload.condition_value)


@router.get("/{rule_id}", response_model=RuleOut)
def get_rule(rule_id: int, db: Annotated[Session, Depends(get_db)]) -> Rule:
    rule = rule_service.get_rule(db, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.patch("/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: int, payload: RuleUpdate, db: Annotated[Session, Depends(get_db)]) -> Rule:
    _validate(payload.condition_type, payload.action_type)
    rule = rule_service.update_rule(db, rule_id, payload.model_dump(exclude_unset=True))
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Annotated[Session, Depends(get_db)]) -> None:
    if not rule_service.delete_rule(db, rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
