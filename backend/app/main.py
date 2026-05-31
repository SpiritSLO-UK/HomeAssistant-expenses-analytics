"""FastAPI application entry point.

Serves the API under ``/api`` and the built React frontend under ``/`` so a
single container works behind Home Assistant ingress (spec §26.3). The frontend
is built with a relative base, so it loads correctly under any ingress path.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.router import api_router
from app.config import settings
from app.db.base import Base
from app.db.session import engine
from app.logging import configure_logging, get_logger

# Import models so every table is registered on Base.metadata.
import app.models  # noqa: F401

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
    # Ensure tables exist. Alembic owns migrations in production; create_all is
    # an idempotent safety net so a fresh add-on starts even before migrations.
    Base.metadata.create_all(bind=engine)
    # Seed the default category library on first run (spec §15.4, §33).
    from app.db.session import SessionLocal
    from app.services.category_service import ensure_default_categories

    with SessionLocal() as db:
        ensure_default_categories(db)
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
