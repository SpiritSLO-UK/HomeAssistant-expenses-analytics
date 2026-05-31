"""Encrypted-backup tests (backlog #15a)."""

from __future__ import annotations

import pytest

from app.services import crypto_service
from app.services.crypto_service import DecryptError


# --- crypto unit ---

def test_encrypt_decrypt_roundtrip():
    blob = crypto_service.encrypt(b"top secret data", "correct horse")
    assert crypto_service.is_encrypted(blob)
    assert blob[:8] == b"HAFIENC1"
    assert b"top secret data" not in blob  # actually encrypted
    assert crypto_service.decrypt(blob, "correct horse") == b"top secret data"


def test_wrong_passphrase_fails():
    blob = crypto_service.encrypt(b"data", "right")
    with pytest.raises(DecryptError):
        crypto_service.decrypt(blob, "wrong")


def test_tampered_blob_fails():
    blob = bytearray(crypto_service.encrypt(b"data", "pw"))
    blob[-1] ^= 0xFF  # flip a ciphertext bit
    with pytest.raises(DecryptError):
        crypto_service.decrypt(bytes(blob), "pw")


def test_non_encrypted_input_rejected():
    with pytest.raises(DecryptError):
        crypto_service.decrypt(b"SQLite format 3\x00...", "pw")


def test_empty_passphrase_rejected():
    with pytest.raises(ValueError):
        crypto_service.encrypt(b"data", "")


# --- API roundtrip ---

def test_encrypted_backup_download_and_restore(client):
    client.post("/api/backup/demo")
    total = client.get("/api/transactions").json()["total"]

    res = client.post("/api/backup/database/encrypted", data={"passphrase": "s3cret"})
    assert res.status_code == 200
    assert res.content[:8] == b"HAFIENC1"
    assert res.content[:16] != b"SQLite format 3\x00"  # not plaintext

    restore = client.post(
        "/api/backup/restore/encrypted",
        files={"file": ("backup.db.enc", res.content, "application/octet-stream")},
        data={"passphrase": "s3cret"},
    )
    assert restore.status_code == 200
    assert client.get("/api/transactions").json()["total"] == total


def test_encrypted_restore_wrong_passphrase(client):
    client.post("/api/backup/demo")
    blob = client.post("/api/backup/database/encrypted", data={"passphrase": "right"}).content
    res = client.post(
        "/api/backup/restore/encrypted",
        files={"file": ("backup.db.enc", blob, "application/octet-stream")},
        data={"passphrase": "wrong"},
    )
    assert res.status_code == 400
    assert "passphrase" in res.json()["detail"].lower()
