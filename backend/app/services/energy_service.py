"""Energy-cost offset (HA-native).

Nets the energy you *produce* (solar/grid/etc., read from Home Assistant) against
what you *spend* on your energy bill (the transactions in a chosen category), to
show a live "your production knocked £X off this month" figure.

Production is read from a user-selectable source (off by default):
- ``ha_api`` — read named HA entities via the Supervisor (``ha_service``).
- ``mqtt``   — read retained MQTT topics (``mqtt_service.read_topics``).

The unit price (£/kWh) is either an explicit tariff or, if blank, **derived** from
the Home utility-meter readings (``AssetLog``: total cost ÷ total usage). The
energy-bill spend reuses the dashboard's split-aware, account-scoped, archived-
excluded category breakdown, so it matches every other figure in the app.

All maths is pure and unit-testable: :func:`offset` accepts injected ``readings``
so tests never touch HA or a broker.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AssetLog, Transaction
from app.services import dashboard_service, ha_service, settings_service
from app.services.scope import account_scope_condition, archived_condition

HISTORY_PERIODS = {"day", "month", "year"}

ENERGY_SOURCES = settings_service.ENERGY_SOURCES  # {"off", "ha_api", "mqtt"}

# Last live-computed offset, so the MQTT sensor can publish a value without doing
# its own (potentially circular) broker/HA read during a publish. Updated whenever
# offset(..., live=True) runs (the Energy page / GET endpoint).
_LAST_SAVING: Decimal = Decimal("0")


# --- config -----------------------------------------------------------------


def _json_list(raw: str | None) -> list[str]:
    try:
        val = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return [str(x).strip() for x in val if str(x).strip()] if isinstance(val, list) else []


def get_config(db: Session) -> dict:
    source = settings_service.get(db, settings_service.ENERGY_SOURCE) or "off"
    if source not in ENERGY_SOURCES:
        source = "off"
    cat_raw = (settings_service.get(db, settings_service.ENERGY_CATEGORY_ID) or "").strip()
    try:
        category_id = int(cat_raw) if cat_raw else None
    except ValueError:
        category_id = None
    return {
        "source": source,
        "production_entities": _json_list(settings_service.get(db, settings_service.ENERGY_PRODUCTION_ENTITIES)),
        "production_topics": _json_list(settings_service.get(db, settings_service.ENERGY_PRODUCTION_TOPICS)),
        "tariff_per_kwh": (settings_service.get(db, settings_service.ENERGY_TARIFF_PER_KWH) or "").strip(),
        "energy_category_id": category_id,
    }


def validate_and_save(db: Session, payload: dict) -> dict:
    """Validate a partial config update and persist it. Raises ``ValueError`` on
    bad input. Only the keys present in ``payload`` are changed."""
    if "source" in payload:
        source = str(payload["source"])
        if source not in ENERGY_SOURCES:
            raise ValueError(f"source must be one of {sorted(ENERGY_SOURCES)}")
        settings_service.set_value(db, settings_service.ENERGY_SOURCE, source)
    if "production_entities" in payload:
        items = payload["production_entities"] or []
        if not isinstance(items, list):
            raise ValueError("production_entities must be a list")
        settings_service.set_value(
            db, settings_service.ENERGY_PRODUCTION_ENTITIES,
            json.dumps([str(x).strip() for x in items if str(x).strip()]),
        )
    if "production_topics" in payload:
        items = payload["production_topics"] or []
        if not isinstance(items, list):
            raise ValueError("production_topics must be a list")
        settings_service.set_value(
            db, settings_service.ENERGY_PRODUCTION_TOPICS,
            json.dumps([str(x).strip() for x in items if str(x).strip()]),
        )
    if "tariff_per_kwh" in payload:
        raw = str(payload["tariff_per_kwh"] or "").strip()
        if raw:
            try:
                if Decimal(raw) < 0:
                    raise ValueError
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("tariff_per_kwh must be a non-negative number") from exc
        settings_service.set_value(db, settings_service.ENERGY_TARIFF_PER_KWH, raw)
    if "energy_category_id" in payload:
        cid = payload["energy_category_id"]
        settings_service.set_value(
            db, settings_service.ENERGY_CATEGORY_ID, "" if cid in (None, "") else str(int(cid))
        )
    return get_config(db)


# --- unit price -------------------------------------------------------------


def derive_unit_price(db: Session) -> Decimal | None:
    """Blended £/kWh from Home electricity meter readings (total cost ÷ total
    usage across consecutive readings), or ``None`` if there isn't enough data."""
    logs = db.scalars(
        select(AssetLog)
        .where(
            AssetLog.kind == "reading",
            AssetLog.meter.ilike("electric%"),
            AssetLog.reading.is_not(None),
        )
        .order_by(AssetLog.asset_id, AssetLog.log_date, AssetLog.id)
    ).all()

    total_usage = Decimal("0")
    total_cost = Decimal("0")
    by_asset: dict[int, list[AssetLog]] = {}
    for log in logs:
        by_asset.setdefault(log.asset_id, []).append(log)
    for entries in by_asset.values():
        for prev, cur in zip(entries, entries[1:], strict=False):
            if prev.reading is None or cur.reading is None or cur.cost is None:
                continue
            usage = cur.reading - prev.reading
            if usage > 0 and cur.cost > 0:
                total_usage += usage
                total_cost += cur.cost
    if total_usage <= 0:
        return None
    return (total_cost / total_usage).quantize(Decimal("0.0001"))


# --- production -------------------------------------------------------------


def _sum_numeric(values) -> Decimal:
    total = Decimal("0")
    for v in values:
        try:
            total += Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError):
            continue
    return total


def _production_kwh(db: Session, cfg: dict, *, live: bool) -> Decimal:
    """Total produced kWh from the configured source. ``live=False`` (used during
    an MQTT publish) skips any broker read to avoid latency/recursion."""
    source = cfg["source"]
    if source == "ha_api":
        states = ha_service.read_states(cfg["production_entities"])
        return _sum_numeric(states.values())
    if source == "mqtt" and live:
        from app.services import mqtt_service  # lazy: avoid import cycle

        payloads = mqtt_service.read_topics(cfg["production_topics"])
        return _sum_numeric(payloads.values())
    return Decimal("0")


# --- offset -----------------------------------------------------------------


def _energy_spend(db: Session, ref: date, category_id: int | None, account_ids: set[int] | None) -> Decimal:
    if category_id is None:
        return Decimal("0")
    for row in dashboard_service.category_breakdown(db, ref, account_ids=account_ids):
        if row["category_id"] == category_id:
            return Decimal(row["total"])
    return Decimal("0")


def offset(
    db: Session,
    ref: date,
    *,
    account_ids: set[int] | None = None,
    readings: dict[str, float] | None = None,
    live: bool = True,
) -> dict:
    """The energy-cost offset for ``ref``'s month.

    ``readings`` (tests) injects produced values directly, bypassing any source.
    """
    global _LAST_SAVING
    cfg = get_config(db)
    currency = settings_service.get_base_currency(db)

    if readings is not None:
        produced = _sum_numeric(readings.values())
    elif cfg["source"] == "off":
        produced = Decimal("0")
    else:
        produced = _production_kwh(db, cfg, live=live)

    tariff = cfg["tariff_per_kwh"]
    if tariff:
        unit_price: Decimal | None = Decimal(tariff)
        price_source = "tariff"
    else:
        unit_price = derive_unit_price(db)
        price_source = "derived" if unit_price is not None else "none"

    saving = (produced * unit_price).quantize(Decimal("0.01")) if unit_price is not None else Decimal("0.00")
    spend = _energy_spend(db, ref, cfg["energy_category_id"], account_ids)
    net = (spend - saving).quantize(Decimal("0.01"))

    if live and cfg["source"] != "off":
        _LAST_SAVING = saving

    start, _ = dashboard_service.month_bounds(ref)
    return {
        "month": start.isoformat(),
        "currency": currency,
        "source": cfg["source"],
        "configured": cfg["source"] != "off",
        "available": _source_available(cfg["source"]),
        "produced_kwh": str(produced),
        "unit_price": str(unit_price) if unit_price is not None else None,
        "unit_price_source": price_source,
        "saving": str(saving),
        "energy_spend": str(spend),
        "net_cost": str(net),
        "energy_category_id": cfg["energy_category_id"],
    }


def _source_available(source: str) -> bool:
    if source == "ha_api":
        return ha_service.available()
    if source == "mqtt":
        from app.config import settings  # lazy

        return bool(settings.mqtt_enabled)
    return False


def last_saving() -> Decimal:
    """The most recent live-computed saving — for the MQTT sensor (no live read)."""
    return _LAST_SAVING


def status(db: Session) -> dict:
    """Source availability + config summary for the Settings/Energy UI."""
    cfg = get_config(db)
    return {
        **cfg,
        "available": _source_available(cfg["source"]),
        "ha_api_available": ha_service.available(),
        "derived_unit_price": (lambda p: str(p) if p is not None else None)(derive_unit_price(db)),
    }


# --- history (energy-bill spend over time) ----------------------------------
#
# Production/saving over time is intentionally NOT charted yet: HA energy sensors
# are point-in-time and could be cumulative ("this-month") or per-interval, so
# their snapshots can't be aggregated safely without knowing the semantics. The
# spend series below is unambiguous (the ledger) and answers "what's my energy
# bill doing day/month/year over time". (Production trend = a documented follow-up.)


def _period_buckets(period: str, count: int, today: date) -> list[tuple[str, date, date]]:
    """`count` consecutive ``(label, start, end)`` windows ending with the one
    containing ``today`` (``end`` exclusive)."""
    out: list[tuple[str, date, date]] = []
    if period == "day":
        for i in range(count - 1, -1, -1):
            d = today - timedelta(days=i)
            out.append((d.isoformat(), d, d + timedelta(days=1)))
    elif period == "year":
        for i in range(count - 1, -1, -1):
            y = today.year - i
            out.append((str(y), date(y, 1, 1), date(y + 1, 1, 1)))
    else:  # month
        ym = today.year * 12 + (today.month - 1)
        for i in range(count - 1, -1, -1):
            y, m0 = divmod(ym - i, 12)
            m = m0 + 1
            end = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
            out.append((f"{y:04d}-{m:02d}", date(y, m, 1), end))
    return out


def _spend_in_range(
    db: Session, category_id: int, start: date, end: date, account_ids: set[int] | None
) -> Decimal:
    val = db.scalar(
        select(func.coalesce(func.sum(-Transaction.base_amount), 0)).where(
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
            Transaction.base_amount < 0,
            Transaction.category_id == category_id,
            Transaction.is_transfer.is_(False),
            Transaction.is_duplicate.is_(False),
            Transaction.base_amount.is_not(None),
            *account_scope_condition(account_ids),
            *archived_condition(),
        )
    )
    return Decimal(str(val or 0))


def history(
    db: Session,
    *,
    period: str = "month",
    count: int = 12,
    account_ids: set[int] | None = None,
    today: date | None = None,
) -> dict:
    """Energy-bill spend over time (day/month/year), from the ledger — full
    history, independent of when the offset was first configured."""
    if period not in HISTORY_PERIODS:
        period = "month"
    count = max(1, min(int(count), 366))
    ref = today or date.today()
    cid = get_config(db)["energy_category_id"]
    buckets = [
        {"label": label, "spend": str(_spend_in_range(db, cid, start, end, account_ids) if cid else Decimal("0"))}
        for label, start, end in _period_buckets(period, count, ref)
    ]
    return {
        "period": period,
        "currency": settings_service.get_base_currency(db),
        "energy_category_id": cid,
        "buckets": buckets,
    }
