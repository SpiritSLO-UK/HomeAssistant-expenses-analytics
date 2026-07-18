"""Migrations against the unlocked engine on restart (encrypted-DB fix).

Regression cover for the release-blocking crash-loop: once at-rest encryption was
enabled, the pre-app ``alembic upgrade head`` in run.sh built a plain (keyless)
engine that could not open the encrypted database, so the container refused to
start. Migrations now run in-process against the ACTIVE engine
(``app.db.migrations_runner``), which inherits the unlocked SQLCipher engine.

These tests reproduce a restart in every mode:

- encrypted + env key (add-on ``HAFI_DB_KEY``)   -> auto-unlock, migrate.
- encrypted + saved ``.db_key`` file (standalone) -> auto-unlock, migrate.
- encrypted + NO key (prompt mode)                -> stays locked, no migration
  at startup; migrations run only after ``unlock()``.
- plaintext                                        -> migrate as before.

Skipped entirely where the SQLCipher driver isn't installed (Windows dev has no
wheel), matching the other at-rest tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sqlcipher3")  # noqa: E402

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import session as dbsession  # noqa: E402
from app.db.migrations_runner import run_migrations  # noqa: E402
from app.services import security_service  # noqa: E402

HEAD_REVISION = "d3e4f5a6b7c8"


@pytest.fixture()
def restore_plaintext():
    """Return the shared temp DB to a clean plaintext state after each test."""
    yield
    security_service._delete_marker()
    security_service.clear_stored_key()
    for suffix in ("", "-wal", "-shm", ".enctmp", ".plaintmp"):
        Path(settings.database_path + suffix).unlink(missing_ok=True)
    dbsession.configure(None)


def _alembic_version() -> str | None:
    """Read the stamped Alembic revision through the ACTIVE engine (plaintext or
    the unlocked SQLCipher engine). Reaching this at all proves the engine opened
    the database, which is exactly what the pre-fix keyless engine could not do."""
    with dbsession.SessionLocal() as db:
        row = db.execute(text("SELECT version_num FROM alembic_version")).fetchone()
    return row[0] if row else None


def test_plaintext_restart_runs_migrations(client, restore_plaintext):
    """Plaintext (unchanged): a restart runs migrations and the schema is at head."""
    client.post("/api/backup/demo")
    assert dbsession.is_locked() is False

    dbsession.init()  # simulated restart: no marker -> plaintext engine
    assert dbsession.is_locked() is False
    run_migrations()
    assert _alembic_version() == HEAD_REVISION


def test_encrypted_env_key_restart_auto_unlocks_and_migrates(client, restore_plaintext, monkeypatch):
    """Add-on / env-key stored mode: the env key auto-unlocks on restart and
    migrations run against the encrypted engine (no crash-loop)."""
    client.post("/api/backup/demo")
    total = client.get("/api/transactions").json()["total"]
    assert total > 0

    # Env key set -> enable(stored) uses the env key (no key FILE is written).
    monkeypatch.setattr(settings, "db_key", "env-pass")
    security_service.enable_encryption("env-pass", "stored")
    assert security_service.stored_key_source() == "env"

    # Simulated restart: drop the engine, then boot from the marker + env key.
    dbsession.lock()
    dbsession.init()
    assert dbsession.is_locked() is False  # auto-unlocked from the env key

    run_migrations()  # what the lifespan runs post-init; must open the encrypted DB
    assert _alembic_version() == HEAD_REVISION
    assert client.get("/api/transactions").json()["total"] == total


def test_encrypted_key_file_restart_auto_unlocks_and_migrates(client, restore_plaintext, monkeypatch):
    """Standalone stored mode: enabling saves the ``.db_key`` file, which
    auto-unlocks on restart; migrations then run against the encrypted engine."""
    client.post("/api/backup/demo")
    total = client.get("/api/transactions").json()["total"]
    assert total > 0

    # No env key -> enable(stored) persists the passphrase to the .db_key file.
    monkeypatch.setattr(settings, "db_key", None)
    security_service.enable_encryption("file-pass", "stored")
    assert security_service.stored_key_source() == "file"

    dbsession.lock()
    dbsession.init()
    assert dbsession.is_locked() is False  # auto-unlocked from the saved file

    run_migrations()
    assert _alembic_version() == HEAD_REVISION
    assert client.get("/api/transactions").json()["total"] == total


def test_prompt_mode_restart_stays_locked_then_migrates_on_unlock(client, restore_plaintext, monkeypatch):
    """Prompt mode (encrypted, no key): a restart must NOT crash. It boots LOCKED
    with no migration attempted; migrations run only once the user unlocks."""
    client.post("/api/backup/demo")
    total = client.get("/api/transactions").json()["total"]
    assert total > 0

    monkeypatch.setattr(settings, "db_key", None)
    security_service.enable_encryption("prompt-pass", "prompt")
    assert security_service.resolve_stored_key() is None  # nothing stored

    # Simulated restart with no key available: stays locked, no engine to migrate.
    dbsession.lock()
    dbsession.init()
    assert dbsession.is_locked() is True
    with pytest.raises(dbsession.DatabaseLocked):
        dbsession.require_engine()  # the lifespan skips migrations while locked

    # The user unlocks via the UI -> unlock() configures the engine AND migrates.
    assert security_service.unlock("prompt-pass") is True
    assert dbsession.is_locked() is False
    assert _alembic_version() == HEAD_REVISION
    assert client.get("/api/transactions").json()["total"] == total
