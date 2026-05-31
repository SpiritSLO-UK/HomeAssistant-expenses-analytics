"""Aggregate API router. New route modules are included here as they land."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import routes_health

api_router = APIRouter(prefix="/api")
api_router.include_router(routes_health.router)
