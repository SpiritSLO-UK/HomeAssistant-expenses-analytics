"""MQTT sensor publishing for Home Assistant (spec §27, §30.11).

Publishes finance metrics as MQTT sensors using Home Assistant's MQTT discovery
convention (spec §27.2): a retained discovery config per sensor under
``<prefix>/sensor/finance/<object_id>/config`` and a retained state under
``<base_topic>/state/<key>``.

Design notes:
- **Off by default** (strict-local, spec §28.2). Everything no-ops unless
  ``settings.mqtt_enabled``.
- ``paho-mqtt`` is an **optional** dependency (the ``mqtt`` extra); it's imported
  lazily so the app runs without it.
- Publishing is **best-effort** via :func:`publish_safe` — a broker problem must
  never break an import or block startup.
- Payload builders (:func:`build_state`, :func:`build_discovery`) are pure and
  broker-free so they can be unit-tested without a broker.

Subscriptions total (spec §30.11) waits for recurring-payment detection
(Stage 7, §20) and is intentionally not published yet.
"""

from __future__ import annotations

import json
import time
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.logging import get_logger
from app.models import Budget, Project
from app.services import (
    budget_service,
    dashboard_service,
    project_service,
    settings_service,
    subscription_service,
)

logger = get_logger("app.mqtt")

DEVICE = {
    "identifiers": ["ha_finance_intelligence"],
    "name": "HA Finance Intelligence",
    "manufacturer": "Blaz Peternel",
    "model": "Finance",
}

# A single shared availability topic for every sensor (spec §27). We publish a
# retained "online" on a successful publish cycle, and register it as the client's
# LWT with retained "offline" so a broker-detected drop flips every sensor to
# "unavailable" in Home Assistant. Combined with ``expire_after`` on each sensor
# this makes HA show the sensors unavailable when the add-on stops publishing.
AVAILABILITY_ONLINE = "online"
AVAILABILITY_OFFLINE = "offline"


def _sensor(
    key: str,
    name: str,
    value: object,
    *,
    unit: str | None = None,
    device_class: str | None = None,
    state_class: str | None = None,
    icon: str | None = None,
) -> dict:
    return {
        "key": key,
        "object_id": f"finance_{key}",
        "name": f"Finance {name}",
        "value": value,
        "unit": unit,
        "device_class": device_class,
        "state_class": state_class,
        "icon": icon,
    }


def _money(key: str, name: str, value: object, currency: str, icon: str) -> dict:
    return _sensor(
        key, name, value, unit=currency, device_class="monetary",
        state_class="measurement", icon=icon,
    )


def _count(key: str, name: str, value: object, icon: str) -> dict:
    return _sensor(key, name, value, state_class="measurement", icon=icon)


def _pct(key: str, name: str, value: object, icon: str) -> dict:
    return _sensor(key, name, value, unit="%", state_class="measurement", icon=icon)


# Sensor groups for the publish-selection UI (backlog: user picks what to publish).
# Order here is the display order. A sensor's `group` ties it to one of these.
SENSOR_GROUP_LABELS: dict[str, str] = {
    "core": "Monthly totals (spend · income · net)",
    "counts": "Review & uncategorised counts",
    "subscriptions": "Subscriptions total",
    "budgets": "Budgets (one or two per budget)",
    "projects": "Projects (one per project)",
    "energy": "Energy offset",
}


def _all_sensors(db: Session, ref: date | None = None) -> list[dict]:
    """Every sensor we *could* publish, each tagged with its group. The set actually
    published is this filtered by the user's publish-selection (see :func:`_sensors`)."""
    ref = ref or date.today()
    currency = settings_service.get_base_currency(db)
    summary = dashboard_service.summary(db, ref)

    sensors: list[dict] = []

    def _add(group: str, sensor: dict) -> None:
        sensor["group"] = group
        sensors.append(sensor)

    _add("core", _money("spend_this_month", "Spend This Month",
                        summary["spend_this_month"], currency, "mdi:cash-minus"))
    _add("core", _money("income_this_month", "Income This Month",
                        summary["income_this_month"], currency, "mdi:cash-plus"))
    _add("core", _money("net_this_month", "Net This Month", summary["net_this_month"], currency, "mdi:cash"))
    _add("counts", _count("review_items", "Review Items", summary["review_items"], "mdi:alert-circle"))
    _add("counts", _count("uncategorised", "Uncategorised",
                          summary["uncategorised_transactions"], "mdi:help-circle"))
    _add("subscriptions", _money("subscriptions_total", "Subscriptions (monthly)",
                                 subscription_service.monthly_total(db), currency, "mdi:autorenew"))
    for b in budget_service.summary(db, ref):
        bid = b["budget_id"]
        _add("budgets", _pct(f"budget_{bid}_percent", f"Budget {b['name']} %", b["percent"], "mdi:chart-arc"))
        _add("budgets", _money(f"budget_{bid}_spent", f"Budget {b['name']} Spent",
                               b["spent"], b["currency"], "mdi:cash-clock"))
    # Per-project totals (spec §27.3 "Finance House Project Total").
    for p in project_service.totals(db):
        _add("projects", _money(f"project_{p['project_id']}_total", f"Project {p['name']} Total",
                                p["spent"], p["currency"], "mdi:home-currency-usd"))
    # Energy-cost offset (HA), only when configured. Uses the last live-computed
    # saving (energy_service caches it) so a publish never does a broker/HA read.
    from app.services import energy_service  # lazy import: avoids an import cycle

    if energy_service.get_config(db)["source"] != "off":
        _add("energy", _money("energy_offset_this_month", "Energy Offset This Month",
                              str(energy_service.last_saving(db)), currency, "mdi:solar-power"))
    return sensors


# The fixed sensors that always exist, regardless of the data. Kept in lock-step
# with :func:`_all_sensors` so :func:`_sensor_index` can enumerate keys/groups
# without computing any value.
_STATIC_SENSORS: list[tuple[str, str]] = [
    ("core", "spend_this_month"),
    ("core", "income_this_month"),
    ("core", "net_this_month"),
    ("counts", "review_items"),
    ("counts", "uncategorised"),
    ("subscriptions", "subscriptions_total"),
]


def _sensor_index(db: Session) -> list[tuple[str, str]]:
    """``(group, key)`` for every publishable sensor — the same set as
    :func:`_all_sensors`, but WITHOUT computing any value. Cheap enough to just
    count sensors (e.g. for :func:`status`) without rebuilding the whole
    aggregation: it only reads budget/project ids and the energy source."""
    index = list(_STATIC_SENSORS)
    for bid in db.scalars(select(Budget.id).where(Budget.owner_user_id.is_(None))):
        index.append(("budgets", f"budget_{bid}_percent"))
        index.append(("budgets", f"budget_{bid}_spent"))
    for pid in db.scalars(select(Project.id)):
        index.append(("projects", f"project_{pid}_total"))
    from app.services import energy_service  # lazy import: avoids an import cycle

    if energy_service.get_config(db)["source"] != "off":
        index.append(("energy", "energy_offset_this_month"))
    return index


def _selection(db: Session) -> tuple[set[str], set[str]]:
    sel = settings_service.get_mqtt_publish_selection(db)
    return set(sel["groups"]), set(sel["sensors"])


def _is_enabled(sensor: dict, disabled_groups: set[str], disabled_keys: set[str]) -> bool:
    return sensor["group"] not in disabled_groups and sensor["key"] not in disabled_keys


def _sensors(db: Session, ref: date | None = None) -> list[dict]:
    """The sensors that will actually be published — the full set minus the user's
    publish-selection (disabled groups + individually disabled sensors)."""
    dg, dk = _selection(db)
    return [s for s in _all_sensors(db, ref) if _is_enabled(s, dg, dk)]


def list_sensors(db: Session) -> dict:
    """All publishable sensors + the current selection, for the Settings UI:
    ``{groups: [{key,label,disabled}], sensors: [{key,name,group,enabled}]}``."""
    dg, dk = _selection(db)
    sensors = _all_sensors(db)
    # Groups present (in label order), plus any disabled group with no current
    # sensors so it can still be re-enabled.
    present = [g for g in SENSOR_GROUP_LABELS if any(s["group"] == g for s in sensors) or g in dg]
    return {
        "groups": [{"key": g, "label": SENSOR_GROUP_LABELS[g], "disabled": g in dg} for g in present],
        "sensors": [
            {"key": s["key"], "name": s["name"], "group": s["group"], "enabled": _is_enabled(s, dg, dk)}
            for s in sensors
        ],
        # The raw individual-sensor denylist, so the UI can round-trip per-sensor
        # overrides without conflating them with a whole-group disable.
        "disabled_sensors": sorted(dk),
    }


def _state_topic(key: str) -> str:
    return f"{settings.mqtt_base_topic}/state/{key}"


def _discovery_topic(object_id: str) -> str:
    return f"{settings.mqtt_discovery_prefix}/sensor/finance/{object_id}/config"


def _availability_topic() -> str:
    return f"{settings.mqtt_discovery_prefix}/sensor/finance/availability"


def _discovery_config(sensor: dict) -> dict:
    config: dict = {
        "name": sensor["name"],
        "unique_id": sensor["object_id"],
        "state_topic": _state_topic(sensor["key"]),
        "device": DEVICE,
        # Availability wiring so HA marks the sensor "unavailable" when the add-on
        # stops. The LWT drives a broker-detected drop; ``expire_after`` is the
        # backstop for a graceful stop where no fresh state arrives in time.
        "availability_topic": _availability_topic(),
        "payload_available": AVAILABILITY_ONLINE,
        "payload_not_available": AVAILABILITY_OFFLINE,
    }
    if settings.mqtt_expire_after_seconds > 0:
        config["expire_after"] = settings.mqtt_expire_after_seconds
    if sensor.get("unit"):
        config["unit_of_measurement"] = sensor["unit"]
    if sensor.get("device_class"):
        config["device_class"] = sensor["device_class"]
    if sensor.get("state_class"):
        config["state_class"] = sensor["state_class"]
    if sensor.get("icon"):
        config["icon"] = sensor["icon"]
    return config


# --- pure payload builders (broker-free; unit-testable) ---


def build_state(db: Session, ref: date | None = None) -> dict[str, object]:
    """Map of ``{sensor_key: value}`` for the current state topics."""
    return {s["key"]: s["value"] for s in _sensors(db, ref)}


def build_discovery(db: Session, ref: date | None = None) -> list[dict]:
    """List of ``{topic, config}`` MQTT discovery messages."""
    return [
        {"topic": _discovery_topic(s["object_id"]), "config": _discovery_config(s)}
        for s in _sensors(db, ref)
    ]


# --- broker plumbing ---


def _paho_available() -> bool:
    import importlib.util

    try:
        # find_spec on a dotted name imports the ancestor packages, so it raises
        # (not returns None) when 'paho' itself is absent — treat that as "no".
        return importlib.util.find_spec("paho.mqtt.client") is not None
    except ModuleNotFoundError:
        return False


def _arm_availability(client) -> None:
    """Register the LWT (last will) so the broker publishes a retained "offline" to
    the shared availability topic if this client drops without a clean disconnect.

    Must run BEFORE ``connect()`` (paho only honours a will registered before the
    connection). Best-effort: a fake test client without ``will_set`` is a no-op.
    """
    will_set = getattr(client, "will_set", None)
    if will_set is None:
        return
    try:
        will_set(_availability_topic(), AVAILABILITY_OFFLINE, retain=True)
    except Exception:  # pragma: no cover - best effort
        pass


def _default_connect():
    """Connect to the broker with paho-mqtt (lazy import)."""
    try:
        import paho.mqtt.client as mqtt  # pyright: ignore[reportMissingImports]  -- optional 'mqtt' extra
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError("paho-mqtt is not installed; install the 'mqtt' extra") from exc

    try:  # paho 2.x wants an explicit callback API version; 1.x has no such enum
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except (AttributeError, TypeError):  # pragma: no cover - depends on paho version
        client = mqtt.Client()
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password or None)
    # Arrange the LWT before connecting so a broker-detected drop flips sensors offline.
    _arm_availability(client)
    client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
    return client


# Upper bound (seconds) on how long a single publish waits for the network loop
# to actually flush it. In the happy path (QoS 0) each wait returns near-instantly
# once the message hits the socket; this cap only guards a pathological broker.
_PUBLISH_TIMEOUT = 5.0


def _safe_disconnect(client) -> None:
    try:
        client.disconnect()
    except Exception:  # pragma: no cover - best effort
        pass


def _safe_loop_start(client) -> None:
    """Start paho's network loop so queued publishes are actually written to the
    socket before the connection is torn down. A fake test client without
    ``loop_start`` is a no-op."""
    try:
        client.loop_start()
    except Exception:  # pragma: no cover - best effort
        pass


def _safe_loop_stop(client) -> None:
    try:
        client.loop_stop()
    except Exception:  # pragma: no cover - best effort
        pass


def _safe_wait_for_publish(info, timeout: float) -> None:
    """Block until ``info`` is actually published (bounded by ``timeout``). Paho's
    ``MQTTMessageInfo`` exposes ``wait_for_publish``; a fake/``None`` result (tests)
    has none and is a no-op."""
    wait = getattr(info, "wait_for_publish", None)
    if wait is None:
        return
    try:
        wait(timeout)
    except Exception:  # pragma: no cover - best effort
        pass


def _publish(client, topic: str, payload: str, *, wait: float = 0.0) -> bool:
    """Publish one retained message; return ``True`` only if the broker accepted it.

    paho's ``publish()`` returns an ``MQTTMessageInfo`` whose ``rc`` is non-zero
    when the message was dropped (e.g. the client isn't connected). Checking it
    stops a dropped message being reported as a success. A fake/``None`` result
    (used by tests) has no ``rc`` and counts as success.

    When ``wait`` is set and the message was accepted, block up to ``wait`` seconds
    for the network loop to flush it, so a publish isn't lost when the connection
    is torn down immediately afterwards.
    """
    info = client.publish(topic, payload, retain=True)
    ok = getattr(info, "rc", 0) == 0
    if ok and wait:
        _safe_wait_for_publish(info, wait)
    return ok


def _publish_online(client) -> bool:
    """Announce the add-on is publishing: a retained "online" on the shared
    availability topic. Paired with the LWT's retained "offline", this shows the
    sensors available now and flips them unavailable if the client drops.

    A graceful end of cycle deliberately does NOT publish "offline" (that would
    flap the sensors between cycles); ``expire_after`` plus this retained "online"
    cover the idle gaps, and only the LWT ever publishes "offline".
    """
    return _publish(client, _availability_topic(), AVAILABILITY_ONLINE, wait=_PUBLISH_TIMEOUT)


def publish_all(db: Session, ref: date | None = None, connect=None) -> dict:
    """Publish discovery + state for every sensor. Returns a small report.

    ``connect`` is injectable for tests (a factory returning an object with
    ``publish``/``disconnect``). Raises if MQTT can't connect — callers that
    must not fail should use :func:`publish_safe`.
    """
    if not settings.mqtt_enabled:
        return {"enabled": False, "published": 0, "reason": "mqtt disabled"}

    all_sensors = _all_sensors(db, ref)
    dg, dk = _selection(db)
    sensors = [s for s in all_sensors if _is_enabled(s, dg, dk)]
    disabled = [s for s in all_sensors if not _is_enabled(s, dg, dk)]
    client = (connect or _default_connect)()
    # Run the network loop so publishes are actually sent, and wait for each to be
    # flushed before disconnecting — otherwise the default connect path can drop
    # messages silently. The loop is always stopped in the finally (#361 pattern).
    _safe_loop_start(client)
    published = 0
    failed = 0
    online = False
    try:
        # Announce presence first so HA sees the sensors available for this cycle.
        online = _publish_online(client)
        for sensor in sensors:
            if _publish(client, _discovery_topic(sensor["object_id"]),
                        json.dumps(_discovery_config(sensor)), wait=_PUBLISH_TIMEOUT):
                published += 1
            else:
                failed += 1
        for sensor in sensors:
            if _publish(client, _state_topic(sensor["key"]), str(sensor["value"]), wait=_PUBLISH_TIMEOUT):
                published += 1
            else:
                failed += 1
        # Clear the retained discovery config for any sensor the user has disabled,
        # so Home Assistant drops the entity instead of leaving it stale.
        for sensor in disabled:
            if not _publish(client, _discovery_topic(sensor["object_id"]), "", wait=_PUBLISH_TIMEOUT):
                failed += 1
    finally:
        _safe_loop_stop(client)
        _safe_disconnect(client)
    if failed:
        logger.warning("MQTT publish: broker rejected %s message(s)", failed)
    return {
        "enabled": True, "published": published, "failed": failed,
        "sensors": len(sensors), "cleared": len(disabled), "online": online,
    }


def publish_safe(db: Session, ref: date | None = None) -> None:
    """Best-effort publish: log and swallow any error (spec §27 cadence hooks)."""
    if not settings.mqtt_enabled:
        return
    try:
        result = publish_all(db, ref=ref)
        logger.info(
            "MQTT published %s messages for %s sensors (%s failed)",
            result.get("published"), result.get("sensors"), result.get("failed", 0),
        )
    except Exception as exc:
        logger.warning("MQTT publish failed (non-fatal): %s", exc)


def read_topics(topics: list[str], *, connect=None, timeout: float = 2.0) -> dict[str, str]:
    """Read the latest **retained** payload of each topic (best-effort).

    Used by the energy offset's ``mqtt`` source to read production values that a
    broker holds retained. Connects, subscribes, waits up to ``timeout`` seconds
    for the retained messages, then disconnects. Returns ``{topic: payload}``;
    ``{}`` if MQTT is disabled, there are no topics, or nothing arrives.
    ``connect`` is injectable for tests (no real broker needed).
    """
    wanted = [t for t in (topics or []) if t]
    if not settings.mqtt_enabled or not wanted:
        return {}

    results: dict[str, str] = {}

    def _on_message(_client, _userdata, msg) -> None:
        try:
            results[msg.topic] = msg.payload.decode("utf-8", "ignore")
        except (AttributeError, UnicodeDecodeError):  # pragma: no cover - defensive
            pass

    try:
        client = (connect or _default_connect)()
    except Exception as exc:  # best-effort: broker problems must never raise
        logger.warning("MQTT read_topics connect failed (non-fatal): %s", exc)
        return {}
    try:
        client.on_message = _on_message
        for topic in wanted:
            client.subscribe(topic)
        client.loop_start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and len(results) < len(wanted):
            time.sleep(0.05)
    except Exception as exc:  # best-effort
        logger.warning("MQTT read_topics failed (non-fatal): %s", exc)
    finally:
        # Always stop the network loop (and disconnect); an error mid-read must
        # never leak the paho loop thread.
        _safe_loop_stop(client)
        _safe_disconnect(client)
    return results


def status(db: Session | None = None) -> dict:
    """Current MQTT configuration/availability for the Settings UI."""
    info = {
        "enabled": settings.mqtt_enabled,
        "available": _paho_available(),
        "host": settings.mqtt_host,
        "port": settings.mqtt_port,
        "discovery_prefix": settings.mqtt_discovery_prefix,
        "base_topic": settings.mqtt_base_topic,
    }
    if db is not None:
        # Count sensors cheaply (ids + selection only) instead of rebuilding every
        # sensor's payload via the full aggregation.
        dg, dk = _selection(db)
        info["sensor_count"] = sum(
            1 for group, key in _sensor_index(db) if group not in dg and key not in dk
        )
    return info
