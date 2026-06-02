"""FastAPI application entry point.

Serves the API under ``/api`` and the built React frontend under ``/`` so a
single container works behind Home Assistant ingress (spec §26.3). The frontend
is built with a relative base, so it loads correctly under any ingress path.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Import models so every table is registered on Base.metadata.
import app.models  # noqa: F401
from app import __version__
from app.api.router import api_router
from app.config import settings
from app.db import session as dbsession
from app.db.base import Base
from app.logging import configure_logging, get_logger

configure_logging(settings.log_level)
logger = get_logger("app.main")

# Built frontend lives here inside the add-on image (see addon/Dockerfile).
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info(
        "Starting %s v%s (privacy_mode=%s, db=%s)",
        settings.app_name,
        __version__,
        settings.privacy_mode.value,
        settings.database_path,
    )
    # Decide the engine state (plaintext, or encrypted+unlocked, or locked) from
    # the encryption marker (backlog #15b).
    dbsession.init()
    if dbsession.is_locked():
        logger.warning("Database is locked — waiting for unlock before serving data.")
    else:
        # Ensure tables exist. Alembic owns migrations in production; create_all
        # is an idempotent safety net so a fresh add-on starts.
        Base.metadata.create_all(bind=dbsession.get_engine())
        # Seed the default category library on first run (spec §15.4, §33).
        from app.services import mqtt_service, retention_service, settings_service
        from app.services.category_service import ensure_default_categories

        with dbsession.SessionLocal() as db:
            ensure_default_categories(db)
            # Honour a log level the owner saved in Settings (overrides the env
            # default applied at import). No-op if unset.
            from app.logging import set_level
            set_level(settings_service.get_log_level(db))
            # Apply the data-retention policy (backlog #78): archive everything due,
            # purge only auto_purge types. No-op unless a policy is set.
            retention_service.run_safe(db)
            # Publish MQTT sensors on startup (spec §27.1). No-op unless enabled.
            mqtt_service.publish_safe(db)
    yield
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# When the database is locked (encrypted, awaiting unlock), block data APIs with
# 423 — but allow health and the security/unlock endpoints through (#15b).
_LOCK_EXEMPT = ("/api/health", "/api/security")

# Endpoints reachable regardless of approval status: health, the lock/unlock
# routes, and ``/api/users/me`` (so a pending user can learn they're pending).
_GATE_EXEMPT = ("/api/health", "/api/security", "/api/users/me")

# Self-service account endpoints (MFA enrol/verify/disable): a user must reach
# these to satisfy the MFA gate or manage their own factor, so they bypass the
# MFA-presence and read-only gates (still approval-gated).
_SELF_SERVICE = ("/api/auth/mfa",)

# Methods that don't mutate data (read-only roles are allowed these).
_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

# The `child` role is a deliberately narrow allowance view: it may only reach its
# own allowance summary (plus the gate-exempt /users/me, /security, /auth/mfa).
# Everything else under /api is 403 for a child. Mirrors `childVisible` in
# frontend/src/nav.ts — keep the two in sync.
_CHILD_ALLOWED_PREFIXES = ("/api/allowance/summary",)


@app.middleware("http")
async def _lock_guard(request: Request, call_next):
    if dbsession.is_locked() and request.url.path.startswith("/api/"):
        if not request.url.path.startswith(_LOCK_EXEMPT):
            return JSONResponse(
                status_code=423,
                content={"detail": "Database is locked. Unlock with your passphrase."},
            )
    return await call_next(request)


@app.middleware("http")
async def _auth_guard(request: Request, call_next):
    """Resolve the current user (HA ingress identity → User) and enforce access
    control on data APIs: pending/disabled accounts are blocked, and read-only
    roles (viewer/child) may only issue safe methods (spec §28; backlog #82/#126).
    """
    path = request.url.path
    # Skip non-API paths and anything while the DB is locked (the lock guard owns
    # that case and the auth lookup needs a live DB).
    if not path.startswith("/api/") or dbsession.is_locked():
        return await call_next(request)

    from app.services import auth_service, mfa_service

    with dbsession.SessionLocal() as db:
        user = auth_service.resolve_current_user(db, request)
        db.commit()
        request.state.user_id = user.id
        request.state.user_role = user.role
        request.state.user_status = user.status
        request.state.user_name = user.display_name
        # MFA presence for the entry gate (only matters if the user enabled it).
        mfa_ok = not user.mfa_enabled or mfa_service.has_valid_session(
            db, user.id, request.headers.get(auth_service.SESSION_HEADER)
        )

    if path.startswith(_GATE_EXEMPT):
        return await call_next(request)

    if request.state.user_status != "approved":
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "Your account is awaiting approval by an administrator."
                    if request.state.user_status == "pending"
                    else "Your account has been disabled."
                ),
                "account_status": request.state.user_status,
            },
        )

    if not mfa_ok and not path.startswith(_SELF_SERVICE):
        return JSONResponse(
            status_code=403,
            content={"detail": "Two-factor verification required.", "mfa_required": True},
        )

    if (
        request.method not in _SAFE_METHODS
        and not auth_service.can_write(request.state.user_role)
        and not path.startswith(_SELF_SERVICE)
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "Your role is read-only and cannot make changes."},
        )

    # Child role: confined to its own allowance view (defence in depth — the nav
    # also hides everything else, but the API must not rely on the client).
    if request.state.user_role == "child" and not path.startswith(_CHILD_ALLOWED_PREFIXES):
        return JSONResponse(
            status_code=403,
            content={"detail": "This area isn't available for your account."},
        )

    return await call_next(request)


# Mutating methods worth an audit-trail entry (reads are too noisy to log).
_AUDIT_METHODS = ("POST", "PUT", "PATCH", "DELETE")


@app.middleware("http")
async def _audit_actions(request: Request, call_next):
    """Record every mutating API call to the audit log (backlog: "track all user +
    API actions"). Registered last so it's the OUTERMOST middleware: it runs after
    the auth guard has resolved the actor and after the route has set the final
    status. Best-effort — an audit write must never break the request, and no
    request body is logged (privacy)."""
    response = await call_next(request)
    try:
        path = request.url.path
        if (
            request.method in _AUDIT_METHODS
            and path.startswith("/api/")
            and not dbsession.is_locked()
        ):
            from app.services import audit_service

            with dbsession.SessionLocal() as db:
                audit_service.record_api_action(
                    db,
                    actor=getattr(request.state, "user_name", None),
                    method=request.method,
                    path=path,
                    status=response.status_code,
                )
                db.commit()
    except Exception:  # pragma: no cover - audit must never break the request
        logger.warning("API-action audit failed", exc_info=True)
    return response


@app.exception_handler(dbsession.DatabaseLocked)
async def _locked_handler(_request: Request, _exc: dbsession.DatabaseLocked):
    return JSONResponse(status_code=423, content={"detail": "Database is locked."})


app.include_router(api_router)


# --- Frontend static serving (mounted only if a build exists) ---
if FRONTEND_DIST.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIST / "assets"),
        name="assets",
    )

    @app.get("/", include_in_schema=False)
    def serve_index() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str) -> FileResponse:
        """SPA fallback: serve the requested file if present, else index.html."""
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:  # pragma: no cover - dev convenience when frontend isn't built
    @app.get("/", include_in_schema=False)
    def serve_placeholder() -> dict:
        return {
            "message": f"{settings.app_name} backend is running.",
            "frontend": "not built — run `npm run build` in frontend/",
            "health": "/api/health",
        }


def run() -> None:
    """Console entry point used by the add-on (`python -m app.main`)."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    run()
