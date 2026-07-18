"""AI provider API key stored encrypted-at-rest (backlog #9).

The key may be set from the UI on a standalone instance. It is persisted in the
``ai_api_key`` settings row, encrypted with the same field-crypto primitive as the
MFA TOTP secret when an app key (``HAFI_DB_KEY``) is configured, plaintext-fallback
otherwise. The env var ``HAFI_AI_API_KEY`` always wins, and the raw value is never
returned by any GET or exported.
"""

from __future__ import annotations

from sqlalchemy import select

from app.config import settings as env_settings
from app.db.session import SessionLocal
from app.models import Setting
from app.services import ai_service, settings_service


def _status(client) -> dict:
    return client.get("/api/ai/status").json()


def _raw_row():
    with SessionLocal() as db:
        return db.scalars(select(Setting).where(Setting.key == settings_service.AI_API_KEY)).one_or_none()


def test_save_sets_has_api_key_and_source_stored(client, monkeypatch):
    monkeypatch.setattr(env_settings, "ai_api_key", None)  # no env override

    before = _status(client)
    assert before["has_api_key"] is False
    assert before["key_source"] == "none"

    r = client.put("/api/settings", json={"ai_api_key": "sk-secret-123"})
    assert r.status_code == 200

    st = _status(client)
    assert st["has_api_key"] is True
    assert st["key_source"] == "stored"

    with SessionLocal() as db:
        assert settings_service.get_ai_api_key(db) == "sk-secret-123"  # decrypts back


def test_env_key_wins_over_stored(client, monkeypatch):
    monkeypatch.setattr(env_settings, "ai_api_key", None)
    client.put("/api/settings", json={"ai_api_key": "sk-stored"})
    assert _status(client)["key_source"] == "stored"

    monkeypatch.setattr(env_settings, "ai_api_key", "sk-env")
    st = _status(client)
    assert st["has_api_key"] is True
    assert st["key_source"] == "env"

    with SessionLocal() as db:
        assert ai_service._resolve_api_key(db) == "sk-env"  # env wins even with a stored key


def test_clear_removes_stored_key(client, monkeypatch):
    monkeypatch.setattr(env_settings, "ai_api_key", None)
    client.put("/api/settings", json={"ai_api_key": "sk-secret"})
    assert _status(client)["key_source"] == "stored"

    client.put("/api/settings", json={"ai_api_key": ""})  # explicit clear
    st = _status(client)
    assert st["has_api_key"] is False
    assert st["key_source"] == "none"

    assert _raw_row() is None  # the row is removed, not merely blanked
    with SessionLocal() as db:
        assert settings_service.get_ai_api_key(db) is None


def test_key_never_appears_in_get_settings_or_logs(client, monkeypatch, caplog):
    monkeypatch.setattr(env_settings, "ai_api_key", None)
    secret = "sk-super-secret-should-not-leak-42"

    with caplog.at_level("DEBUG"):
        put = client.put("/api/settings", json={"ai_api_key": secret})
        assert put.status_code == 200
        get = client.get("/api/settings")
        status = client.get("/api/ai/status")
        export = client.get("/api/backup/config")

    # PUT echoes the settings map, GET returns it, status/export must all omit it.
    assert secret not in put.text
    assert secret not in get.text
    assert "ai_api_key" not in get.json()
    assert secret not in status.text
    assert secret not in export.text
    assert secret not in caplog.text  # never logged


def test_encrypted_at_rest_when_app_key_set(client, monkeypatch):
    monkeypatch.setattr(env_settings, "ai_api_key", None)
    monkeypatch.setattr(env_settings, "db_key", "app-secret-passphrase")
    secret = "sk-plaintext-value"

    client.put("/api/settings", json={"ai_api_key": secret})

    row = _raw_row()
    assert row is not None
    assert row.value.startswith("aienc1:")  # marked ciphertext, not the raw key
    assert secret not in row.value
    with SessionLocal() as db:
        assert settings_service.get_ai_api_key(db) == secret  # round-trips


def test_plaintext_fallback_without_app_key(client, monkeypatch):
    monkeypatch.setattr(env_settings, "ai_api_key", None)
    monkeypatch.setattr(env_settings, "db_key", None)  # no app key → no field crypto

    client.put("/api/settings", json={"ai_api_key": "sk-nokey"})

    row = _raw_row()
    assert row is not None
    assert not row.value.startswith("aienc1:")  # stored as-is, same posture as TOTP secret
    with SessionLocal() as db:
        assert settings_service.get_ai_api_key(db) == "sk-nokey"


def test_stored_key_unrecoverable_after_app_key_change_reports_none(client, monkeypatch):
    """A rotated/absent app key makes a ciphertext value unrecoverable — AI must
    report "no key" (fail closed) rather than crash."""
    monkeypatch.setattr(env_settings, "ai_api_key", None)
    monkeypatch.setattr(env_settings, "db_key", "original-key")
    client.put("/api/settings", json={"ai_api_key": "sk-rotate"})
    assert _status(client)["key_source"] == "stored"

    monkeypatch.setattr(env_settings, "db_key", "different-key")  # rotated
    st = _status(client)
    assert st["has_api_key"] is False
    assert st["key_source"] == "none"
    with SessionLocal() as db:
        assert settings_service.get_ai_api_key(db) is None
