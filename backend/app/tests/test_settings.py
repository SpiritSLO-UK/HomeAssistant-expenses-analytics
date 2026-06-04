"""Settings endpoint — runtime log-level control (admin log level)."""

from __future__ import annotations

import logging


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


def test_base_currency_must_be_supported(client):
    # A curated code is accepted and recomputes conversions for display.
    ok = client.put("/api/settings", json={"base_currency": "USD"})
    assert ok.status_code == 200
    assert ok.json()["base_currency"] == "USD"
    assert "recompute" in ok.json()  # base changed → re-converted
    # An unsupported / free-text code is rejected.
    bad = client.put("/api/settings", json={"base_currency": "XYZ"})
    assert bad.status_code == 400
