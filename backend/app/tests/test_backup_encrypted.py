"""Backup / restore on an at-rest-encrypted (SQLCipher) install (findings #2, #11).

Skips entirely where the SQLCipher driver isn't installed (e.g. Windows dev, which
has no wheel), matching ``test_atrest_encryption.py``. Runs the real encrypt +
snapshot + restore flow on Linux / the add-on / CI.

Regressions guarded here:
- #2  ``snapshot_database()`` used stdlib sqlite3 and could not read a SQLCipher
      file, breaking downloads, the encrypted-backup wrapper, and (silently) the
      retention safety backup.
- #11 ``restore_database()`` swapped in a plaintext file but left the SQLCipher
      engine + ``encryption.json`` marker in place, bricking the install.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("sqlcipher3")  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import session as dbsession  # noqa: E402
from app.services import backup_service, security_service  # noqa: E402

SQLITE_MAGIC = b"SQLite format 3"


@pytest.fixture()
def restore_plaintext():
    """Return the shared temp DB to a clean plaintext state even if a test fails
    mid-flight, so later tests don't inherit an encrypted file or stray backups."""
    yield
    security_service._delete_marker()
    security_service.clear_stored_key()
    for suffix in ("", "-wal", "-shm", ".enctmp", ".plaintmp", ".bak"):
        Path(settings.database_path + suffix).unlink(missing_ok=True)
    for leftover in backup_service.backups_dir().glob("*.db"):
        leftover.unlink(missing_ok=True)
    dbsession.configure(None)


def _header() -> bytes:
    return Path(settings.database_path).read_bytes()[: len(SQLITE_MAGIC)]


def _readable_transaction_count(db_path: Path) -> int:
    """Open ``db_path`` with *stdlib* sqlite3 (no key) and count transactions.

    Proves the snapshot is genuine plaintext: if it were still ciphertext this would
    raise ``sqlite3.DatabaseError('file is not a database')``.
    """
    con = sqlite3.connect(str(db_path))
    try:
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert backup_service._REQUIRED_TABLES <= tables, f"missing tables in snapshot: {tables}"
        return con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    finally:
        con.close()


def test_snapshot_of_encrypted_db_is_readable_plaintext(client, restore_plaintext):
    """#2: snapshot_database() on a SQLCipher install produces a readable plaintext
    backup whose contents match the live encrypted data."""
    client.post("/api/backup/demo")
    total = client.get("/api/transactions").json()["total"]
    assert total > 0

    res = client.post("/api/security/enable", json={"passphrase": "hunter2", "unlock_mode": "prompt"})
    assert res.status_code == 200, res.text
    # Live file is now ciphertext, but the app still serves the data.
    assert _header() != SQLITE_MAGIC
    assert client.get("/api/transactions").json()["total"] == total

    snap = backup_service.snapshot_database()
    try:
        assert snap.read_bytes()[: len(SQLITE_MAGIC)] == SQLITE_MAGIC  # plaintext output
        assert _readable_transaction_count(snap) == total  # exact, no float compare
    finally:
        snap.unlink(missing_ok=True)


def test_create_safety_backup_works_when_encrypted(client, restore_plaintext):
    """#2: the retention engine's safety backup (silently skipped before) succeeds on
    an encrypted install and writes a readable plaintext file."""
    client.post("/api/backup/demo")
    total = client.get("/api/transactions").json()["total"]
    assert total > 0
    assert client.post(
        "/api/security/enable", json={"passphrase": "hunter2", "unlock_mode": "prompt"}
    ).status_code == 200

    dest = backup_service.create_safety_backup("retention")
    try:
        assert dest.exists() and dest.stat().st_size > 0
        assert _readable_transaction_count(dest) == total
    finally:
        dest.unlink(missing_ok=True)


def test_restore_over_encrypted_install_reconciles_engine_and_marker(client, restore_plaintext):
    """#11: restoring a plaintext backup onto an encrypted install must reconcile the
    engine + marker to plaintext — not leave a SQLCipher engine over a plaintext file
    (500s now, self-lock on restart)."""
    client.post("/api/backup/demo")
    total = client.get("/api/transactions").json()["total"]
    assert total > 0

    # A plaintext backup captured while the DB is still plaintext.
    plaintext_snap = backup_service.snapshot_database()
    plaintext_bytes = plaintext_snap.read_bytes()
    plaintext_snap.unlink(missing_ok=True)
    assert plaintext_bytes[: len(SQLITE_MAGIC)] == SQLITE_MAGIC

    # Encrypt the live install, then restore the plaintext backup over it.
    assert client.post(
        "/api/security/enable", json={"passphrase": "hunter2", "unlock_mode": "prompt"}
    ).status_code == 200
    assert _header() != SQLITE_MAGIC
    assert security_service.read_marker() is not None

    backup_service.restore_database(plaintext_bytes)

    # Marker cleared, engine rebuilt as plaintext, file is plaintext, data served.
    assert security_service.read_marker() is None
    assert dbsession.is_locked() is False
    assert _header() == SQLITE_MAGIC
    status = client.get("/api/security/status").json()
    assert status["encryption_enabled"] is False
    assert status["locked"] is False
    assert client.get("/api/transactions").json()["total"] == total

    # And a simulated restart must NOT lock: the marker no longer claims encryption.
    dbsession.init()
    assert dbsession.is_locked() is False
    assert client.get("/api/transactions").json()["total"] == total
