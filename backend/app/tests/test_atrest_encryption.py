"""At-rest SQLCipher encryption tests (backlog #15b).

Skips entirely where the SQLCipher driver isn't installed (e.g. Windows dev,
which has no wheel). Runs the real encrypt/lock/unlock/decrypt flow on Linux /
the add-on / CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sqlcipher3")  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import session as dbsession  # noqa: E402
from app.services import security_service  # noqa: E402

SQLITE_MAGIC = b"SQLite format 3"


@pytest.fixture()
def restore_plaintext():
    """Always return the shared temp DB to a clean plaintext state, even if a
    test fails mid-flight (so later tests aren't left with an encrypted file)."""
    yield
    security_service._delete_marker()
    for suffix in ("", "-wal", "-shm", ".enctmp", ".plaintmp"):
        Path(settings.database_path + suffix).unlink(missing_ok=True)
    dbsession.configure(None)


def _header() -> bytes:
    return Path(settings.database_path).read_bytes()[:16]


def test_enable_lock_unlock_disable(client, restore_plaintext):
    client.post("/api/backup/demo")
    total = client.get("/api/transactions").json()["total"]
    assert total > 0

    status = client.get("/api/security/status").json()
    assert status["encryption_available"] is True
    assert status["encryption_enabled"] is False
    assert status["locked"] is False

    # Enable encryption -> file becomes ciphertext, data still served.
    res = client.post("/api/security/enable", json={"passphrase": "hunter2", "unlock_mode": "prompt"})
    assert res.status_code == 200, res.text
    assert _header()[: len(SQLITE_MAGIC)] != SQLITE_MAGIC
    assert client.get("/api/transactions").json()["total"] == total
    assert client.get("/api/security/status").json()["encryption_enabled"] is True

    # Simulate a restart: locked until unlocked.
    dbsession.lock()
    assert client.get("/api/security/status").json()["locked"] is True
    assert client.get("/api/health").json()["status"] == "locked"
    assert client.get("/api/transactions").status_code == 423  # data blocked
    assert client.post("/api/security/unlock", json={"passphrase": "nope"}).status_code == 400
    assert client.post("/api/security/unlock", json={"passphrase": "hunter2"}).json()["status"] == "unlocked"
    assert client.get("/api/transactions").json()["total"] == total

    # Disable -> back to plaintext, data intact.
    assert client.post("/api/security/disable", json={"passphrase": "hunter2"}).status_code == 200
    assert _header()[: len(SQLITE_MAGIC)] == SQLITE_MAGIC
    assert client.get("/api/transactions").json()["total"] == total
    assert client.get("/api/security/status").json()["encryption_enabled"] is False


def test_enable_requires_passphrase(client, restore_plaintext):
    assert client.post("/api/security/enable", json={"passphrase": "", "unlock_mode": "prompt"}).status_code == 400


def test_disable_wrong_passphrase(client, restore_plaintext):
    client.post("/api/security/enable", json={"passphrase": "right", "unlock_mode": "prompt"})
    res = client.post("/api/security/disable", json={"passphrase": "wrong"})
    assert res.status_code == 400


def test_stored_key_unlocks_on_restart(client, restore_plaintext, monkeypatch):
    """Stored unlock mode: a matching HAFI_DB_KEY auto-unlocks on restart, a wrong
    one locks (rather than building a broken engine), and status reflects whether
    the key is wired (drives the Settings warning)."""
    client.post("/api/backup/demo")
    total = client.get("/api/transactions").json()["total"]
    assert total > 0

    res = client.post("/api/security/enable", json={"passphrase": "hunter2", "unlock_mode": "stored"})
    assert res.status_code == 200, res.text

    # Stored mode selected but no key configured → flagged so the UI can warn.
    monkeypatch.setattr(settings, "db_key", None)
    s = client.get("/api/security/status").json()
    assert s["unlock_mode"] == "stored"
    assert s["stored_key_present"] is False

    # Restart with the WRONG stored key → stays locked, data blocked.
    monkeypatch.setattr(settings, "db_key", "wrong-key")
    dbsession.init()
    assert dbsession.is_locked() is True
    assert client.get("/api/security/status").json()["stored_key_present"] is True
    assert client.get("/api/transactions").status_code == 423

    # Restart with the CORRECT stored key → unattended unlock, data served.
    monkeypatch.setattr(settings, "db_key", "hunter2")
    dbsession.init()
    assert dbsession.is_locked() is False
    assert client.get("/api/transactions").json()["total"] == total
