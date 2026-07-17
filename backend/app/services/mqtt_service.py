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

from sqlalchemy.orm import Session

from app.config import settings
from app.logging import get_logger
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


def _discovery_config(sensor: dict) -> dict:
    config: dict = {
        "name": sensor["name"],
        "unique_id": sensor["object_id"],
        "state_topic": _state_topic(sensor["key"]),
        "device": DEVICE,
    }
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
    client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
    return client


def _safe_disconnect(client) -> None:
    try:
        client.disconnect()
    except Exception:  # pragma: no cover - best effort
        pass


def _safe_loop_stop(client) -> None:
    try:
        client.loop_stop()
    except Exception:  # pragma: no cover - best effort
        pass


def _publish(client, topic: str, payload: str) -> bool:
    """Publish one retained message; return ``True`` only if the broker accepted it.

    paho's ``publish()`` returns an ``MQTTMessageInfo`` whose ``rc`` is non-zero
    when the message was dropped (e.g. the client isn't connected). Checking it
    stops a dropped message being reported as a success. A fake/``None`` result
    (used by tests) has no ``rc`` and counts as success.
    """
    return getattr(client.publish(topic, payload, retain=True), "rc", 0) == 0


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
    published = 0
    failed = 0
    try:
        for sensor in sensors:
            if _publish(client, _discovery_topic(sensor["object_id"]),
                        json.dumps(_discovery_config(sensor))):
                published += 1
            else:
                failed += 1
        for sensor in sensors:
            if _publish(client, _state_topic(sensor["key"]), str(sensor["value"])):
                published += 1
            else:
                failed += 1
        # Clear the retained discovery config for any sensor the user has disabled,
        # so Home Assistant drops the entity instead of leaving it stale.
        for sensor in disabled:
            if not _publish(client, _discovery_topic(sensor["object_id"]), ""):
                failed += 1
    finally:
        _safe_disconnect(client)
    if failed:
        logger.warning("MQTT publish: broker rejected %s message(s)", failed)
    return {
        "enabled": True, "published": published, "failed": failed,
        "sensors": len(sensors), "cleared": len(disabled),
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
        info["sensor_count"] = len(_sensors(db))
    return info
