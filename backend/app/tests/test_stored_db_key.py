"""Stored auto-unlock key file (UI-driven "stored" mode on standalone).

Two layers:

- Pure-logic tests (save / read / clear / env-precedence) run everywhere, with
  no SQLCipher driver, because they only touch the key file and settings.
- Full encrypt/unlock flow tests are guarded with ``skipif`` so they run on
  Linux / CI (where the SQLCipher wheel exists) and skip on Windows dev, the
  same way ``test_atrest_encryption.py`` does.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.config import settings
from app.db import session as dbsession
from app.services import security_service

_HAS_SQLCIPHER = security_service.sqlcipher_available()
requires_sqlcipher = pytest.mark.skipif(not _HAS_SQLCIPHER, reason="SQLCipher driver not available")


@pytest.fixture()
def clean_key_file():
    """Ensure no stored key file leaks into or out of a test."""
    security_service.clear_stored_key()
    yield
    security_service.clear_stored_key()


@pytest.fixture()
def restore_plaintext():
    """Return the shared temp DB to a clean plaintext state after a flow test,
    even on failure, and drop any stored key file it wrote."""
    yield
    security_service._delete_marker()
    security_service.clear_stored_key()
    for suffix in ("", "-wal", "-shm", ".enctmp", ".plaintmp"):
        Path(settings.database_path + suffix).unlink(missing_ok=True)
    dbsession.configure(None)


# --- Pure logic (no SQLCipher) ----------------------------------------------


def test_save_read_clear_key_file(clean_key_file):
    assert security_service.read_stored_key_file() is None

    security_service.save_stored_key("hunter2")
    assert security_service.read_stored_key_file() == "hunter2"

    # 0600: owner read/write only. os.chmod is only meaningful on POSIX; on
    # Windows it can't drop group/other bits, so assert the mode there only.
    if os.name == "posix":
        mode = stat.S_IMODE(os.stat(security_service._key_file_path()).st_mode)
        assert mode == 0o600

    security_service.clear_stored_key()
    assert security_service.read_stored_key_file() is None
    # Clearing an absent file is a no-op, not an error.
    security_service.clear_stored_key()


def test_empty_key_file_reads_as_none(clean_key_file):
    security_service._key_file_path().write_text("   \n", encoding="utf-8")
    assert security_service.read_stored_key_file() is None


def test_read_strips_whitespace(clean_key_file):
    security_service._key_file_path().write_text("  secret\n", encoding="utf-8")
    assert security_service.read_stored_key_file() == "secret"


def test_resolve_prefers_env_over_file(clean_key_file, monkeypatch):
    security_service.save_stored_key("file-key")

    monkeypatch.setattr(settings, "db_key", "env-key")
    assert security_service.resolve_stored_key() == "env-key"
    assert security_service.stored_key_source() == "env"

    monkeypatch.setattr(settings, "db_key", None)
    assert security_service.resolve_stored_key() == "file-key"
    assert security_service.stored_key_source() == "file"

    security_service.clear_stored_key()
    assert security_service.resolve_stored_key() is None
    assert security_service.stored_key_source() is None


def test_status_reflects_saved_key(client, clean_key_file, monkeypatch):
    """The Settings "stored but no key" warning keys off stored_key_present, which
    must flip to True once a key file is saved (no env key needed)."""
    monkeypatch.setattr(settings, "db_key", None)

    s = client.get("/api/security/status").json()
    assert s["stored_key_present"] is False
    assert s["stored_key_source"] is None

    security_service.save_stored_key("hunter2")
    s = client.get("/api/security/status").json()
    assert s["stored_key_present"] is True
    assert s["stored_key_source"] == "file"


# --- Full flow (needs SQLCipher) --------------------------------------------


@requires_sqlcipher
def test_enable_stored_saves_key_and_unlocks_from_file(client, restore_plaintext, monkeypatch):
    """Standalone stored mode: no HAFI_DB_KEY env, so enable saves a 0600 key file
    and a fresh startup auto-unlocks from that file."""
    monkeypatch.setattr(settings, "db_key", None)
    client.post("/api/backup/demo")
    total = client.get("/api/transactions").json()["total"]
    assert total > 0

    res = client.post("/api/security/enable", json={"passphrase": "hunter2", "unlock_mode": "stored"})
    assert res.status_code == 200, res.text

    assert security_service.read_stored_key_file() == "hunter2"
    if os.name == "posix":
        mode = stat.S_IMODE(os.stat(security_service._key_file_path()).st_mode)
        assert mode == 0o600

    # Status shows the key is wired via the file, not the env.
    s = client.get("/api/security/status").json()
    assert s["stored_key_present"] is True
    assert s["stored_key_source"] == "file"

    # Simulate a restart with NO env key: init() must unlock from the saved file.
    dbsession.lock()
    assert dbsession.is_locked() is True
    dbsession.init()
    assert dbsession.is_locked() is False
    assert client.get("/api/transactions").json()["total"] == total


@requires_sqlcipher
def test_env_key_takes_precedence_no_file_written(client, restore_plaintext, monkeypatch):
    """Add-on path: HAFI_DB_KEY env is set, so enable must NOT write a key file
    (the env value is authoritative)."""
    monkeypatch.setattr(settings, "db_key", "hunter2")
    client.post("/api/backup/demo")

    res = client.post("/api/security/enable", json={"passphrase": "hunter2", "unlock_mode": "stored"})
    assert res.status_code == 200, res.text

    assert security_service.read_stored_key_file() is None  # env wins, no file
    s = client.get("/api/security/status").json()
    assert s["stored_key_present"] is True
    assert s["stored_key_source"] == "env"


@requires_sqlcipher
def test_prompt_mode_leaves_no_key_file(client, restore_plaintext, monkeypatch):
    """Prompt mode stores nothing, and clears any stale key file."""
    monkeypatch.setattr(settings, "db_key", None)
    security_service.save_stored_key("stale-key")  # pretend a prior stored setup
    client.post("/api/backup/demo")

    res = client.post("/api/security/enable", json={"passphrase": "hunter2", "unlock_mode": "prompt"})
    assert res.status_code == 200, res.text

    assert security_service.read_stored_key_file() is None  # cleared


@requires_sqlcipher
def test_disable_removes_key_file(client, restore_plaintext, monkeypatch):
    monkeypatch.setattr(settings, "db_key", None)
    client.post("/api/backup/demo")
    client.post("/api/security/enable", json={"passphrase": "hunter2", "unlock_mode": "stored"})
    assert security_service.read_stored_key_file() == "hunter2"

    res = client.post("/api/security/disable", json={"passphrase": "hunter2"})
    assert res.status_code == 200, res.text
    assert security_service.read_stored_key_file() is None


@requires_sqlcipher
def test_wrong_saved_key_stays_locked(client, restore_plaintext, monkeypatch):
    """A corrupted/incorrect saved key must keep the DB locked, never serve data."""
    monkeypatch.setattr(settings, "db_key", None)
    client.post("/api/backup/demo")
    client.post("/api/security/enable", json={"passphrase": "hunter2", "unlock_mode": "stored"})

    # Overwrite the saved key with the wrong value, then restart.
    security_service.save_stored_key("wrong-key")
    dbsession.lock()
    dbsession.init()
    assert dbsession.is_locked() is True
    assert client.get("/api/transactions").status_code == 423
