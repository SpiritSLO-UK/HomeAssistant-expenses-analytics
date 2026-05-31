"""Aggregate API router. New route modules are included here as they land."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    routes_backup,
    routes_categories,
    routes_dashboard,
    routes_health,
    routes_imports,
    routes_transactions,
    routes_vendors,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(routes_health.router)
api_router.include_router(routes_imports.router)
api_router.include_router(routes_transactions.router)
api_router.include_router(routes_categories.router)
api_router.include_router(routes_vendors.router)
api_router.include_router(routes_dashboard.router)
api_router.include_router(routes_backup.router)
