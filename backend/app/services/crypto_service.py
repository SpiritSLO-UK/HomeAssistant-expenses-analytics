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
# Minimum passphrase length enforced on the ENCRYPT side only (SR-E4). There is
# no recovery if the passphrase is lost and these backups leave the device, so a
# trivially short passphrase must be refused before it protects anything. Kept as
# a plain length floor (no composition rules) to stay usable; decrypt imposes NO
# floor so backups made with any passphrase — including older, shorter ones —
# still open.
_MIN_PASSPHRASE_LEN = 8

# scrypt cost parameters.
#
# WARNING — these values are FIXED for the "HAFIENC1" container and are NOT
# recorded in the on-disk blob (the header is magic|salt|nonce|ciphertext only).
# Every backup is therefore implicitly bound to exactly these numbers: changing
# any of them here would make _every existing backup_ underivable and impossible
# to decrypt. Do not raise N/r/p in place.
#
# Safe upgrade path (deliberately NOT done in this change, to avoid format risk):
#   1. Introduce a new magic/version, e.g. b"HAFIENC2", whose header stores the
#      params (n, r, p) alongside salt/nonce.
#   2. encrypt() writes the new magic + params; decrypt() dispatches on the magic
#      bytes — reading params from the header for "HAFIENC2" and falling back to
#      these fixed constants for legacy "HAFIENC1" blobs.
#   3. Keep the "HAFIENC1" read path forever (back-compat), with a round-trip test
#      proving old-format blobs still decrypt.
# Until that versioned header lands, treat these three constants as immutable.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1


class DecryptError(Exception):
    """Raised when decryption fails (wrong passphrase or corrupted/tampered file)."""


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_internal(data: bytes, passphrase: str) -> bytes:
    """Encrypt with the ``HAFIENC1`` container but WITHOUT the ``_MIN_PASSPHRASE_LEN``
    floor. For INTERNALLY-derived keys only — the app secret ``HAFI_DB_KEY`` that
    field-encrypts the stored AI API key and the MFA TOTP seed. That key is not a
    user-chosen backup passphrase (SQLCipher imposes no length floor on it either),
    so it must not be rejected here for being short.

    The container is byte-for-byte identical to :func:`encrypt`'s output — only the
    length check differs — so values written through either path decrypt with the
    same :func:`decrypt`/:func:`decrypt_internal` and already-stored secrets keep
    opening. A passphrase is still required (empty is refused)."""
    if not passphrase:
        raise ValueError("A passphrase is required to encrypt.")
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = _derive_key(passphrase, salt)
    ciphertext = AESGCM(key).encrypt(nonce, data, MAGIC)
    return MAGIC + salt + nonce + ciphertext


def encrypt(data: bytes, passphrase: str) -> bytes:
    """Encrypt a backup with a USER-CHOSEN passphrase, enforcing the length floor.

    Use this for anything protected by a passphrase the user typed. For internal
    app keys (``HAFI_DB_KEY``) use :func:`encrypt_internal`, which skips the floor.
    """
    if not passphrase:
        raise ValueError("A passphrase is required to encrypt a backup.")
    if len(passphrase) < _MIN_PASSPHRASE_LEN:
        # Enforced on encrypt only: a too-short passphrase gives weak protection to
        # an unrecoverable, off-device backup. Decrypt keeps no floor so existing
        # backups made with shorter passphrases still open (SR-E4).
        raise ValueError(
            f"Passphrase must be at least {_MIN_PASSPHRASE_LEN} characters."
        )
    return encrypt_internal(data, passphrase)


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


def decrypt_internal(blob: bytes, passphrase: str) -> bytes:
    """Decrypt a container produced by :func:`encrypt_internal` (or :func:`encrypt`).

    The two encrypt paths share one format, so this is the counterpart of
    :func:`decrypt`; it exists only so the internal (no-floor) callers read through
    a symmetric name. Decrypt never imposes a passphrase-length floor, so a value
    written with a short internal key still opens."""
    return decrypt(blob, passphrase)
