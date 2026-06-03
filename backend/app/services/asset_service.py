"""Assets (car/home/other) and their log timelines (spec §25.1).

Car refuel logs yield consumption stats. The UK convention is miles on the
odometer, fuel bought in litres, economy quoted in **MPG (imperial gallon)** — so
we report both **MPG** and **L/100km** regardless of the asset's distance unit.

Consumption is tank-to-tank: the fuel added at a *full* fill divided by the
distance since the previous *full* fill. Partial fills (``is_full_tank=False``)
don't anchor a segment.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, AssetLog
from app.services.household_service import get_or_create_default_household

ASSET_KINDS = {"car", "home", "other"}
DISTANCE_UNITS = {"mi", "km"}
LOG_KINDS = {"refuel", "service", "expense", "reading", "note"}

TWO_DP = Decimal("0.01")
IMPERIAL_GALLON_L = Decimal("4.54609")  # UK gallon (MPG is imperial here)
MI_PER_KM = Decimal("1.609344")


# --- Assets ------------------------------------------------------------------


def list_assets(db: Session, *, kind: str | None = None, active_only: bool = True) -> list[Asset]:
    stmt = select(Asset)
    if active_only:
        stmt = stmt.where(Asset.is_active.is_(True))
    if kind is not None:
        stmt = stmt.where(Asset.kind == kind)
    return list(db.scalars(stmt.order_by(Asset.name)).all())


def get_asset(db: Session, asset_id: int) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise ValueError("Asset not found")
    return asset


def create_asset(db: Session, *, name: str, kind: str = "car",
                 identifier: str | None = None, distance_unit: str = "mi") -> Asset:
    if kind not in ASSET_KINDS:
        raise ValueError(f"kind must be one of {sorted(ASSET_KINDS)}")
    if distance_unit not in DISTANCE_UNITS:
        raise ValueError(f"distance_unit must be one of {sorted(DISTANCE_UNITS)}")
    asset = Asset(
        household_id=get_or_create_default_household(db).id,
        name=name.strip(),
        kind=kind,
        identifier=(identifier or None),
        distance_unit=distance_unit,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def update_asset(db: Session, asset: Asset, **fields) -> Asset:
    if fields.get("distance_unit") is not None and fields["distance_unit"] not in DISTANCE_UNITS:
        raise ValueError(f"distance_unit must be one of {sorted(DISTANCE_UNITS)}")
    for key in ("name", "identifier", "distance_unit", "is_active"):
        if key in fields and fields[key] is not None:
            setattr(asset, key, fields[key])
    db.commit()
    db.refresh(asset)
    return asset


def delete_asset(db: Session, asset: Asset) -> None:
    db.delete(asset)
    db.commit()


# --- Logs --------------------------------------------------------------------


def list_logs(db: Session, asset_id: int) -> list[AssetLog]:
    return list(
        db.scalars(
            select(AssetLog)
            .where(AssetLog.asset_id == asset_id)
            .order_by(AssetLog.log_date, AssetLog.id)
        ).all()
    )


def get_log(db: Session, log_id: int) -> AssetLog:
    log = db.get(AssetLog, log_id)
    if log is None:
        raise ValueError("Log not found")
    return log


def add_log(db: Session, asset_id: int, *, log_date: date, kind: str = "refuel",
            **fields) -> AssetLog:
    get_asset(db, asset_id)  # validate existence
    if kind not in LOG_KINDS:
        raise ValueError(f"kind must be one of {sorted(LOG_KINDS)}")

    def _money(value):
        return Decimal(value).quantize(TWO_DP) if value is not None else None

    def _dec(value):
        return Decimal(value) if value is not None else None

    log = AssetLog(
        asset_id=asset_id,
        log_date=log_date,
        kind=kind,
        note=(fields.get("note") or None),
        cost=_money(fields.get("cost")),
        odometer=_dec(fields.get("odometer")),
        litres=_dec(fields.get("litres")),
        is_full_tank=fields.get("is_full_tank", True) if kind == "refuel" else None,
        fuel_type=(fields.get("fuel_type") or None),
        meter=(fields.get("meter") or None),
        reading=_dec(fields.get("reading")),
        unit=(fields.get("unit") or None),
        transaction_id=fields.get("transaction_id"),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def delete_log(db: Session, log: AssetLog) -> None:
    db.delete(log)
    db.commit()


def log_to_dict(log: AssetLog) -> dict:
    return {
        "id": log.id,
        "asset_id": log.asset_id,
        "log_date": log.log_date.isoformat(),
        "kind": log.kind,
        "note": log.note,
        "cost": str(log.cost) if log.cost is not None else None,
        "odometer": str(log.odometer) if log.odometer is not None else None,
        "litres": str(log.litres) if log.litres is not None else None,
        "is_full_tank": log.is_full_tank,
        "fuel_type": log.fuel_type,
        "meter": log.meter,
        "reading": str(log.reading) if log.reading is not None else None,
        "unit": log.unit,
        "transaction_id": log.transaction_id,
    }


# --- Car consumption ---------------------------------------------------------


def _is_full(value: bool | None) -> bool:
    # NULL means "not recorded" → assume a full tank (the common case).
    return value is None or bool(value)


def car_stats(db: Session, asset: Asset, logs: list[AssetLog] | None = None) -> dict:
    """Consumption + cost stats for a car, computed tank-to-tank between full fills."""
    logs = logs if logs is not None else list_logs(db, asset.id)
    unit = asset.distance_unit or "mi"
    refuels = sorted(
        [lg for lg in logs if lg.kind == "refuel" and lg.odometer is not None and lg.litres is not None],
        key=lambda lg: (Decimal(lg.odometer), lg.id),
    )

    segments: list[dict] = []
    tot_km = Decimal("0")
    tot_litres = Decimal("0")
    for prev, cur in zip(refuels, refuels[1:], strict=False):
        if not (_is_full(prev.is_full_tank) and _is_full(cur.is_full_tank)):
            continue
        dist = Decimal(cur.odometer) - Decimal(prev.odometer)
        litres = Decimal(cur.litres)
        if dist <= 0 or litres <= 0:
            continue
        dist_km = dist if unit == "km" else dist * MI_PER_KM
        dist_mi = dist if unit == "mi" else dist / MI_PER_KM
        tot_km += dist_km
        tot_litres += litres
        segments.append({
            "date": cur.log_date.isoformat(),
            "from_odometer": str(prev.odometer),
            "to_odometer": str(cur.odometer),
            "distance": str(dist),
            "litres": str(litres),
            "l_per_100km": round(float(litres / dist_km * 100), 2),
            "mpg": round(float(dist_mi / (litres / IMPERIAL_GALLON_L)), 1),
            "cost": str(cur.cost) if cur.cost is not None else None,
        })

    avg_l_per_100km = round(float(tot_litres / tot_km * 100), 2) if tot_km > 0 else None
    avg_mpg = (
        round(float((tot_km / MI_PER_KM) / (tot_litres / IMPERIAL_GALLON_L)), 1)
        if tot_km > 0 and tot_litres > 0
        else None
    )
    fuel_cost = sum(
        (Decimal(lg.cost) for lg in logs if lg.kind == "refuel" and lg.cost is not None), Decimal("0")
    ).quantize(TWO_DP)
    last = segments[-1] if segments else None
    return {
        "distance_unit": unit,
        "refuel_count": len(refuels),
        "latest_odometer": str(refuels[-1].odometer) if refuels else None,
        "total_fuel_cost": str(fuel_cost),
        "total_litres": str(tot_litres) if tot_litres > 0 else "0",
        "avg_l_per_100km": avg_l_per_100km,
        "avg_mpg": avg_mpg,
        "last_l_per_100km": last["l_per_100km"] if last else None,
        "last_mpg": last["mpg"] if last else None,
        "segments": segments,
    }


# --- Home utility readings ---------------------------------------------------


def home_stats(db: Session, asset: Asset, logs: list[AssetLog] | None = None) -> dict:
    """Per-meter usage for a home, computed between consecutive readings of the
    same meter (electricity/gas/water…). A meter reset/rollover (usage < 0) is
    skipped rather than counted as huge negative consumption."""
    logs = logs if logs is not None else list_logs(db, asset.id)
    readings = [
        lg for lg in logs if lg.kind == "reading" and lg.meter and lg.reading is not None
    ]
    by_meter: dict[str, list[AssetLog]] = {}
    for lg in sorted(readings, key=lambda lg: (lg.log_date, lg.id)):
        by_meter.setdefault(lg.meter, []).append(lg)

    meters = []
    for meter, rs in by_meter.items():
        unit = next((r.unit for r in rs if r.unit), None)
        segments = []
        total_usage = Decimal("0")
        total_cost = Decimal("0")
        for prev, cur in zip(rs, rs[1:], strict=False):
            usage = Decimal(cur.reading) - Decimal(prev.reading)
            if usage < 0:  # meter reset / new meter — don't count
                continue
            days = (cur.log_date - prev.log_date).days
            total_usage += usage
            if cur.cost is not None:
                total_cost += Decimal(cur.cost)
            segments.append({
                "date": cur.log_date.isoformat(),
                "usage": str(usage),
                "days": days,
                "avg_per_day": round(float(usage / days), 3) if days > 0 else None,
                "cost": str(cur.cost) if cur.cost is not None else None,
            })
        meters.append({
            "meter": meter,
            "unit": unit,
            "latest_reading": str(rs[-1].reading),
            "reading_count": len(rs),
            "total_usage": str(total_usage),
            "total_cost": str(total_cost.quantize(TWO_DP)),
            "segments": segments,
        })
    return {"meters": meters}


def asset_to_dict(db: Session, asset: Asset, *, with_logs: bool = False) -> dict:
    logs = list_logs(db, asset.id)
    total_cost = sum(
        (Decimal(lg.cost) for lg in logs if lg.cost is not None), Decimal("0")
    ).quantize(TWO_DP)
    out: dict = {
        "id": asset.id,
        "name": asset.name,
        "kind": asset.kind,
        "identifier": asset.identifier,
        "distance_unit": asset.distance_unit,
        "is_active": asset.is_active,
        "log_count": len(logs),
        "total_cost": str(total_cost),
    }
    if asset.kind == "car":
        out["car"] = car_stats(db, asset, logs)
    if asset.kind == "home":
        out["home"] = home_stats(db, asset, logs)
    if with_logs:
        out["logs"] = [log_to_dict(lg) for lg in logs]
    return out


def summary(db: Session) -> dict:
    assets = [asset_to_dict(db, a) for a in list_assets(db)]
    total_cost = sum((Decimal(a["total_cost"]) for a in assets), Decimal("0")).quantize(TWO_DP)
    return {"assets": assets, "total_cost": str(total_cost), "count": len(assets)}
