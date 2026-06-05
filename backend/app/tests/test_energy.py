"""Energy-cost offset (HA): offset maths, unit-price derivation, config validation,
source reads, and the settings-manager gate. No real HA/broker is touched — tests
inject readings or mock the source."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models import Asset, AssetLog, Category, Transaction
from app.services import energy_service, ha_service

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


def test_no_price_means_zero_saving(db):
    out = energy_service.offset(db, REF, readings={"s": 100})
    assert out["unit_price"] is None
    assert out["unit_price_source"] == "none"
    assert out["saving"] == "0.00"


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
