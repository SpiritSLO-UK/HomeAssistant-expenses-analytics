"""Assets: cars/home/other + log timelines, with car consumption stats (§25.1)."""

from __future__ import annotations

import pytest


def _asset(client, name="Family car", kind="car", distance_unit="mi") -> int:
    r = client.post(
        "/api/assets", json={"name": name, "kind": kind, "distance_unit": distance_unit}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _refuel(client, aid, *, date, odometer, litres, cost=None, full=True):
    body = {"log_date": date, "kind": "refuel", "odometer": str(odometer), "litres": str(litres), "is_full_tank": full}
    if cost is not None:
        body["cost"] = str(cost)
    r = client.post(f"/api/assets/{aid}/logs", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_asset_validation(client):
    assert _asset(client)
    assert client.post("/api/assets", json={"name": "x", "kind": "spaceship"}).status_code == 400
    assert client.post("/api/assets", json={"name": "x", "distance_unit": "furlongs"}).status_code == 400


def test_car_consumption_miles_mpg_and_l_per_100km(client):
    aid = _asset(client, distance_unit="mi")
    # Two full fills 300 miles apart, 30 litres at the 2nd fill.
    _refuel(client, aid, date="2026-01-01", odometer=10000, litres=35, cost=50)
    _refuel(client, aid, date="2026-01-15", odometer=10300, litres=30, cost=45)

    car = client.get(f"/api/assets/{aid}").json()["car"]
    assert car["refuel_count"] == 2
    assert car["latest_odometer"] == "10300.0"
    # 300 mi on 30 L (imperial gallon = 4.54609 L):
    #   gallons = 30 / 4.54609 = 6.5986 → MPG = 300 / 6.5986 ≈ 45.5
    assert car["avg_mpg"] == pytest.approx(45.5, abs=0.2)
    #   km = 300 * 1.609344 = 482.8 → L/100km = 30 / 482.8 * 100 ≈ 6.21
    assert car["avg_l_per_100km"] == pytest.approx(6.21, abs=0.05)
    assert len(car["segments"]) == 1
    assert car["total_fuel_cost"] == "95.00"


def test_partial_fill_excluded_from_consumption(client):
    aid = _asset(client, distance_unit="mi")
    _refuel(client, aid, date="2026-01-01", odometer=20000, litres=40)
    # A partial fill can't anchor a tank-to-tank figure.
    _refuel(client, aid, date="2026-01-10", odometer=20150, litres=15, full=False)
    car = client.get(f"/api/assets/{aid}").json()["car"]
    assert car["segments"] == []
    assert car["avg_mpg"] is None


def test_km_unit_consumption(client):
    aid = _asset(client, distance_unit="km")
    _refuel(client, aid, date="2026-02-01", odometer=50000, litres=45)
    _refuel(client, aid, date="2026-02-20", odometer=50500, litres=40)  # 500 km on 40 L
    car = client.get(f"/api/assets/{aid}").json()["car"]
    # 40 L / 500 km * 100 = 8.0 L/100km
    assert car["avg_l_per_100km"] == pytest.approx(8.0, abs=0.01)
    # 500 km = 310.7 mi; 40 L = 8.798 gal → 35.3 MPG
    assert car["avg_mpg"] == pytest.approx(35.3, abs=0.3)


def test_service_expense_logs_count_toward_total_cost(client):
    aid = _asset(client)
    client.post(f"/api/assets/{aid}/logs", json={"log_date": "2026-03-01", "kind": "service", "cost": "220", "note": "MOT + service"})
    _refuel(client, aid, date="2026-03-02", odometer=30000, litres=40, cost=60)
    asset = client.get(f"/api/assets/{aid}").json()
    assert asset["total_cost"] == "280.00"  # 220 + 60
    assert asset["log_count"] == 2


def test_logs_listed_and_deletable(client):
    aid = _asset(client)
    log = _refuel(client, aid, date="2026-01-01", odometer=1000, litres=30, cost=40)
    assert len(client.get(f"/api/assets/{aid}/logs").json()) == 1
    assert client.delete(f"/api/assets/logs/{log['id']}").status_code == 204
    assert client.get(f"/api/assets/{aid}/logs").json() == []


def test_update_and_delete_asset(client):
    aid = _asset(client, name="Old name")
    patched = client.patch(f"/api/assets/{aid}", json={"name": "New name", "distance_unit": "km"}).json()
    assert patched["name"] == "New name" and patched["distance_unit"] == "km"
    # Bad distance unit rejected.
    assert client.patch(f"/api/assets/{aid}", json={"distance_unit": "leagues"}).status_code == 400
    assert client.delete(f"/api/assets/{aid}").status_code == 204
    assert client.get(f"/api/assets/{aid}").status_code == 404


def test_log_on_missing_asset_404(client):
    assert client.post("/api/assets/9999/logs", json={"log_date": "2026-01-01", "kind": "service", "cost": "10"}).status_code == 404


def test_list_filtered_by_kind(client):
    _asset(client, name="Car", kind="car")
    _asset(client, name="House", kind="home")
    cars = client.get("/api/assets?kind=car").json()
    assert all(a["kind"] == "car" for a in cars) and len(cars) == 1
