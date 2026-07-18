"""Encrypted-backup tests (backlog #15a)."""

from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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
    blob = crypto_service.encrypt(b"data", "rightpassphrase")
    with pytest.raises(DecryptError):
        crypto_service.decrypt(blob, "wrongpassphrase")


def test_tampered_blob_fails():
    blob = bytearray(crypto_service.encrypt(b"data", "passphrase"))
    blob[-1] ^= 0xFF  # flip a ciphertext bit
    with pytest.raises(DecryptError):
        crypto_service.decrypt(bytes(blob), "passphrase")


def test_non_encrypted_input_rejected():
    with pytest.raises(DecryptError):
        crypto_service.decrypt(b"SQLite format 3\x00...", "pw")


def test_empty_passphrase_rejected():
    with pytest.raises(ValueError):
        crypto_service.encrypt(b"data", "")


def test_too_short_passphrase_rejected():
    # Encrypt refuses a passphrase below the strength floor (SR-E4). One char under
    # the limit must fail; exactly at the limit is allowed (asserted separately).
    too_short = "a" * (crypto_service._MIN_PASSPHRASE_LEN - 1)
    with pytest.raises(ValueError, match="at least"):
        crypto_service.encrypt(b"data", too_short)


def test_min_length_passphrase_allowed():
    at_floor = "a" * crypto_service._MIN_PASSPHRASE_LEN
    blob = crypto_service.encrypt(b"data", at_floor)
    assert crypto_service.decrypt(blob, at_floor) == b"data"


def test_decrypt_has_no_strength_floor():
    # Back-compat: decrypt must open a backup made with a passphrase that is now
    # below the encrypt-side floor. We hand-build such a blob using the internal
    # primitives (mirroring the "HAFIENC1" format) so the encrypt() floor cannot
    # block it, proving old/weak-passphrase backups still decrypt.
    weak = "abc"  # shorter than _MIN_PASSPHRASE_LEN
    assert len(weak) < crypto_service._MIN_PASSPHRASE_LEN
    salt = os.urandom(crypto_service._SALT_LEN)
    nonce = os.urandom(crypto_service._NONCE_LEN)
    key = crypto_service._derive_key(weak, salt)
    ciphertext = AESGCM(key).encrypt(nonce, b"legacy data", crypto_service.MAGIC)
    legacy_blob = crypto_service.MAGIC + salt + nonce + ciphertext

    assert crypto_service.decrypt(legacy_blob, weak) == b"legacy data"


def test_truncated_blob_rejected():
    # A blob with the magic header but too short to hold salt/nonce/tag must fail
    # clearly, not slice into empty fields (SR-E4).
    with pytest.raises(DecryptError, match="truncated or corrupted"):
        crypto_service.decrypt(crypto_service.MAGIC + b"\x00\x01\x02", "pw")


# --- API roundtrip ---

def test_encrypted_backup_download_and_restore(client):
    client.post("/api/backup/demo")
    total = client.get("/api/transactions").json()["total"]

    res = client.post("/api/backup/database/encrypted", data={"passphrase": "s3cretpass"})
    assert res.status_code == 200
    assert res.content[:8] == b"HAFIENC1"
    assert res.content[:16] != b"SQLite format 3\x00"  # not plaintext

    restore = client.post(
        "/api/backup/restore/encrypted",
        files={"file": ("backup.db.enc", res.content, "application/octet-stream")},
        data={"passphrase": "s3cretpass"},
    )
    assert restore.status_code == 200
    assert client.get("/api/transactions").json()["total"] == total


def test_encrypted_restore_wrong_passphrase(client):
    client.post("/api/backup/demo")
    blob = client.post("/api/backup/database/encrypted", data={"passphrase": "rightpass"}).content
    res = client.post(
        "/api/backup/restore/encrypted",
        files={"file": ("backup.db.enc", blob, "application/octet-stream")},
        data={"passphrase": "wrongpass"},
    )
    assert res.status_code == 400
    assert "passphrase" in res.json()["detail"].lower()
