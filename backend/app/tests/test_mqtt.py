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


class _PublishInfo:
    """Minimal stand-in for paho's MQTTMessageInfo (only ``rc`` is inspected)."""

    def __init__(self, rc: int) -> None:
        self.rc = rc


class RejectingClient(FakeClient):
    """A client whose publishes are dropped by the broker (non-zero ``rc``)."""

    def __init__(self, rc: int = 4) -> None:  # 4 == MQTT_ERR_NO_CONN
        super().__init__()
        self._rc = rc

    def publish(self, topic: str, payload: str, retain: bool = False) -> _PublishInfo:
        super().publish(topic, payload, retain)
        return _PublishInfo(self._rc)


class _WaitInfo(_PublishInfo):
    """Accepted publish (rc 0) that records ``wait_for_publish`` like paho's info."""

    def __init__(self, client: LoopClient) -> None:
        super().__init__(0)
        self._client = client

    def wait_for_publish(self, timeout: float | None = None) -> None:
        self._client.waited += 1


class LoopClient(FakeClient):
    """FakeClient that also records paho's network-loop + wait-for-publish lifecycle."""

    def __init__(self) -> None:
        super().__init__()
        self.loop_started = False
        self.loop_stopped = False
        self.waited = 0

    def publish(self, topic: str, payload: str, retain: bool = False) -> _WaitInfo:
        super().publish(topic, payload, retain)
        return _WaitInfo(self)

    def loop_start(self) -> None:
        self.loop_started = True

    def loop_stop(self) -> None:
        self.loop_stopped = True


class WillClient(FakeClient):
    """FakeClient that records the LWT (last will) registered before connect."""

    def __init__(self) -> None:
        super().__init__()
        self.will: tuple[str, str, bool] | None = None

    def will_set(self, topic: str, payload: str, retain: bool = False) -> None:
        self.will = (topic, payload, retain)


class ReadClient:
    """Fake broker client for ``read_topics``; records its lifecycle calls."""

    def __init__(self, *, fail_subscribe: bool = False, deliver=None) -> None:
        self.on_message = None
        self.loop_started = False
        self.loop_stopped = False
        self.disconnected = False
        self._fail_subscribe = fail_subscribe
        self._deliver = deliver  # list of (topic, payload) delivered on loop_start

    def subscribe(self, topic: str) -> None:
        if self._fail_subscribe:
            raise RuntimeError("subscribe boom")

    def loop_start(self) -> None:
        self.loop_started = True
        for topic, payload in self._deliver or []:
            if self.on_message:
                self.on_message(self, None, _Msg(topic, payload))

    def loop_stop(self) -> None:
        self.loop_stopped = True

    def disconnect(self) -> None:
        self.disconnected = True


class _Msg:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


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


def test_discovery_config_carries_availability(db):
    from app.services import mqtt_service

    disc = mqtt_service.build_discovery(db)
    assert disc  # at least the core sensors
    avail_topic = "homeassistant/sensor/finance/availability"
    for d in disc:
        cfg = d["config"]
        assert cfg["availability_topic"] == avail_topic
        assert cfg["payload_available"] == "online"
        assert cfg["payload_not_available"] == "offline"
        # expire_after is the freshness backstop; positive when configured (default).
        assert cfg["expire_after"] == mqtt_service.settings.mqtt_expire_after_seconds
        assert cfg["expire_after"] > 0


def test_discovery_omits_expire_after_when_disabled(db, monkeypatch):
    from app.services import mqtt_service

    monkeypatch.setattr(mqtt_service.settings, "mqtt_expire_after_seconds", 0)
    disc = mqtt_service.build_discovery(db)
    assert disc
    assert all("expire_after" not in d["config"] for d in disc)
    # Availability topic + LWT payloads remain even without expire_after.
    assert all(d["config"]["availability_topic"].endswith("/finance/availability") for d in disc)


def test_arm_availability_registers_lwt(db):
    from app.services import mqtt_service

    fake = WillClient()
    mqtt_service._arm_availability(fake)
    assert fake.will == ("homeassistant/sensor/finance/availability", "offline", True)


def test_publish_announces_online_retained(db, monkeypatch):
    from app.services import mqtt_service

    monkeypatch.setattr(mqtt_service.settings, "mqtt_enabled", True)
    fake = FakeClient()
    report = mqtt_service.publish_all(db, connect=lambda: fake)

    assert report["online"] is True
    online = [(t, p, r) for (t, p, r) in fake.published
              if t == "homeassistant/sensor/finance/availability"]
    # A single retained "online" is published; "offline" is only ever the LWT.
    assert online == [("homeassistant/sensor/finance/availability", "online", True)]


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
    assert report["failed"] == 0
    topics = [t for (t, _p, _r) in fake.published]
    assert any(t == "homeassistant/sensor/finance/finance_spend_this_month/config" for t in topics)
    assert any(t == "homeassistant/finance/state/spend_this_month" for t in topics)
    assert all(retain for (_t, _p, retain) in fake.published)  # sensors must be retained
    assert fake.disconnected


def test_publish_counts_broker_rejections(db, monkeypatch):
    from app.services import mqtt_service

    monkeypatch.setattr(mqtt_service.settings, "mqtt_enabled", True)
    fake = RejectingClient(rc=4)  # broker drops every message
    report = mqtt_service.publish_all(db, connect=lambda: fake)

    # Nothing counted as published; every attempted publish is a failure...
    assert report["published"] == 0
    assert report["failed"] == 2 * report["sensors"] + report["cleared"]
    # ...and the client is still cleaned up.
    assert fake.disconnected


def test_publish_all_runs_and_stops_network_loop(db, monkeypatch):
    from app.services import mqtt_service

    monkeypatch.setattr(mqtt_service.settings, "mqtt_enabled", True)
    fake = LoopClient()
    report = mqtt_service.publish_all(db, connect=lambda: fake)

    # The network loop must run so publishes are actually sent, and must always be
    # stopped afterwards (default connect path used to drop messages silently).
    assert fake.loop_started
    assert fake.loop_stopped
    assert fake.disconnected
    # Every accepted publish is waited on so it isn't lost when we disconnect. The
    # extra +1 is the retained "online" announced on the shared availability topic.
    assert report["cleared"] == 0  # nothing disabled by default
    assert fake.waited == report["published"] + 1


def test_read_topics_stops_loop_even_on_error(monkeypatch):
    from app.services import mqtt_service

    monkeypatch.setattr(mqtt_service.settings, "mqtt_enabled", True)
    fake = ReadClient(fail_subscribe=True)  # error mid-read
    result = mqtt_service.read_topics(["home/x"], connect=lambda: fake, timeout=0.1)

    assert result == {}
    assert fake.loop_stopped  # loop_stop must run in finally, not just on the happy path
    assert fake.disconnected


def test_read_topics_reads_retained_payloads(monkeypatch):
    from app.services import mqtt_service

    monkeypatch.setattr(mqtt_service.settings, "mqtt_enabled", True)
    fake = ReadClient(deliver=[("home/x", b"42")])
    result = mqtt_service.read_topics(["home/x"], connect=lambda: fake, timeout=0.5)

    assert result == {"home/x": "42"}
    assert fake.loop_stopped
    assert fake.disconnected


# --- endpoints ---

def test_mqtt_status_endpoint(client):
    st = client.get("/api/mqtt/status").json()
    assert st["enabled"] is False
    assert "available" in st
    assert st["base_topic"] == "homeassistant/finance"
    assert st["sensor_count"] >= 5


def test_status_sensor_count_matches_full_build(db):
    from app.services import mqtt_service

    # The cheap count (ids + selection) must equal the full sensor build exactly.
    assert mqtt_service.status(db)["sensor_count"] == len(mqtt_service._sensors(db))


def test_status_sensor_count_tracks_budgets_and_selection(client):
    base = client.get("/api/mqtt/status").json()["sensor_count"]
    groceries = _cat(client, "Groceries")
    client.post("/api/budgets", json={"name": "Groceries", "amount": "300", "category_id": groceries})
    after = client.get("/api/mqtt/status").json()["sensor_count"]
    assert after == base + 2  # a household budget adds percent + spent sensors

    client.get("/api/users/me")  # local owner = settings-manager
    client.put("/api/mqtt/sensors", json={"disabled_groups": ["counts"], "disabled_sensors": []})
    reduced = client.get("/api/mqtt/status").json()["sensor_count"]
    assert reduced == after - 2  # the 2 "counts" sensors drop out


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


# --- opt-in security events (failed unlock / failed MFA / wrong passphrase) ---

import json as _json  # noqa: E402 - local to the security-event tests below

_EVENT_STATE_TOPIC = "homeassistant/finance/event/security_event"
_EVENT_DISCOVERY_TOPIC = "homeassistant/event/finance/finance_security_event/config"

# The ONLY keys ever allowed in a security-event payload — a guard against a secret
# (passphrase / TOTP code) or PII (user id, name) ever creeping in.
_ALLOWED_PAYLOAD_KEYS = {"event_type", "timestamp", "recent_failures"}


def _enable_security_events(monkeypatch) -> None:
    from app.services import mqtt_service

    monkeypatch.setattr(mqtt_service.settings, "mqtt_enabled", True)
    monkeypatch.setattr(mqtt_service.settings, "mqtt_security_events", True)


def _event_payload(fake: FakeClient) -> dict:
    """The parsed JSON of the non-retained event message on the event state topic."""
    events = [(t, p, r) for (t, p, r) in fake.published if t == _EVENT_STATE_TOPIC]
    assert len(events) == 1
    topic, payload, retain = events[0]
    assert retain is False  # a momentary event is never retained
    return _json.loads(payload)


def test_security_event_payload_builder_shape():
    from app.services import mqtt_service

    payload = mqtt_service.build_security_event_payload("failed_unlock", 3)
    assert payload["event_type"] == "failed_unlock"
    assert payload["recent_failures"] == 3
    assert set(payload) == _ALLOWED_PAYLOAD_KEYS
    assert isinstance(payload["timestamp"], str)


def test_security_event_payload_omits_counter_when_absent():
    from app.services import mqtt_service

    payload = mqtt_service.build_security_event_payload("wrong_passphrase")
    assert payload["event_type"] == "wrong_passphrase"
    assert "recent_failures" not in payload
    assert set(payload) == {"event_type", "timestamp"}


def test_security_event_payload_rejects_unknown_type():
    import pytest

    from app.services import mqtt_service

    with pytest.raises(ValueError, match="unknown security event type"):
        mqtt_service.build_security_event_payload("not_a_real_type")


def test_security_event_discovery_shape():
    from app.services import mqtt_service

    disc = mqtt_service.build_security_event_discovery()
    assert disc["topic"] == _EVENT_DISCOVERY_TOPIC
    cfg = disc["config"]
    assert cfg["unique_id"] == "finance_security_event"
    assert cfg["state_topic"] == _EVENT_STATE_TOPIC
    assert cfg["event_types"] == ["failed_unlock", "failed_mfa", "wrong_passphrase"]
    assert cfg["availability_topic"] == "homeassistant/sensor/finance/availability"
    assert cfg["device"]["identifiers"] == ["ha_finance_intelligence"]


def test_publish_security_event_when_enabled(monkeypatch):
    from app.services import mqtt_service

    _enable_security_events(monkeypatch)
    fake = FakeClient()
    ok = mqtt_service.publish_security_event("failed_unlock", 2, connect=lambda: fake)

    assert ok is True
    # Discovery config is published (retained) so HA registers the entity...
    disc = [(t, p, r) for (t, p, r) in fake.published if t == _EVENT_DISCOVERY_TOPIC]
    assert len(disc) == 1
    assert disc[0][2] is True
    # ...and the event itself carries the type + counter, non-retained, no secret.
    payload = _event_payload(fake)
    assert payload["event_type"] == "failed_unlock"
    assert payload["recent_failures"] == 2
    assert set(payload) <= _ALLOWED_PAYLOAD_KEYS
    assert fake.disconnected


def test_publish_security_event_noop_when_opt_in_off(monkeypatch):
    from app.services import mqtt_service

    # MQTT on, but the security-event opt-in is OFF → nothing is published.
    monkeypatch.setattr(mqtt_service.settings, "mqtt_enabled", True)
    monkeypatch.setattr(mqtt_service.settings, "mqtt_security_events", False)
    fake = FakeClient()
    ok = mqtt_service.publish_security_event("failed_mfa", 1, connect=lambda: fake)

    assert ok is False
    assert fake.published == []


def test_publish_security_event_noop_when_mqtt_off(monkeypatch):
    from app.services import mqtt_service

    # The opt-in is on, but MQTT itself is off → still nothing.
    monkeypatch.setattr(mqtt_service.settings, "mqtt_enabled", False)
    monkeypatch.setattr(mqtt_service.settings, "mqtt_security_events", True)
    fake = FakeClient()
    ok = mqtt_service.publish_security_event("wrong_passphrase", connect=lambda: fake)

    assert ok is False
    assert fake.published == []


def test_publish_security_event_never_raises_on_connect_failure(monkeypatch):
    from app.services import mqtt_service

    _enable_security_events(monkeypatch)

    def _boom():
        raise RuntimeError("broker down")

    # A broker/connect failure must be swallowed, never propagated to the auth flow.
    assert mqtt_service.publish_security_event("failed_unlock", connect=_boom) is False
    mqtt_service.publish_security_event_safe("failed_unlock")  # must not raise


def test_no_secret_in_any_security_event_payload(monkeypatch):
    from app.services import mqtt_service

    _enable_security_events(monkeypatch)
    secret = "hunter2-super-secret"
    for event_type in mqtt_service.SECURITY_EVENT_TYPES:
        fake = FakeClient()
        mqtt_service.publish_security_event(event_type, 4, connect=lambda f=fake: f)
        payload = _event_payload(fake)
        assert set(payload) <= _ALLOWED_PAYLOAD_KEYS
        # Belt-and-braces: no secret-shaped string anywhere in the wire bytes.
        for _t, wire, _r in fake.published:
            assert secret not in wire


def test_record_failed_unlock_publishes_event(monkeypatch):
    from app.services import mqtt_service, security_service

    _enable_security_events(monkeypatch)
    fake = FakeClient()
    monkeypatch.setattr(mqtt_service, "_default_connect", lambda: fake)
    try:
        security_service.record_failed_unlock()
        payload = _event_payload(fake)
        assert payload["event_type"] == "failed_unlock"
        assert payload["recent_failures"] >= 1
    finally:
        security_service.record_successful_unlock()  # clear the streak for other tests


def test_record_mfa_failure_publishes_event(monkeypatch):
    from app.services import mfa_service, mqtt_service

    _enable_security_events(monkeypatch)
    fake = FakeClient()
    monkeypatch.setattr(mqtt_service, "_default_connect", lambda: fake)
    mfa_service.reset_throttle()
    try:
        count = mfa_service.record_mfa_failure(4242)
        assert count == 1
        payload = _event_payload(fake)
        assert payload["event_type"] == "failed_mfa"
        assert payload["recent_failures"] == 1
        # The user id must NOT appear anywhere in what we publish.
        for _t, wire, _r in fake.published:
            assert "4242" not in wire
    finally:
        mfa_service.reset_throttle()


def test_record_mfa_failure_silent_when_disabled(monkeypatch):
    from app.services import mfa_service, mqtt_service

    # Feature off (default): recording a failure must publish nothing.
    fake = FakeClient()
    monkeypatch.setattr(mqtt_service, "_default_connect", lambda: fake)
    mfa_service.reset_throttle()
    try:
        mfa_service.record_mfa_failure(99)
        assert fake.published == []
    finally:
        mfa_service.reset_throttle()
