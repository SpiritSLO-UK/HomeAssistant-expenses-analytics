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
