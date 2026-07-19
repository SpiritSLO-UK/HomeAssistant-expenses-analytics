"""FastAPI application entry point.

Serves the API under ``/api`` and the built React frontend under ``/`` so a
single container works behind Home Assistant ingress (spec §26.3). The frontend
is built with a relative base, so it loads correctly under any ingress path.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
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


def _run_startup_migrations() -> None:
    """Bring the schema to head against the active engine, or refuse to serve.

    A failed migration means the finance database may be inconsistent; serving
    the app anyway risks corrupting data or showing wrong figures. So we FAIL
    HARD (re-raise, which aborts startup and the container exits) unless the
    operator sets ``HAFI_ALLOW_MIGRATION_FAILURE=1`` for recovery: the same
    contract the removed ``run.sh`` step enforced, now owned by the app."""
    from app.db.migrations_runner import run_migrations

    try:
        run_migrations()
    except Exception:
        if os.environ.get("HAFI_ALLOW_MIGRATION_FAILURE") == "1":
            logger.exception(
                "Database migration failed, but HAFI_ALLOW_MIGRATION_FAILURE=1 is set: "
                "continuing in recovery mode. The database may be inconsistent.",
            )
            return
        logger.exception(
            "Database migration failed; refusing to start to avoid serving inconsistent "
            "data. Restart with HAFI_ALLOW_MIGRATION_FAILURE=1 to override for recovery.",
        )
        raise


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
        # Prompt mode (encrypted, no key): stay locked and serve only health +
        # the lock/unlock endpoints. Migrations run in security_service.unlock()
        # once the user supplies the passphrase, never against a locked DB.
        logger.warning("Database is locked — waiting for unlock before serving data.")
    else:
        # Run migrations against the ACTIVE engine (plaintext, or the unlocked
        # SQLCipher engine). This replaces the old `alembic upgrade head` in
        # run.sh, which could not open an encrypted DB and crash-looped on restart.
        _run_startup_migrations()
        # Ensure tables exist. Alembic owns migrations in production; create_all
        # is an idempotent safety net so a fresh add-on starts.
        Base.metadata.create_all(bind=dbsession.require_engine())
        # Seed the default category library on first run (spec §15.4, §33).
        from app.services import (
            investment_service,
            mqtt_service,
            retention_service,
            settings_service,
        )
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
            # Refresh investment prices on startup (spec §27). No-op unless a
            # price source is configured (default manual = no network).
            investment_service.sync_prices_safe(db)
    yield
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    lifespan=lifespan,
)

# CORS is only active for an explicitly configured cross-origin frontend (local dev;
# empty in production, where the UI is served same-origin). Even then, scope methods +
# headers to what the app actually uses rather than "*" with credentials — the wildcards
# were over-broad (CR-SEC-12). The frontend only sends Content-Type + the session header.
_CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_CORS_HEADERS = ["Content-Type", "X-HAFI-Session"]  # the MFA session header (auth_service.SESSION_HEADER)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=_CORS_METHODS,
        allow_headers=_CORS_HEADERS,
    )


# Common prefix for all data API routes.
_API_PREFIX = "/api/"
_HEALTH = "/api/health"

# When the database is locked (encrypted, awaiting unlock), block data APIs with
# 423 — but allow health and the security/unlock endpoints through (#15b).
_LOCK_EXEMPT = (_HEALTH, "/api/security")

# Endpoints reachable regardless of approval status: health, the lock/unlock
# routes, and ``/api/users/me`` (so a pending user can learn they're pending).
_GATE_EXEMPT = (_HEALTH, "/api/security", "/api/users/me")

# A DISABLED account keeps no access beyond seeing its own status — not even the
# otherwise gate-exempt /api/security, so a disabled owner can't use their retained
# admin role to manage the system (SR-6). Pending accounts are unaffected here.
_DISABLED_ALLOWED = (_HEALTH, "/api/users/me")

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


def _path_in(path: str, prefixes: tuple[str, ...]) -> bool:
    """True if ``path`` equals one of ``prefixes`` or is a descendant of it, matched
    on **path-segment boundaries**. Unlike a bare ``str.startswith`` this treats each
    prefix as whole segments, so ``/api/users/me`` matches only ``/api/users/me`` and
    ``/api/users/me/…`` — NOT sibling paths such as ``/api/users/members`` (the
    prefix-collision behind the CR-SEC-16 roster leak, #368). Genuine subtrees like
    ``/api/security`` and ``/api/allowance/summary`` still match their descendants."""
    return any(path == p or path.startswith(p + "/") for p in prefixes)


@app.middleware("http")
async def _lock_guard(request: Request, call_next):
    if dbsession.is_locked() and request.url.path.startswith(_API_PREFIX):
        if not _path_in(request.url.path, _LOCK_EXEMPT):
            return JSONResponse(
                status_code=423,
                content={"detail": "Database is locked. Unlock with your passphrase."},
            )
    return await call_next(request)


def _access_denied(request: Request, path: str) -> JSONResponse | None:
    """Apply the data-API access gates in order; return a 403 to block, or ``None``
    to allow. Split out of ``_auth_guard`` so each guard stays simple — it reads the
    per-request state the middleware stashed (status / role / mfa / blocked-prefixes)."""
    from app.services import auth_service

    st = request.state
    if st.user_status != "approved":
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "Your account is awaiting approval by an administrator."
                    if st.user_status == "pending"
                    else "Your account has been disabled."
                ),
                "account_status": st.user_status,
            },
        )
    # Admin-required MFA not yet enrolled (#157) — blocked until they set it up; the
    # MFA self-service endpoints (and gate-exempt /users/me) stay reachable to enrol.
    if getattr(st, "mfa_setup_required", False) and not _path_in(path, _SELF_SERVICE):
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Your administrator requires two-factor authentication — set it up to continue.",
                "mfa_setup_required": True,
            },
        )
    # MFA entry gate (the user has MFA on but this request lacks a valid session).
    if not getattr(st, "mfa_ok", True) and not _path_in(path, _SELF_SERVICE):
        return JSONResponse(
            status_code=403,
            content={"detail": "Two-factor verification required.", "mfa_required": True},
        )
    # Read-only roles (viewer/child) may only issue safe methods.
    if (
        request.method not in _SAFE_METHODS
        and not auth_service.can_write(st.user_role)
        and not _path_in(path, _SELF_SERVICE)
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "Your role is read-only and cannot make changes."},
        )
    # Child role: confined to its own allowance view (defence in depth — the nav
    # also hides everything else, but the API must not rely on the client).
    if st.user_role == "child" and not _path_in(path, _CHILD_ALLOWED_PREFIXES):
        return JSONResponse(
            status_code=403,
            content={"detail": "This area isn't available for your account."},
        )
    # Per-user blocked pages (#108): the owner can restrict an individual non-admin
    # user from specific pages — enforced here, not just hidden in the sidebar.
    if _path_in(path, tuple(getattr(st, "user_blocked_prefixes", ()))):
        return JSONResponse(
            status_code=403,
            content={"detail": "This area isn't available for your account."},
        )
    return None


@app.middleware("http")
async def _auth_guard(request: Request, call_next):
    """Resolve the current user (HA ingress identity → User) and enforce access
    control on data APIs: pending/disabled accounts are blocked, and read-only
    roles (viewer/child) may only issue safe methods (spec §28; backlog #82/#126).
    """
    path = request.url.path
    # Skip non-API paths, the public health probe, and anything while the DB is
    # locked (the lock guard owns that case and the auth lookup needs a live DB).
    # Health must be exempt: the container HEALTHCHECK polls it from inside the
    # container with no HA ingress headers, which would otherwise resolve to the
    # "local" fallback identity and spawn a bogus pending user on every probe.
    if not path.startswith(_API_PREFIX) or path == _HEALTH or dbsession.is_locked():
        return await call_next(request)

    from app.services import auth_service, mfa_service

    with dbsession.SessionLocal() as db:
        user = auth_service.resolve_current_user(db, request)
        # Only commit when the guard actually changed something (new user, a
        # throttled last_seen refresh, or an external-id/display update). Most GETs
        # leave the session clean and shouldn't pay for a write (CR-FEAT-4).
        if db.new or db.dirty or db.deleted:
            db.commit()
        request.state.user_id = user.id
        request.state.user_role = user.role
        request.state.user_status = user.status
        request.state.user_name = user.display_name
        # Per-user blocked pages (#108): API prefixes this user may not reach.
        request.state.user_blocked_prefixes = auth_service.blocked_api_prefixes(user)
        # Admin-required MFA not yet enrolled (#157) → blocked until they set it up.
        request.state.mfa_setup_required = user.mfa_policy == "required" and not user.mfa_enabled
        # MFA entry-gate state (only matters if the user enabled it).
        request.state.mfa_ok = not user.mfa_enabled or mfa_service.has_valid_session(
            db, user.id, request.headers.get(auth_service.SESSION_HEADER)
        )

    # A disabled account is blocked everywhere except seeing its own status, even on
    # the otherwise gate-exempt paths (e.g. /api/security) — closes the disabled-owner
    # bypass where the retained admin role still granted access (SR-6).
    if request.state.user_status == "disabled" and not _path_in(path, _DISABLED_ALLOWED):
        return JSONResponse(
            status_code=403,
            content={"detail": "Your account has been disabled.", "account_status": "disabled"},
        )

    if _path_in(path, _GATE_EXEMPT):
        return await call_next(request)

    denied = _access_denied(request, path)
    if denied is not None:
        return denied
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
            and path.startswith(_API_PREFIX)
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


# --- Content-Security-Policy (CR-FEAT-8) ---
# Served on every response as the primary mitigation for the sessionStorage-held
# session token: a strict script-src denies an injected inline/remote script the
# chance to run and exfiltrate it. Every directive below is verified against how the
# app actually loads — Vite's same-origin JS/CSS bundles, the single inline theme
# script in frontend/index.html, and the same-origin receipt-preview iframe — so this
# can be *enforced* (not report-only) without breaking the SPA.
#
# NOTE: the script-src hash is the sha256 of the exact inline theme script in
# frontend/index.html. If that inline script is ever edited, this hash must be
# recomputed or the app will fail to set its theme on load. Two tests guard this
# so a drift can't ship silently: backend/app/tests/test_security_headers.py
# recomputes it from the source file, and e2e/tests/csp.spec.ts hashes the
# actually-served document and asserts the hash is present in the served CSP.
_CSP_DIRECTIVES = (
    # Fallback for any resource type without its own rule: same-origin only.
    "default-src 'self'",
    # JS: the Vite bundle is same-origin ('self'); the only inline script is the
    # pre-paint theme setter in index.html, allowed by its exact sha256 hash rather
    # than 'unsafe-inline' so an injected script still can't run (the token-theft
    # mitigation that is the whole point of this header).
    "script-src 'self' 'sha256-+fwDoau6WkaBQHVWdlxW4L0hEDD377jzXBuYSc7bPfw='",
    # CSS: the Vite stylesheet is same-origin; 'unsafe-inline' covers React inline
    # style props and any runtime-injected <style>. Low risk (style injection can't
    # read the token) and it keeps the SPA from breaking on styling.
    "style-src 'self' 'unsafe-inline'",
    # Images: same-origin (receipt image previews) plus data: URIs (the SVG favicon).
    "img-src 'self' data:",
    # Fonts: same-origin bundled fonts plus any data: font.
    "font-src 'self' data:",
    # XHR/fetch/WebSocket: the API and all FX/price lookups are proxied through this
    # backend, so same-origin is sufficient.
    "connect-src 'self'",
    # Frames the app embeds: the receipt PDF preview iframe loads
    # /api/receipts/{id}/file, which is same-origin.
    "frame-src 'self'",
    # Who may frame the app: 'self' still allows Home Assistant ingress (which
    # reverse-proxies the add-on onto the HA origin) while blocking external
    # clickjacking. Deliberately NOT 'none', which would break ingress.
    "frame-ancestors 'self'",
    # No plugins / <object>/<embed>.
    "object-src 'none'",
    # Lock <base href> to same-origin so an injection can't repoint relative URLs.
    "base-uri 'self'",
    # Restrict form submissions to same-origin.
    "form-action 'self'",
)
_CSP = "; ".join(_CSP_DIRECTIVES)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Attach the Content-Security-Policy to every response (CR-FEAT-8).

    Registered last so it is the OUTERMOST middleware and therefore also stamps the
    header on the 403/423 short-circuit responses returned by the guards above. See
    ``_CSP`` for the per-directive rationale. ``setdefault`` lets a route override it
    if one ever needs to (none do today)."""
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", _CSP)
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

    # index.html must NOT be cached: it's the SPA entry point that references the
    # content-hashed JS/CSS bundles. If a client (notably the Home Assistant mobile
    # webview) caches it, an add-on update keeps loading the OLD bundle until a full
    # HA restart. `no-cache` makes clients revalidate the entry point on each load,
    # so a new build's hashed assets are picked up on the next open. The hashed
    # files under /assets are safe to cache — their names change every build.
    _NO_CACHE = {"Cache-Control": "no-cache"}

    def _index_response() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html", headers=_NO_CACHE)

    @app.get("/", include_in_schema=False)
    def serve_index() -> FileResponse:
        return _index_response()

    _DIST_ROOT = FRONTEND_DIST.resolve()

    @app.get(
        "/{full_path:path}",
        include_in_schema=False,
        responses={404: {"description": "Unknown /api/* route (not a client path)"}},
    )
    def serve_spa(full_path: str) -> FileResponse:
        """SPA fallback: serve the requested file if present (and contained within
        the build directory), else index.html."""
        # An unknown /api/* path must not fall back to the SPA shell: return a real
        # 404 so API clients get JSON (not a 200 index.html), and so behaviour is
        # identical whether or not a frontend build is present.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (FRONTEND_DIST / full_path).resolve()
        # Explicit containment guard: Starlette already strips `..`, but never
        # serve a file resolved outside the build dir even if that changes
        # (CR-BUG-3).
        if full_path and candidate.is_file() and candidate.is_relative_to(_DIST_ROOT):
            return FileResponse(candidate)
        return _index_response()
else:  # pragma: no cover - dev convenience when frontend isn't built
    @app.get("/", include_in_schema=False)
    def serve_placeholder() -> dict:
        return {
            "message": f"{settings.app_name} backend is running.",
            "frontend": "not built — run `npm run build` in frontend/",
            "health": _HEALTH,
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
