"""Aggregate API router. New route modules are included here as they land."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    routes_accounts,
    routes_ai,
    routes_allowance,
    routes_auth,
    routes_backup,
    routes_budgets,
    routes_categories,
    routes_dashboard,
    routes_export,
    routes_fx,
    routes_health,
    routes_imports,
    routes_logs,
    routes_mqtt,
    routes_projects,
    routes_receipts,
    routes_review,
    routes_rules,
    routes_savings,
    routes_security,
    routes_settings,
    routes_subscriptions,
    routes_tags,
    routes_transactions,
    routes_users,
    routes_vendors,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(routes_health.router)
api_router.include_router(routes_imports.router)
api_router.include_router(routes_transactions.router)
api_router.include_router(routes_categories.router)
api_router.include_router(routes_vendors.router)
api_router.include_router(routes_dashboard.router)
api_router.include_router(routes_budgets.router)
api_router.include_router(routes_projects.router)
api_router.include_router(routes_tags.router)
api_router.include_router(routes_subscriptions.router)
api_router.include_router(routes_receipts.router)
api_router.include_router(routes_review.router)
api_router.include_router(routes_ai.router)
api_router.include_router(routes_mqtt.router)
api_router.include_router(routes_backup.router)
api_router.include_router(routes_settings.router)
api_router.include_router(routes_fx.router)
api_router.include_router(routes_rules.router)
api_router.include_router(routes_savings.router)
api_router.include_router(routes_security.router)
api_router.include_router(routes_users.router)
api_router.include_router(routes_auth.router)
api_router.include_router(routes_logs.router)
api_router.include_router(routes_export.router)
api_router.include_router(routes_allowance.router)
api_router.include_router(routes_accounts.router)
