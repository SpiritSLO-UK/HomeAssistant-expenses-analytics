"""Passphrase-based encryption for backups (backlog #15).

Encrypts a backup blob with AES-256-GCM using a key derived from a user
passphrase via scrypt. Authenticated encryption means a wrong passphrase or any
tampering fails loudly (no silent garbage). Pure-Python (``cryptography``), no
native dependencies, so it runs and is tested everywhere.

This protects backups that leave the device (manual copies, future cloud
upload) — only someone with the passphrase can read them. **There is no
recovery if the passphrase is lost.**

Container format (all binary, concatenated):
    magic "HAFIENC1" (8 bytes) | salt (16) | nonce (12) | ciphertext+tag
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"HAFIENC1"
_SALT_LEN = 16
_NONCE_LEN = 12
_KEY_LEN = 32
_GCM_TAG_LEN = 16
# Shortest possible valid container: header + salt + nonce + the GCM tag (an
# empty plaintext still carries a 16-byte tag).
_MIN_LEN = len(MAGIC) + _SALT_LEN + _NONCE_LEN + _GCM_TAG_LEN
# scrypt cost parameters (interactive-strength; tune up if needed).
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1


class DecryptError(Exception):
    """Raised when decryption fails (wrong passphrase or corrupted/tampered file)."""


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt(data: bytes, passphrase: str) -> bytes:
    if not passphrase:
        raise ValueError("A passphrase is required to encrypt a backup.")
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = _derive_key(passphrase, salt)
    ciphertext = AESGCM(key).encrypt(nonce, data, MAGIC)
    return MAGIC + salt + nonce + ciphertext


def is_encrypted(blob: bytes) -> bool:
    return blob[: len(MAGIC)] == MAGIC


def decrypt(blob: bytes, passphrase: str) -> bytes:
    if not is_encrypted(blob):
        raise DecryptError("Not an encrypted HA Finance backup.")
    if len(blob) < _MIN_LEN:
        # Explicit guard so a truncated file fails clearly instead of slicing into
        # empty salt/nonce and surfacing as a misleading "wrong passphrase" (SR-E4).
        raise DecryptError("Encrypted backup is truncated or corrupted.")
    header = len(MAGIC)
    salt = blob[header : header + _SALT_LEN]
    nonce = blob[header + _SALT_LEN : header + _SALT_LEN + _NONCE_LEN]
    ciphertext = blob[header + _SALT_LEN + _NONCE_LEN :]
    key = _derive_key(passphrase, salt)
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, MAGIC)
    except InvalidTag as exc:
        raise DecryptError("Wrong passphrase or the backup file is corrupted.") from exc
