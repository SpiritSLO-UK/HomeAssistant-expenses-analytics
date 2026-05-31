"""At-rest encryption / lock API (backlog #15b).

These routes must work while the database is locked, so they never depend on a
DB session.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import security_service

router = APIRouter(prefix="/security", tags=["security"])


class Passphrase(BaseModel):
    passphrase: str


class EnableRequest(BaseModel):
    passphrase: str
    unlock_mode: str = "prompt"  # prompt | stored


@router.get("/status")
def status() -> dict:
    return security_service.status()


@router.post("/unlock")
def unlock(payload: Passphrase) -> dict:
    if not security_service.unlock(payload.passphrase):
        raise HTTPException(status_code=400, detail="Wrong passphrase.")
    return {"status": "unlocked"}


@router.post("/enable")
def enable(payload: EnableRequest) -> dict:
    try:
        security_service.enable_encryption(payload.passphrase, payload.unlock_mode)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "encrypted", "unlock_mode": payload.unlock_mode}


@router.post("/disable")
def disable(payload: Passphrase) -> dict:
    try:
        security_service.disable_encryption(payload.passphrase)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "decrypted"}
