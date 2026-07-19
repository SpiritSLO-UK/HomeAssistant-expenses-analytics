"""Energy-cost offset (HA): offset maths, unit-price derivation, config validation,
source reads, and the settings-manager gate. No real HA/broker is touched — tests
inject readings or mock the source."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.models import Asset, AssetLog, Category, EnergySnapshot, Transaction
from app.services import energy_service, ha_service, split_service
from app.services.split_service import SplitInput

REF = date(2026, 6, 15)


def _hdr(uid: str, name: str | None = None) -> dict[str, str]:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name or uid}


def _approved_member(client, uid: str, name: str) -> int:
    client.get("/api/users/me", headers=_hdr(uid, name))
    mid = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == uid)
    client.patch(f"/api/users/{mid}", json={"role": "member", "status": "approved"})
    return mid


def _energy_category(db, spend: str | None = None) -> int:
    cat = Category(name="Energy", colour="#facc15")
    db.add(cat)
    db.flush()
    if spend is not None:
        amt = Decimal(spend)  # negative = money out
        db.add(Transaction(description_raw="Octopus Energy", amount=amt, base_amount=amt,
                            currency="GBP", direction="debit", transaction_date=date(2026, 6, 10),
                            category_id=cat.id))
    db.commit()
    return cat.id


# --- maths ------------------------------------------------------------------


def test_off_by_default_is_a_noop(db):
    out = energy_service.offset(db, REF)
    assert out["source"] == "off"
    assert out["configured"] is False
    assert out["produced_kwh"] == "0"
    assert out["saving"] == "0.00"
    assert out["net_cost"] == "0.00"


def test_offset_with_tariff_and_spend(db):
    cid = _energy_category(db, spend="-120.00")
    energy_service.validate_and_save(db, {"tariff_per_kwh": "0.30", "energy_category_id": cid})

    out = energy_service.offset(db, REF, readings={"sensor.solar": 50, "sensor.battery": 10})
    assert out["produced_kwh"] == "60"
    assert out["unit_price"] == "0.30"
    assert out["unit_price_source"] == "tariff"
    assert out["saving"] == "18.00"  # 60 * 0.30
    assert out["energy_spend"] == "120.00"
    assert out["net_cost"] == "102.00"  # 120 - 18


def test_unit_price_derived_from_meter_readings(db):
    # Home electricity: 1000 -> 1400 kWh (=400 used) costing £112 → £0.28/kWh.
    asset = Asset(name="Home", kind="home")
    db.add(asset)
    db.flush()
    db.add(AssetLog(asset_id=asset.id, kind="reading", meter="electricity",
                    reading=Decimal("1000"), log_date=date(2026, 5, 1)))
    db.add(AssetLog(asset_id=asset.id, kind="reading", meter="electricity",
                    reading=Decimal("1400"), cost=Decimal("112.00"), log_date=date(2026, 6, 1)))
    db.commit()

    assert energy_service.derive_unit_price(db) == Decimal("0.2800")

    energy_service.validate_and_save(db, {"tariff_per_kwh": ""})  # blank → derive
    out = energy_service.offset(db, REF, readings={"s": 100})
    assert out["unit_price_source"] == "derived"
    assert out["unit_price"] == "0.2800"
    assert out["saving"] == "28.00"  # 100 * 0.28


def test_meter_reset_skips_both_cost_and_kwh(db):
    """A meter reset (reading goes DOWN between two costed readings) must drop BOTH
    the interval's kWh and its cost, so the blended £/kWh isn't skewed. Here the
    only *valid* interval is 100 -> 300 (=200 kWh) costing £50 → £0.25/kWh; the
    reset interval (300 -> 20, £999) is ignored entirely."""
    asset = Asset(name="Home", kind="home")
    db.add(asset)
    db.flush()
    db.add(AssetLog(asset_id=asset.id, kind="reading", meter="electricity",
                    reading=Decimal("100"), log_date=date(2026, 4, 1)))
    db.add(AssetLog(asset_id=asset.id, kind="reading", meter="electricity",
                    reading=Decimal("300"), cost=Decimal("50.00"), log_date=date(2026, 5, 1)))
    # Meter reset: reading drops to 20 with a (would-be-distorting) cost attached.
    db.add(AssetLog(asset_id=asset.id, kind="reading", meter="electricity",
                    reading=Decimal("20"), cost=Decimal("999.00"), log_date=date(2026, 6, 1)))
    db.commit()

    # £50 / 200 kWh = £0.25 — the reset interval's kWh and its £999 are both excluded.
    assert energy_service.derive_unit_price(db) == Decimal("0.2500")


def test_no_price_means_zero_saving(db):
    out = energy_service.offset(db, REF, readings={"s": 100})
    assert out["unit_price"] is None
    assert out["unit_price_source"] == "none"
    assert out["saving"] == "0.00"


def test_last_saving_derives_from_latest_snapshot(db):
    """SR-5: the MQTT saving comes from the latest persisted production snapshot ×
    the current unit price — not a cross-request module-global mutated by offset()."""
    energy_service.validate_and_save(db, {"source": "ha_api", "tariff_per_kwh": "0.30"})
    # No snapshot yet → zero (no global carrying a stale value).
    assert energy_service.last_saving(db) == Decimal("0.00")

    # The LATEST snapshot (by captured_at) drives it — an older one doesn't win a race.
    db.add(EnergySnapshot(captured_at=datetime(2026, 6, 1, 10), produced=Decimal("40"), source="ha_api"))
    db.add(EnergySnapshot(captured_at=datetime(2026, 6, 2, 10), produced=Decimal("50"), source="ha_api"))
    db.commit()
    assert energy_service.last_saving(db) == Decimal("15.00")  # 50 * 0.30

    # Energy off → zero regardless of any snapshots.
    energy_service.validate_and_save(db, {"source": "off"})
    assert energy_service.last_saving(db) == Decimal("0.00")


# --- config validation ------------------------------------------------------


def test_validate_rejects_bad_input(db):
    with pytest.raises(ValueError):
        energy_service.validate_and_save(db, {"source": "nonsense"})
    with pytest.raises(ValueError):
        energy_service.validate_and_save(db, {"tariff_per_kwh": "-1"})
    with pytest.raises(ValueError):
        energy_service.validate_and_save(db, {"production_entities": "not-a-list"})


def test_validate_persists_and_normalises(db):
    cfg = energy_service.validate_and_save(db, {
        "source": "ha_api",
        "production_entities": ["sensor.a", " sensor.b ", ""],
        "tariff_per_kwh": "0.25",
    })
    assert cfg["source"] == "ha_api"
    assert cfg["production_entities"] == ["sensor.a", "sensor.b"]  # trimmed, blanks dropped
    assert energy_service.get_config(db)["tariff_per_kwh"] == "0.25"


# --- source reads -----------------------------------------------------------


def test_ha_service_noop_without_token(db, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert ha_service.available() is False
    assert ha_service.read_states(["sensor.solar"]) == {}


def test_ha_api_source_reads_named_entities(db, monkeypatch):
    monkeypatch.setattr(ha_service, "read_states", lambda ids: {"sensor.solar": 42.0})
    energy_service.validate_and_save(db, {"source": "ha_api", "tariff_per_kwh": "1.00",
                                          "production_entities": ["sensor.solar"]})
    out = energy_service.offset(db, REF)  # no injected readings → uses the (mocked) source
    assert out["produced_kwh"] == "42.0"
    assert out["saving"] == "42.00"


# --- history (energy-bill spend over time) ----------------------------------


def _spend_txn(db, cid, day, amount):
    db.add(Transaction(description_raw="energy", amount=Decimal(amount), base_amount=Decimal(amount),
                       currency="GBP", direction="debit", transaction_date=day, category_id=cid))


def test_history_monthly_spend(db):
    cid = _energy_category(db)
    _spend_txn(db, cid, date(2026, 5, 10), "-80")
    _spend_txn(db, cid, date(2026, 6, 3), "-50")
    _spend_txn(db, cid, date(2026, 6, 20), "-30")
    db.commit()
    energy_service.validate_and_save(db, {"energy_category_id": cid})

    h = energy_service.history(db, period="month", count=3, today=date(2026, 6, 15))
    spend = {b["label"]: Decimal(b["spend"]) for b in h["buckets"]}
    assert [b["label"] for b in h["buckets"]] == ["2026-04", "2026-05", "2026-06"]
    assert spend["2026-04"] == 0
    assert spend["2026-05"] == Decimal("80")
    assert spend["2026-06"] == Decimal("80")  # 50 + 30


def test_history_daily_and_yearly(db):
    cid = _energy_category(db)
    _spend_txn(db, cid, date(2026, 6, 15), "-12")
    db.commit()
    energy_service.validate_and_save(db, {"energy_category_id": cid})

    day = {b["label"]: Decimal(b["spend"]) for b in energy_service.history(
        db, period="day", count=2, today=date(2026, 6, 15))["buckets"]}
    assert day["2026-06-14"] == 0 and day["2026-06-15"] == Decimal("12")

    year = energy_service.history(db, period="year", count=2, today=date(2026, 6, 15))
    assert [b["label"] for b in year["buckets"]] == ["2025", "2026"]
    assert {b["label"]: Decimal(b["spend"]) for b in year["buckets"]}["2026"] == Decimal("12")


def test_history_zero_without_category(db):
    h = energy_service.history(db, period="month", count=2, today=date(2026, 6, 15))
    assert all(Decimal(b["spend"]) == 0 for b in h["buckets"])


def test_history_count_guard_handles_bad_input(db):
    """A None / non-numeric ``count`` (service is callable outside the API's validated
    Query) must not raise — it falls back to the default and clamps to [1, 366]."""
    cid = _energy_category(db)
    energy_service.validate_and_save(db, {"energy_category_id": cid})

    assert len(energy_service.history(db, period="month", count=None, today=REF)["buckets"]) == 12
    assert len(energy_service.history(db, period="month", count="abc", today=REF)["buckets"]) == 12
    # Clamping still applies to valid-but-out-of-range values.
    assert len(energy_service.history(db, period="day", count=0, today=REF)["buckets"]) == 1
    assert len(energy_service.production_history(db, period="month", count=None, today=REF)["buckets"]) == 12


def test_history_endpoint(client):
    client.get("/api/users/me")
    r = client.get("/api/energy/history?period=month&count=3")
    assert r.status_code == 200
    body = r.json()
    assert body["period"] == "month" and len(body["buckets"]) == 3


# --- production / saving trend over time ------------------------------------


def _snap(db, dt: datetime, produced: str) -> None:
    db.add(EnergySnapshot(captured_at=dt, produced=Decimal(produced), source="ha_api"))


def test_production_history_cumulative(db):
    # A cumulative meter total rising over time; per-period = diff across boundaries.
    energy_service.validate_and_save(db, {"production_semantics": "cumulative", "tariff_per_kwh": "0.30"})
    _snap(db, datetime(2026, 5, 5, 12), "1000")
    _snap(db, datetime(2026, 5, 28, 12), "1080")  # May rise: 1080 - 1000 = 80
    _snap(db, datetime(2026, 6, 10, 12), "1130")  # Jun rise: 1130 - 1080 = 50
    db.commit()

    h = energy_service.production_history(db, period="month", count=3, today=date(2026, 6, 15))
    out = {b["label"]: (Decimal(b["produced_kwh"]), Decimal(b["saving"])) for b in h["buckets"]}
    assert h["semantics"] == "cumulative"
    assert out["2026-04"] == (Decimal("0"), Decimal("0.00"))
    assert out["2026-05"][0] == Decimal("80")
    assert out["2026-05"][1] == Decimal("24.00")  # 80 * 0.30
    assert out["2026-06"][0] == Decimal("50")
    assert out["2026-06"][1] == Decimal("15.00")  # 50 * 0.30


def test_production_history_interval(db):
    # Interval sensor: each reading is production since the last; per-period = sum.
    energy_service.validate_and_save(db, {"production_semantics": "interval", "tariff_per_kwh": "0.30"})
    _snap(db, datetime(2026, 5, 10, 9), "30")
    _snap(db, datetime(2026, 5, 20, 9), "20")  # May total of thirty plus twenty
    _snap(db, datetime(2026, 6, 5, 9), "40")   # June total of forty
    db.commit()

    out = {b["label"]: Decimal(b["produced_kwh"]) for b in
           energy_service.production_history(db, period="month", count=3, today=date(2026, 6, 15))["buckets"]}
    assert out["2026-05"] == Decimal("50")
    assert out["2026-06"] == Decimal("40")


def test_record_snapshot_is_throttled(db):
    energy_service.record_snapshot(db, Decimal("10"), "ha_api")
    energy_service.record_snapshot(db, Decimal("20"), "ha_api")  # within the gap → skipped
    rows = db.query(EnergySnapshot).all()
    assert len(rows) == 1
    assert rows[0].produced == Decimal("10")


def test_snapshot_throttle_is_per_source(db):
    """The throttle is keyed per source: one source's recent write must not suppress
    a different source's snapshot (only same-source writes within the gap are skipped)."""
    energy_service.record_snapshot(db, Decimal("10"), "ha_api")
    energy_service.record_snapshot(db, Decimal("20"), "mqtt")   # different source → NOT throttled
    energy_service.record_snapshot(db, Decimal("30"), "ha_api")  # same source, within gap → skipped
    rows = db.query(EnergySnapshot).all()
    by_source = {r.source: r.produced for r in rows}
    assert len(rows) == 2
    assert by_source["ha_api"] == Decimal("10")  # the later ha_api write was throttled out
    assert by_source["mqtt"] == Decimal("20")


def test_offset_non_live_mqtt_uses_last_snapshot(db):
    """A non-live offset (e.g. during an MQTT publish) can't read the broker, so for
    an MQTT source it falls back to the last persisted production snapshot, not 0."""
    energy_service.validate_and_save(db, {"source": "mqtt", "tariff_per_kwh": "0.30"})
    # No snapshot yet → non-live production is 0 (nothing to fall back to).
    assert energy_service.offset(db, REF, live=False)["produced_kwh"] == "0"

    db.add(EnergySnapshot(captured_at=datetime(2026, 6, 2, 10), produced=Decimal("50"), source="mqtt"))
    db.commit()
    out = energy_service.offset(db, REF, live=False)
    assert Decimal(out["produced_kwh"]) == Decimal("50")
    assert out["saving"] == "15.00"  # 50 * 0.30


def test_production_history_endpoint(client):
    client.get("/api/users/me")
    r = client.get("/api/energy/production-history?period=month&count=3")
    assert r.status_code == 200
    body = r.json()
    assert body["period"] == "month" and len(body["buckets"]) == 3
    assert "semantics" in body and "produced_kwh" in body["buckets"][0]


# --- cumulative-vs-interval live offset (finding #9) ------------------------


def test_offset_cumulative_nets_month_start_baseline(db, monkeypatch):
    """A cumulative (lifetime) counter must report the month's *rise*, not the whole
    lifetime reading: month production = current reading - month-start baseline."""
    energy_service.validate_and_save(db, {
        "source": "ha_api", "production_semantics": "cumulative",
        "tariff_per_kwh": "0.30", "production_entities": ["sensor.meter"],
    })
    # Last reading before June → June's baseline (a lifetime total of 1000 kWh).
    db.add(EnergySnapshot(captured_at=datetime(2026, 5, 28, 12), produced=Decimal("1000"), source="ha_api"))
    db.commit()
    monkeypatch.setattr(ha_service, "read_states", lambda ids: {"sensor.meter": 1130.0})

    out = energy_service.offset(db, REF)  # live read → 1130 lifetime kWh
    assert Decimal(out["produced_kwh"]) == Decimal("130")  # 1130 - 1000, NOT 1130
    assert Decimal(out["saving"]) == Decimal("39.00")  # 130 * 0.30


def test_offset_interval_ignores_baseline(db, monkeypatch):
    """An interval sensor's reading already is the period's production, so a baseline
    snapshot must NOT be subtracted (behaviour unchanged)."""
    energy_service.validate_and_save(db, {
        "source": "ha_api", "production_semantics": "interval",
        "tariff_per_kwh": "0.30", "production_entities": ["sensor.meter"],
    })
    db.add(EnergySnapshot(captured_at=datetime(2026, 5, 28, 12), produced=Decimal("1000"), source="ha_api"))
    db.commit()
    monkeypatch.setattr(ha_service, "read_states", lambda ids: {"sensor.meter": 42.0})

    out = energy_service.offset(db, REF)
    assert Decimal(out["produced_kwh"]) == Decimal("42.0")  # raw, no subtraction
    assert Decimal(out["saving"]) == Decimal("12.60")  # 42 * 0.30


def test_last_saving_cumulative_nets_month_start_baseline(db):
    """The MQTT 'this month' sensor must match offset(): a cumulative reading is
    netted against the month-start baseline, not published as the lifetime total."""
    energy_service.validate_and_save(db, {
        "source": "ha_api", "production_semantics": "cumulative", "tariff_per_kwh": "0.30"})
    db.add(EnergySnapshot(captured_at=datetime(2026, 5, 28, 12), produced=Decimal("1000"), source="ha_api"))
    db.add(EnergySnapshot(captured_at=datetime(2026, 6, 20, 12), produced=Decimal("1130"), source="ha_api"))
    db.commit()
    # Latest is June's 1130; baseline (last before June) is 1000 → month rise 130.
    assert energy_service.last_saving(db) == Decimal("39.00")  # 130 * 0.30, not 1130 * 0.30


def test_last_saving_interval_uses_raw_latest(db):
    """An interval sensor's latest reading is the period's production as-is."""
    energy_service.validate_and_save(db, {
        "source": "ha_api", "production_semantics": "interval", "tariff_per_kwh": "0.30"})
    db.add(EnergySnapshot(captured_at=datetime(2026, 5, 28, 12), produced=Decimal("1000"), source="ha_api"))
    db.add(EnergySnapshot(captured_at=datetime(2026, 6, 20, 12), produced=Decimal("40"), source="ha_api"))
    db.commit()
    assert energy_service.last_saving(db) == Decimal("12.00")  # 40 * 0.30, no baseline


# --- split-aware history matches the offset (finding #14) -------------------


def test_offset_and_history_agree_on_split_energy_portion(db):
    """A split transaction whose energy portion lives in a split (the parent sits in
    no category) must be counted identically by the offset's current-month spend and
    history()'s current-month bucket — both split-aware, spendable-filtered."""
    energy = Category(name="Energy", colour="#facc15")
    other = Category(name="Other", colour="#0ea5e9")
    db.add_all([energy, other])
    db.flush()
    parent = Transaction(
        description_raw="Combined utilities bill", amount=Decimal("-100.00"),
        base_amount=Decimal("-100.00"), fx_rate=Decimal("1"), currency="GBP",
        direction="debit", transaction_date=date(2026, 6, 10), category_id=None,
    )
    db.add(parent)
    db.flush()
    split_service.set_splits(db, parent, [
        SplitInput(amount=Decimal("-40.00"), category_id=energy.id),  # the energy portion
        SplitInput(amount=Decimal("-60.00"), category_id=other.id),
    ])
    energy_service.validate_and_save(db, {"energy_category_id": energy.id})

    off_spend = Decimal(energy_service.offset(db, REF)["energy_spend"])
    bucket_spend = Decimal(
        energy_service.history(db, period="month", count=1, today=REF)["buckets"][-1]["spend"]
    )
    assert off_spend == Decimal("40")  # only the energy split part, not the whole 100
    assert bucket_spend == Decimal("40")  # history agrees rather than reporting 0
    assert off_spend == bucket_spend


# --- RBAC -------------------------------------------------------------------


def test_config_gated_to_settings_manager(client):
    client.get("/api/users/me")  # owner (headerless local user)
    assert client.get("/api/energy/config").status_code == 200
    assert client.put("/api/energy/config", json={"source": "off"}).status_code == 200

    _approved_member(client, "ha-bob", "Bob")
    h = _hdr("ha-bob", "Bob")
    assert client.get("/api/energy/config", headers=h).status_code == 403
    assert client.put("/api/energy/config", json={"source": "mqtt"}, headers=h).status_code == 403

    # Offset/status are open reads (account-scoped), not gated to managers.
    assert client.get("/api/energy/offset", headers=h).status_code == 200
    assert client.get("/api/energy/status", headers=h).status_code == 200


def test_offset_endpoint_shape(client):
    client.get("/api/users/me")
    body = client.get("/api/energy/offset").json()
    assert {"month", "source", "produced_kwh", "saving", "net_cost", "energy_spend"} <= body.keys()


def test_invalid_config_returns_400(client):
    client.get("/api/users/me")
    assert client.put("/api/energy/config", json={"tariff_per_kwh": "-5"}).status_code == 400
