"""Home assets: utility meter readings → per-meter usage stats (spec §25.1)."""

from __future__ import annotations

from decimal import Decimal

import pytest


def _home(client, name="Home") -> int:
    r = client.post("/api/assets", json={"name": name, "kind": "home"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _reading(client, aid, *, date, meter, reading, unit="kWh", cost=None):
    body = {"log_date": date, "kind": "reading", "meter": meter, "reading": str(reading), "unit": unit}
    if cost is not None:
        body["cost"] = str(cost)
    r = client.post(f"/api/assets/{aid}/logs", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_home_meter_usage_between_readings(client):
    aid = _home(client)
    _reading(client, aid, date="2026-01-01", meter="electricity", reading=10000, unit="kWh")
    _reading(client, aid, date="2026-01-31", meter="electricity", reading=10300, unit="kWh", cost=90)
    _reading(client, aid, date="2026-03-02", meter="electricity", reading=10600, unit="kWh", cost=85)

    home = client.get(f"/api/assets/{aid}").json()["home"]
    elec = next(m for m in home["meters"] if m["meter"] == "electricity")
    assert elec["unit"] == "kWh"
    assert elec["latest_reading"] == "10600.000"
    assert Decimal(elec["total_usage"]) == Decimal("600")  # (10300-10000)+(10600-10300)
    assert Decimal(elec["total_cost"]) == Decimal("175.00")  # 90 + 85
    assert len(elec["segments"]) == 2
    assert elec["segments"][0]["days"] == 30
    assert elec["segments"][0]["avg_per_day"] == pytest.approx(10.0, abs=0.01)  # 300/30


def test_home_multiple_meters_separated(client):
    aid = _home(client)
    _reading(client, aid, date="2026-01-01", meter="electricity", reading=5000, unit="kWh")
    _reading(client, aid, date="2026-02-01", meter="electricity", reading=5250, unit="kWh")
    _reading(client, aid, date="2026-01-01", meter="water", reading=800, unit="m3")
    _reading(client, aid, date="2026-02-01", meter="water", reading=812, unit="m3")

    meters = {m["meter"]: m for m in client.get(f"/api/assets/{aid}").json()["home"]["meters"]}
    assert Decimal(meters["electricity"]["total_usage"]) == Decimal("250")
    assert Decimal(meters["water"]["total_usage"]) == Decimal("12")
    assert meters["water"]["unit"] == "m3"


def test_meter_rollover_skipped(client):
    aid = _home(client)
    _reading(client, aid, date="2026-01-01", meter="gas", reading=9990, unit="kWh")
    # A reset to a lower number must not produce negative usage.
    _reading(client, aid, date="2026-02-01", meter="gas", reading=120, unit="kWh")
    _reading(client, aid, date="2026-03-01", meter="gas", reading=300, unit="kWh")
    gas = next(m for m in client.get(f"/api/assets/{aid}").json()["home"]["meters"] if m["meter"] == "gas")
    # Only the 120 -> 300 segment counts (the rollover is skipped).
    assert Decimal(gas["total_usage"]) == Decimal("180")
    assert len(gas["segments"]) == 1


def test_home_maintenance_counts_total_cost_not_meters(client):
    aid = _home(client)
    client.post(f"/api/assets/{aid}/logs", json={"log_date": "2026-01-10", "kind": "service", "cost": "120", "note": "Boiler service"})
    _reading(client, aid, date="2026-01-01", meter="electricity", reading=100, unit="kWh")
    _reading(client, aid, date="2026-02-01", meter="electricity", reading=250, unit="kWh", cost=40)
    asset = client.get(f"/api/assets/{aid}").json()
    assert asset["total_cost"] == "160.00"  # 120 service + 40 electricity
    elec = asset["home"]["meters"][0]
    assert Decimal(elec["total_usage"]) == Decimal("150")
