"""Assets: cars/home/other + log timelines, with car consumption stats (§25.1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

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


def test_car_reports_one_system_not_a_mix(client):
    # Imperial (miles): economy in MPG, fuel total in gallons.
    imp = _asset(client, name="Imperial car", distance_unit="mi")
    _refuel(client, imp, date="2026-01-01", odometer=10000, litres=35)
    _refuel(client, imp, date="2026-01-20", odometer=10300, litres=30)  # 300 mi on 30 L
    car = client.get(f"/api/assets/{imp}").json()["car"]
    assert car["system"] == "imperial"
    assert car["economy_unit"] == "MPG"
    assert car["fuel_unit"] == "gal"
    assert car["avg_economy"] == car["avg_mpg"]
    assert Decimal(car["total_fuel"]) == pytest.approx(Decimal(str(30 / 4.54609)), abs=Decimal("0.01"))  # gallons

    # Metric (km): economy in L/100km, fuel total in litres.
    met = _asset(client, name="Metric car", distance_unit="km")
    _refuel(client, met, date="2026-02-01", odometer=50000, litres=45)
    _refuel(client, met, date="2026-02-20", odometer=50500, litres=40)  # 500 km on 40 L
    car = client.get(f"/api/assets/{met}").json()["car"]
    assert car["system"] == "metric"
    assert car["economy_unit"] == "L/100km"
    assert car["fuel_unit"] == "L"
    assert car["avg_economy"] == car["avg_l_per_100km"]
    assert Decimal(car["total_fuel"]) == Decimal("40")  # litres unchanged
    assert car["segments"][0]["economy"] == car["segments"][0]["l_per_100km"]


def test_refuels_segment_by_date_not_odometer(client):
    # Fills are entered out of odometer order but their DATES are chronological:
    # a lower odometer was logged *after* a higher one (e.g. a corrected/edited
    # entry). Segmentation must follow the dates, not the odometer values.
    aid = _asset(client, distance_unit="mi")
    # Chronology: 01-01 @10000 → 01-15 @10300 (300 mi, 30 L) → 02-01 @10600 (300 mi, 30 L)
    # Enter the 02-01 row FIRST so insertion/id order ≠ chronology, and it has a
    # higher odometer than the middle one.
    _refuel(client, aid, date="2026-02-01", odometer=10600, litres=30, cost=45)
    _refuel(client, aid, date="2026-01-01", odometer=10000, litres=35, cost=50)
    _refuel(client, aid, date="2026-01-15", odometer=10300, litres=30, cost=45)

    car = client.get(f"/api/assets/{aid}").json()["car"]
    # Two chronological 300 mi / 30 L segments, both counted.
    assert len(car["segments"]) == 2
    # Segments appear in date order.
    assert [s["date"] for s in car["segments"]] == ["2026-01-15", "2026-02-01"]
    assert car["latest_odometer"] == "10600.0"  # last chronological, not max
    assert car["avg_mpg"] == pytest.approx(45.5, abs=0.2)


def test_decreasing_odometer_between_dates_is_skipped(client):
    # Odometer goes DOWN between two chronological fills (mistyped reading).
    # That yields a non-positive distance and must be skipped, not emitted as a
    # negative-distance segment.
    aid = _asset(client, distance_unit="mi")
    _refuel(client, aid, date="2026-01-01", odometer=10000, litres=35)
    _refuel(client, aid, date="2026-01-15", odometer=9800, litres=30)  # typo: lower
    car = client.get(f"/api/assets/{aid}").json()["car"]
    assert car["segments"] == []
    assert car["avg_mpg"] is None


def test_partial_fill_without_flag_is_not_full(client):
    # A refuel whose full-tank flag was never recorded (None) must NOT anchor a
    # tank-to-tank segment — economy is measured full-to-full, so only an
    # explicit full tank counts.
    from app.db.session import SessionLocal
    from app.services import asset_service

    aid = _asset(client, distance_unit="mi")
    first = _refuel(client, aid, date="2026-01-01", odometer=20000, litres=40)
    second = _refuel(client, aid, date="2026-01-10", odometer=20150, litres=15)

    # Force the middle fill's flag to unrecorded (None) directly in the DB, the
    # way legacy/imported rows can be, then recompute.
    db = SessionLocal()
    try:
        log = asset_service.get_log(db, second["id"])
        log.is_full_tank = None
        db.commit()
        asset = asset_service.get_asset(db, aid)
        car = asset_service.car_stats(db, asset)
    finally:
        db.close()

    assert car["segments"] == []
    assert car["avg_mpg"] is None
    # First fill is still explicitly full; sanity that only the None one dropped.
    assert first["is_full_tank"] is True


def test_segment_fuel_cost_separates_from_total(client):
    # total_fuel_cost = all refuel spend; segment_fuel_cost = only fills that
    # anchor a measured segment. A trailing partial fill's cost is in the total
    # but not in the segment cost.
    aid = _asset(client, distance_unit="mi")
    _refuel(client, aid, date="2026-01-01", odometer=30000, litres=40, cost=55)
    _refuel(client, aid, date="2026-01-15", odometer=30300, litres=30, cost=45)  # full → counted
    _refuel(client, aid, date="2026-01-20", odometer=30400, litres=10, cost=15, full=False)  # partial

    car = client.get(f"/api/assets/{aid}").json()["car"]
    assert len(car["segments"]) == 1
    assert car["total_fuel_cost"] == "115.00"  # 55 + 45 + 15
    assert car["segment_fuel_cost"] == "45.00"  # only the 2nd (segment-anchoring) fill


def test_add_log_service_rejects_nonsensical_values(client):
    # The service layer guards against negative/nonsensical numbers on insert
    # (defence-in-depth beyond the API schema), for direct/importer callers.
    from app.db.session import SessionLocal
    from app.services import asset_service

    aid = _asset(client, distance_unit="mi")
    bad_inserts = [
        {"odometer": Decimal("-1"), "litres": Decimal("30")},          # negative odometer
        {"odometer": Decimal("100"), "litres": Decimal("-5")},         # negative litres
        {"odometer": Decimal("100"), "litres": Decimal("0")},          # zero litres
        {"odometer": Decimal("100"), "litres": Decimal("30"), "cost": Decimal("-10")},  # negative cost
        {"kind": "reading", "meter": "elec", "reading": Decimal("-3")},  # negative reading
    ]
    d1 = date(2026, 1, 1)
    d2 = date(2026, 1, 15)
    db = SessionLocal()
    try:
        for bad in bad_inserts:
            with pytest.raises(ValueError):
                asset_service.add_log(db, aid, log_date=d1, **bad)
        # A backwards odometer (below an earlier fill) is NOT an insert error —
        # it is tolerated and skipped by the tank-to-tank calc.
        asset_service.add_log(db, aid, log_date=d1,
                              odometer=Decimal("10000"), litres=Decimal("35"))
        later = asset_service.add_log(db, aid, log_date=d2,
                                      odometer=Decimal("9800"), litres=Decimal("30"))
        assert later.odometer == Decimal("9800")
    finally:
        db.close()


def test_list_filtered_by_kind(client):
    _asset(client, name="Car", kind="car")
    _asset(client, name="House", kind="home")
    cars = client.get("/api/assets?kind=car").json()
    assert all(a["kind"] == "car" for a in cars) and len(cars) == 1
