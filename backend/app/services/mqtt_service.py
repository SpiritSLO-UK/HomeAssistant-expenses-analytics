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
from datetime import date

from sqlalchemy.orm import Session

from app.config import settings
from app.logging import get_logger
from app.services import budget_service, dashboard_service, project_service, settings_service

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


def _sensors(db: Session, ref: date | None = None) -> list[dict]:
    """The full set of sensors to publish (core metrics + one per budget)."""
    ref = ref or date.today()
    currency = settings_service.get_base_currency(db)
    summary = dashboard_service.summary(db, ref)

    sensors = [
        _money("spend_this_month", "Spend This Month", summary["spend_this_month"], currency, "mdi:cash-minus"),
        _money("income_this_month", "Income This Month", summary["income_this_month"], currency, "mdi:cash-plus"),
        _money("net_this_month", "Net This Month", summary["net_this_month"], currency, "mdi:cash"),
        _count("review_items", "Review Items", summary["review_items"], "mdi:alert-circle"),
        _count("uncategorised", "Uncategorised", summary["uncategorised_transactions"], "mdi:help-circle"),
    ]
    for b in budget_service.summary(db, ref):
        bid = b["budget_id"]
        sensors.append(_pct(f"budget_{bid}_percent", f"Budget {b['name']} %", b["percent"], "mdi:chart-arc"))
        sensors.append(
            _money(f"budget_{bid}_spent", f"Budget {b['name']} Spent", b["spent"], b["currency"], "mdi:cash-clock")
        )
    # Per-project totals (spec §27.3 "Finance House Project Total").
    for p in project_service.totals(db):
        sensors.append(
            _money(f"project_{p['project_id']}_total", f"Project {p['name']} Total",
                   p["spent"], p["currency"], "mdi:home-currency-usd")
        )
    return sensors


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
    try:
        import paho.mqtt.client  # noqa: F401

        return True
    except ImportError:
        return False


def _default_connect():
    """Connect to the broker with paho-mqtt (lazy import)."""
    try:
        import paho.mqtt.client as mqtt
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


def publish_all(db: Session, ref: date | None = None, connect=None) -> dict:
    """Publish discovery + state for every sensor. Returns a small report.

    ``connect`` is injectable for tests (a factory returning an object with
    ``publish``/``disconnect``). Raises if MQTT can't connect — callers that
    must not fail should use :func:`publish_safe`.
    """
    if not settings.mqtt_enabled:
        return {"enabled": False, "published": 0, "reason": "mqtt disabled"}

    sensors = _sensors(db, ref)
    client = (connect or _default_connect)()
    published = 0
    try:
        for sensor in sensors:
            client.publish(
                _discovery_topic(sensor["object_id"]),
                json.dumps(_discovery_config(sensor)),
                retain=True,
            )
            published += 1
        for sensor in sensors:
            client.publish(_state_topic(sensor["key"]), str(sensor["value"]), retain=True)
            published += 1
    finally:
        _safe_disconnect(client)
    return {"enabled": True, "published": published, "sensors": len(sensors)}


def publish_safe(db: Session, ref: date | None = None) -> None:
    """Best-effort publish: log and swallow any error (spec §27 cadence hooks)."""
    if not settings.mqtt_enabled:
        return
    try:
        result = publish_all(db, ref=ref)
        logger.info(
            "MQTT published %s messages for %s sensors",
            result.get("published"), result.get("sensors"),
        )
    except Exception as exc:
        logger.warning("MQTT publish failed (non-fatal): %s", exc)


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
