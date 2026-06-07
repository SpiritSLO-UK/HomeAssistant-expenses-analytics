"""Test fixtures.

Points the app at a throwaway temp SQLite database BEFORE any app module is
imported, so the global engine/settings bind to it. Each ``client`` fixture
gets a fresh schema for isolation.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

# Must be set before importing app.config / app.db.session.
#
# We FORCE an isolated temp database and never honour an inherited
# HAFI_DATABASE_PATH — a test run must never be able to read or write a real
# database (backlog #30: "ensure tests can't access live data"). The guard
# below is belt-and-suspenders in case this file is ever edited.
_TMP = Path(tempfile.mkdtemp(prefix="hafi-test-"))
os.environ["HAFI_DATABASE_PATH"] = str(_TMP / "test.db")
os.environ.setdefault("HAFI_MQTT_ENABLED", "false")

# Hermetic AI/secret config: a developer's local backend/.env (e.g. a real AI key
# for the demo) is auto-loaded by pydantic-settings from the test CWD and would
# otherwise flip test defaults (privacy_mode, api key). Force AI off + no key so
# tests never depend on — or transmit — a local secret. (env vars beat .env.)
os.environ["HAFI_PRIVACY_MODE"] = "strict_local"
os.environ["HAFI_AI_API_KEY"] = ""

if "hafi-test-" not in os.environ["HAFI_DATABASE_PATH"]:
    raise RuntimeError(
        "Refusing to run tests against a non-temporary database: "
        f"{os.environ['HAFI_DATABASE_PATH']}"
    )

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import session as dbsession  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402

# Tests always run plaintext (encryption is verified separately). Build the
# engine up front so the fixtures have one to drop/create against.
dbsession.configure(None)


def _reset_schema() -> None:
    """Drop + recreate the whole schema for a fresh, isolated test.

    The engine runs in WAL mode with a connection pool, so doing ``drop_all``
    and ``create_all`` as two separate engine checkouts can let the second one
    reflect a stale snapshot (the table still "exists") and skip recreating it
    *and its indexes* — a race that pytest-xdist's reordering would surface as a
    flaky missing-index failure. Disposing the pool first clears any stale
    connection a prior test left behind, and running both DDL steps on a single
    connection guarantees ``create_all``'s checkfirst sees the post-drop state.
    """
    engine = dbsession.require_engine()
    engine.dispose()
    with engine.begin() as conn:
        Base.metadata.drop_all(conn)
        Base.metadata.create_all(conn)


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    _reset_schema()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db():
    _reset_schema()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def samples_dir() -> Path:
    # backend/app/tests/ -> repo root -> examples/sample-csv
    return Path(__file__).resolve().parents[3] / "examples" / "sample-csv"
