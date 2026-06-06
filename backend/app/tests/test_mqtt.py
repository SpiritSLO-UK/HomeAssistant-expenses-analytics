"""MQTT publishing tests (spec §27, §30.11 — Stage 6).

No real broker: payload builders are pure, and publishing is exercised with an
injected fake client.
"""

from __future__ import annotations


class FakeClient:
    """Records publishes instead of touching a broker."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, bool]] = []
        self.disconnected = False

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self.published.append((topic, payload, retain))

    def disconnect(self) -> None:
        self.disconnected = True


def _cat(client, name: str) -> int:
    return next(c["id"] for c in client.get("/api/categories").json() if c["name"] == name)


# --- pure payload builders ---

def test_preview_has_core_sensors(client):
    preview = client.get("/api/mqtt/preview").json()
    for key in ["spend_this_month", "income_this_month", "net_this_month", "review_items", "uncategorised"]:
        assert key in preview["state"]


def test_discovery_config_shape(db):
    from app.services import mqtt_service

    disc = mqtt_service.build_discovery(db)
    spend = next(d for d in disc if d["topic"].endswith("finance_spend_this_month/config"))
    cfg = spend["config"]
    assert spend["topic"] == "homeassistant/sensor/finance/finance_spend_this_month/config"
    assert cfg["unique_id"] == "finance_spend_this_month"
    assert cfg["state_topic"] == "homeassistant/finance/state/spend_this_month"
    assert cfg["device_class"] == "monetary"
    assert cfg["device"]["identifiers"] == ["ha_finance_intelligence"]


def test_budget_adds_sensors(client):
    groceries = _cat(client, "Groceries")
    bid = client.post(
        "/api/budgets", json={"name": "Groceries", "amount": "300", "category_id": groceries}
    ).json()["id"]
    state = client.get("/api/mqtt/preview").json()["state"]
    assert f"budget_{bid}_percent" in state
    assert f"budget_{bid}_spent" in state


# --- publishing ---

def test_publish_noop_when_disabled(db):
    from app.services import mqtt_service

    report = mqtt_service.publish_all(db)  # disabled by default in tests
    assert report["enabled"] is False
    assert report["published"] == 0


def test_publish_all_when_enabled(db, monkeypatch):
    from app.services import mqtt_service

    monkeypatch.setattr(mqtt_service.settings, "mqtt_enabled", True)
    fake = FakeClient()
    report = mqtt_service.publish_all(db, connect=lambda: fake)

    assert report["enabled"] is True
    assert report["published"] == 2 * report["sensors"]  # discovery + state per sensor
    topics = [t for (t, _p, _r) in fake.published]
    assert any(t == "homeassistant/sensor/finance/finance_spend_this_month/config" for t in topics)
    assert any(t == "homeassistant/finance/state/spend_this_month" for t in topics)
    assert all(retain for (_t, _p, retain) in fake.published)  # sensors must be retained
    assert fake.disconnected


# --- endpoints ---

def test_mqtt_status_endpoint(client):
    st = client.get("/api/mqtt/status").json()
    assert st["enabled"] is False
    assert "available" in st
    assert st["base_topic"] == "homeassistant/finance"
    assert st["sensor_count"] >= 5


def test_mqtt_publish_disabled_returns_400(client):
    assert client.post("/api/mqtt/publish").status_code == 400


# --- publish selection: choose what to publish (group + per-sensor) ---

def _hdr(uid: str, name: str | None = None) -> dict[str, str]:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name or uid}


def test_sensors_endpoint_lists_groups_all_enabled_by_default(client):
    body = client.get("/api/mqtt/sensors").json()
    group_keys = {g["key"] for g in body["groups"]}
    assert {"core", "counts", "subscriptions"} <= group_keys
    assert all(g["disabled"] is False for g in body["groups"])
    assert body["sensors"] and all(s["enabled"] for s in body["sensors"])
    assert all(s["group"] in group_keys for s in body["sensors"])  # every sensor tagged


def test_disable_group_removes_its_sensors_from_publish(client):
    client.get("/api/users/me")  # local owner = settings-manager
    r = client.put("/api/mqtt/sensors", json={"disabled_groups": ["counts"], "disabled_sensors": []})
    assert r.status_code == 200
    counts = next(g for g in r.json()["groups"] if g["key"] == "counts")
    assert counts["disabled"] is True
    assert all(not s["enabled"] for s in r.json()["sensors"] if s["group"] == "counts")
    state = client.get("/api/mqtt/preview").json()["state"]
    assert "review_items" not in state and "uncategorised" not in state
    assert "spend_this_month" in state  # core untouched


def test_disable_individual_sensor(client):
    client.get("/api/users/me")
    client.put("/api/mqtt/sensors", json={"disabled_groups": [], "disabled_sensors": ["net_this_month"]})
    state = client.get("/api/mqtt/preview").json()["state"]
    assert "net_this_month" not in state
    assert "spend_this_month" in state and "income_this_month" in state


def test_publish_clears_discovery_for_disabled(db, monkeypatch):
    from app.services import mqtt_service, settings_service

    settings_service.set_mqtt_publish_selection(db, groups=["counts"], sensors=["net_this_month"])
    monkeypatch.setattr(mqtt_service.settings, "mqtt_enabled", True)
    fake = FakeClient()
    report = mqtt_service.publish_all(db, connect=lambda: fake)
    assert report["cleared"] >= 3  # review_items, uncategorised, net_this_month
    # A disabled sensor gets an EMPTY retained discovery payload so HA drops it...
    net_cfg = [(t, p, r) for (t, p, r) in fake.published if t.endswith("finance_net_this_month/config")]
    assert net_cfg and net_cfg[-1][1] == "" and net_cfg[-1][2] is True
    # ...and its state is never published.
    assert not any(t.endswith("/state/net_this_month") for (t, _p, _r) in fake.published)


def test_set_sensors_requires_manager(client):
    client.get("/api/users/me")  # establish the owner first
    client.get("/api/users/me", headers=_hdr("ha-bob", "Bob"))
    bob = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == "ha-bob")
    client.patch(f"/api/users/{bob}", json={"role": "member", "status": "approved"})
    r = client.put(
        "/api/mqtt/sensors",
        json={"disabled_groups": ["core"], "disabled_sensors": []},
        headers=_hdr("ha-bob", "Bob"),
    )
    assert r.status_code == 403
