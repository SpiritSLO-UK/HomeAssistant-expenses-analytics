"""Run Alembic migrations in-process against the active engine (backlog fix).

Historically ``addon/run.sh`` ran ``alembic upgrade head`` as a separate process
BEFORE the app started. That process built a plain (non-SQLCipher) engine with no
key, so once at-rest encryption was enabled it could not open the encrypted
database and the container crash-looped on the next restart
(``file is not a database``).

Migrations now run here instead, inside the app, against
``app.db.session``'s *active* engine. That engine is whatever the encryption
context produced (the plaintext engine, or the unlocked SQLCipher engine), so
migrations always run on the UNLOCKED database in every mode.

Callers:

- ``app.main`` lifespan, right after ``dbsession.init()`` when the DB is not
  locked (plaintext, or auto-unlocked from a stored/env/file key).
- ``security_service.unlock()``, right after a prompt-mode unlock succeeds.

The standalone ``alembic`` CLI (local dev on a plaintext DB) keeps working: this
module injects a live connection via ``config.attributes["connection"]`` and
``env.py`` prefers it, falling back to its own ``engine_from_config`` when run
from the CLI.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.config import settings
from app.db import session as dbsession
from app.logging import get_logger

logger = get_logger(__name__)

# backend/app/db/migrations_runner.py -> backend/app/db/migrations
_SCRIPT_LOCATION = Path(__file__).resolve().parent / "migrations"


def _alembic_config() -> Config:
    """A minimal Alembic config pointing at our migration scripts.

    Deliberately built WITHOUT the alembic.ini path so ``env.py`` skips
    ``fileConfig`` (running that in-process would reconfigure, and by default
    disable, the app's own loggers). Everything env.py needs it derives from
    ``app.config``; the injected connection carries the engine."""
    config = Config()
    config.set_main_option("script_location", str(_SCRIPT_LOCATION))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


def run_migrations() -> None:
    """Bring the active engine's schema to ``head``.

    Three cases, so a DB whose schema was created out-of-band (the ``create_all``
    safety net, or the test harness) is not mistaken for a pre-Alembic base and
    told to re-create tables that already exist:

    - schema present AND already under Alembic control -> ``upgrade head``
      (applies any pending migrations; no-op when current).
    - schema present but NOT stamped (built by ``create_all``) -> ``stamp head``
      (record the version without re-running DDL; create_all already built the
      head schema from the models).
    - empty database -> ``upgrade head`` (build everything from base).

    Raises on a genuine migration failure so callers can refuse to serve
    inconsistent data.
    """
    engine = dbsession.require_engine()
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    version_present = "alembic_version" in tables
    schema_present = bool(tables - {"alembic_version"})

    config = _alembic_config()
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        if schema_present and not version_present:
            logger.info("Existing un-stamped schema found; stamping Alembic head.")
            command.stamp(config, "head")
        else:
            command.upgrade(config, "head")
    logger.info("Database migrations are at head.")
