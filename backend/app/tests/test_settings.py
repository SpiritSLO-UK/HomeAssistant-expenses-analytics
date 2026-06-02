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
