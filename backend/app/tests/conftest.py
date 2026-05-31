"""Test fixtures.

Points the app at a throwaway temp SQLite database BEFORE any app module is
imported, so the global engine/settings bind to it. Each ``client`` fixture
gets a fresh schema for isolation.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Must be set before importing app.config / app.db.session.
_TMP = Path(tempfile.mkdtemp(prefix="hafi-test-"))
os.environ.setdefault("HAFI_DATABASE_PATH", str(_TMP / "test.db"))
os.environ.setdefault("HAFI_MQTT_ENABLED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def samples_dir() -> Path:
    # backend/app/tests/ -> repo root -> examples/sample-csv
    return Path(__file__).resolve().parents[3] / "examples" / "sample-csv"
