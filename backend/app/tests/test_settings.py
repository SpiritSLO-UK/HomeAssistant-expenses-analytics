"""Settings endpoint — runtime log-level control (admin log level)."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


def test_set_log_level_applies_and_round_trips(client):
    r = client.put("/api/settings", json={"log_level": "DEBUG"})
    assert r.status_code == 200
    assert r.json()["log_level"] == "DEBUG"
    # Takes effect immediately on the root logger…
    assert logging.getLogger().level == logging.DEBUG
    # …and round-trips on GET.
    assert client.get("/api/settings").json()["log_level"] == "DEBUG"
    client.put("/api/settings", json={"log_level": "INFO"})  # restore default
    assert logging.getLogger().level == logging.INFO


def test_invalid_log_level_rejected(client):
    r = client.put("/api/settings", json={"log_level": "LOUD"})
    assert r.status_code == 400


def test_supported_currencies_listed(client):
    """The curated top-10 base-currency choices power the Settings dropdown."""
    rows = client.get("/api/settings/currencies").json()
    codes = [c["code"] for c in rows]
    assert len(rows) >= 10
    assert {"GBP", "USD", "EUR", "JPY", "SGD"} <= set(codes)
    gbp = next(c for c in rows if c["code"] == "GBP")
    assert gbp["symbol"] == "£" and gbp["name"]


def test_supported_countries_listed(client):
    """The ISO country list powers the vendor / trip country pickers — sorted by
    name, with common countries present and the 'EU' pseudo-code omitted."""
    rows = client.get("/api/settings/countries").json()
    codes = {c["code"] for c in rows}
    assert len(rows) >= 200  # ~all of ISO-3166-1
    assert {"GB", "US", "JP", "ES", "BR"} <= codes
    assert "EU" not in codes  # the EUR-fallback pseudo-code isn't a country
    names = [c["name"] for c in rows]
    assert names == sorted(names)  # sorted by display name
    assert {"code": "GB", "name": "United Kingdom"} in rows


def test_default_vendor_country_set_validate_clear(client):
    """The default vendor country accepts a valid ISO-2 (case-insensitive), rejects
    non-countries (incl. the 'EU' pseudo-code), and clears on ""."""
    ok = client.put("/api/settings", json={"default_vendor_country": "gb"})
    assert ok.status_code == 200
    assert ok.json()["default_vendor_country"] == "GB"  # normalised to upper-case
    assert client.put("/api/settings", json={"default_vendor_country": "ZZ"}).status_code == 400
    assert client.put("/api/settings", json={"default_vendor_country": "EU"}).status_code == 400
    cleared = client.put("/api/settings", json={"default_vendor_country": ""})
    assert cleared.status_code == 200
    assert cleared.json()["default_vendor_country"] == ""


def test_setting_key_is_unique(client):
    """SR-2: ``settings.key`` is the row identity, so a value can never be silently
    shadowed by a duplicate row. ``set_value`` upserts in place, and a raw duplicate
    insert is rejected by the unique index."""
    from app.db.session import SessionLocal
    from app.models import Setting
    from app.services import settings_service

    with SessionLocal() as db:
        settings_service.set_value(db, "sr2_probe", "one")
        settings_service.set_value(db, "sr2_probe", "two")  # upsert — not a 2nd row
        assert settings_service.get(db, "sr2_probe") == "two"
        rows = db.scalars(select(Setting).where(Setting.key == "sr2_probe")).all()
        assert len(rows) == 1

        db.add(Setting(key="sr2_probe", value="dup"))  # raw duplicate → rejected
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_set_many_commits_all_in_one_transaction(client):
    """``set_many`` upserts a whole batch and commits once — every key persists."""
    from app.db.session import SessionLocal
    from app.services import settings_service

    with SessionLocal() as db:
        settings_service.set_many(db, {"sm_a": "1", "sm_b": "2", "sm_c": "3"})
    with SessionLocal() as db:  # fresh session → proves it was committed
        assert settings_service.get(db, "sm_a") == "1"
        assert settings_service.get(db, "sm_b") == "2"
        assert settings_service.get(db, "sm_c") == "3"


def test_set_many_is_atomic_bad_value_rolls_back_whole_batch(client):
    """A mid-batch invalid value aborts the ENTIRE save with no partial write:
    neither the earlier valid key nor the bad one is persisted."""
    from app.db.session import SessionLocal
    from app.models import Setting
    from app.services import settings_service

    with SessionLocal() as db:
        settings_service.set_value(db, "atomic_probe", "before")

    with SessionLocal() as db:
        with pytest.raises(ValueError):
            # First entry is a valid change to the existing key; the second entry has
            # a non-string value → the whole batch must be rejected untouched.
            settings_service.set_many(db, {"atomic_probe": "after", "atomic_bad": 123})

    with SessionLocal() as db:  # fresh session → sees only what was committed
        assert settings_service.get(db, "atomic_probe") == "before"  # unchanged
        rows = db.scalars(select(Setting).where(Setting.key == "atomic_bad")).all()
        assert rows == []  # the bad key was never written


def test_set_value_delegates_to_set_many(client):
    """The single-key wrapper still upserts and commits (regression guard)."""
    from app.db.session import SessionLocal
    from app.models import Setting
    from app.services import settings_service

    with SessionLocal() as db:
        settings_service.set_value(db, "delegate_probe", "one")
        settings_service.set_value(db, "delegate_probe", "two")  # upsert, not a 2nd row
    with SessionLocal() as db:
        assert settings_service.get(db, "delegate_probe") == "two"
        rows = db.scalars(select(Setting).where(Setting.key == "delegate_probe")).all()
        assert len(rows) == 1


def test_get_values_batch_reads_stored_and_defaults(client):
    """``get_values`` resolves each key exactly like ``get`` — stored value wins, else
    the built-in default — in a single query, and tolerates duplicate keys."""
    from app.db.session import SessionLocal
    from app.services import settings_service

    with SessionLocal() as db:
        settings_service.set_value(db, "gv_stored", "kept")

    with SessionLocal() as db:
        result = settings_service.get_values(
            db,
            [
                "gv_stored",
                settings_service.FX_MODE,  # unset → static default
                "gv_missing",  # unknown key, no default → None
                "gv_stored",  # duplicate is harmless
            ],
        )

    assert result == {
        "gv_stored": "kept",
        settings_service.FX_MODE: "manual",
        "gv_missing": None,
    }


def test_get_values_matches_get_per_key(client):
    """Batch read agrees with the per-key getter for every requested key, and an empty
    request is a no-op."""
    from app.db.session import SessionLocal
    from app.services import settings_service

    with SessionLocal() as db:
        settings_service.set_value(db, "gv_a", "1")
        settings_service.set_value(db, "gv_b", "2")

    keys = ["gv_a", "gv_b", settings_service.LOG_LEVEL, "gv_absent"]
    with SessionLocal() as db:
        batch = settings_service.get_values(db, keys)
        assert batch == {k: settings_service.get(db, k) for k in keys}
        assert settings_service.get_values(db, []) == {}


def test_date_format_round_trips_and_defaults_iso(client):
    """The app-wide date display format defaults to ISO, accepts the allowed values
    (case-insensitive), round-trips on GET, and rejects anything else."""
    # Default before it's ever set.
    assert client.get("/api/settings").json()["date_format"] == "iso"
    # A valid value is normalised to lower-case and persisted.
    ok = client.put("/api/settings", json={"date_format": "US"})
    assert ok.status_code == 200
    assert ok.json()["date_format"] == "us"
    assert client.get("/api/settings").json()["date_format"] == "us"
    # UK is also allowed.
    assert client.put("/api/settings", json={"date_format": "uk"}).json()["date_format"] == "uk"
    # An unknown value is rejected.
    assert client.put("/api/settings", json={"date_format": "ymd"}).status_code == 400
    client.put("/api/settings", json={"date_format": "iso"})  # restore default


def test_base_currency_must_be_supported(client):
    # A curated code is accepted and recomputes conversions for display.
    ok = client.put("/api/settings", json={"base_currency": "USD"})
    assert ok.status_code == 200
    assert ok.json()["base_currency"] == "USD"
    assert "recompute" in ok.json()  # base changed → re-converted
    # An unsupported / free-text code is rejected.
    bad = client.put("/api/settings", json={"base_currency": "XYZ"})
    assert bad.status_code == 400
