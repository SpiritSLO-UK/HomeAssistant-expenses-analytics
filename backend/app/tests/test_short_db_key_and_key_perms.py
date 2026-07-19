"""Secret handling with a short ``HAFI_DB_KEY``, and key-file permissions.

Covers two fixes:

- #22: the app secret ``HAFI_DB_KEY`` is an INTERNAL key, not a user-chosen backup
  passphrase, so field-encryption of the stored AI API key and the MFA TOTP seed
  must NOT enforce the 8-char backup floor. A short db_key must still round-trip
  (no ValueError / 500). The user-facing backup ``encrypt`` keeps its floor.
- #28: ``save_stored_key`` must never leave the plaintext key on disk with perms
  wider than 0600 — the file is created restrictively from the outset.
"""

from __future__ import annotations

import os
import stat

import pytest

from app.config import settings as env_settings
from app.db.session import SessionLocal
from app.services import crypto_service, mfa_service, settings_service, totp

SHORT_KEY = "1234"  # 4 chars — below the 8-char backup floor


# --- #22: internal field-crypto ignores the backup passphrase floor ----------


def test_internal_encrypt_roundtrips_with_short_key():
    blob = crypto_service.encrypt_internal(b"payload", SHORT_KEY)
    assert crypto_service.decrypt_internal(blob, SHORT_KEY) == b"payload"


def test_internal_encrypt_still_requires_a_passphrase():
    with pytest.raises(ValueError):
        crypto_service.encrypt_internal(b"payload", "")


def test_backup_encrypt_still_rejects_short_passphrase():
    """The user-facing backup path is unchanged — a <8-char passphrase is refused."""
    with pytest.raises(ValueError):
        crypto_service.encrypt(b"payload", SHORT_KEY)
    # And a long one still works + round-trips.
    blob = crypto_service.encrypt(b"payload", "long-enough-passphrase")
    assert crypto_service.decrypt(blob, "long-enough-passphrase") == b"payload"


def test_internal_container_matches_backup_format():
    """encrypt_internal emits the same HAFIENC1 container as encrypt, so already
    stored secrets stay decryptable across the two paths."""
    long_key = "long-enough-passphrase"
    internal = crypto_service.encrypt_internal(b"same", long_key)
    assert crypto_service.is_encrypted(internal)
    # A value written by the floored path decrypts through the internal path too.
    floored = crypto_service.encrypt(b"same", long_key)
    assert crypto_service.decrypt_internal(floored, long_key) == b"same"
    assert crypto_service.decrypt(internal, long_key) == b"same"


def test_ai_key_roundtrips_with_short_db_key(client, monkeypatch):
    """Saving + reading back an AI key with a short HAFI_DB_KEY must not 500."""
    monkeypatch.setattr(env_settings, "ai_api_key", None)
    monkeypatch.setattr(env_settings, "db_key", SHORT_KEY)

    r = client.put("/api/settings", json={"ai_api_key": "sk-short-key-ok"})
    assert r.status_code == 200, r.text

    st = client.get("/api/ai/status").json()
    assert st["has_api_key"] is True
    assert st["key_source"] == "stored"

    with SessionLocal() as db:
        assert settings_service.get_ai_api_key(db) == "sk-short-key-ok"


def test_ai_key_encrypt_helper_roundtrips_with_short_db_key(monkeypatch):
    monkeypatch.setattr(env_settings, "db_key", SHORT_KEY)
    stored = settings_service._encrypt_ai_key("sk-abc")
    assert stored.startswith(settings_service._ENC_PREFIX)
    assert "sk-abc" not in stored
    assert settings_service._decrypt_ai_key(stored) == "sk-abc"


def test_mfa_secret_roundtrips_with_short_db_key(monkeypatch):
    """MFA enrolment encrypts the TOTP seed with HAFI_DB_KEY; a short key must
    round-trip rather than raise."""
    monkeypatch.setattr(env_settings, "db_key", SHORT_KEY)
    secret = totp.generate_secret()

    stored = mfa_service.encrypt_secret(secret)
    assert stored.startswith(mfa_service._ENC_PREFIX)
    assert secret not in stored
    assert mfa_service.decrypt_secret(stored) == secret


# --- #28: stored key file is never world-readable ----------------------------


@pytest.fixture()
def clean_key_file():
    from app.services import security_service

    security_service.clear_stored_key()
    yield
    security_service.clear_stored_key()


def test_save_stored_key_is_0600_at_all_times(clean_key_file):
    from app.services import security_service

    security_service.save_stored_key("s3cr3t-passphrase")
    assert security_service.read_stored_key_file() == "s3cr3t-passphrase"

    # POSIX is where perms matter: the file must be owner-only, never group/other.
    if os.name == "posix":
        mode = stat.S_IMODE(os.stat(security_service._key_file_path()).st_mode)
        assert mode == 0o600


def test_save_stored_key_tightens_a_preexisting_wide_file(clean_key_file):
    """Even if a wider-perm file already existed, the saved key ends up 0600 (the
    old plaintext is replaced, not appended to)."""
    from app.services import security_service

    path = security_service._key_file_path()
    path.write_text("old-wide-perms", encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o644)

    security_service.save_stored_key("new-secret")
    assert security_service.read_stored_key_file() == "new-secret"
    if os.name == "posix":
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600
