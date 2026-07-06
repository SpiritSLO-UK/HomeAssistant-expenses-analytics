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
    # Tank-to-tank economy is measured full-to-full, so a segment boundary
    # requires an *explicit* full-tank marker. An unrecorded flag (NULL) is
    # treated as NOT full: a partial fill with no flag must not anchor a
    # segment, or it would skew the economy figure.
    return bool(value)


def _refuel_segment(prev: AssetLog, cur: AssetLog, unit: str, imperial: bool) -> dict | None:
    """One tank-to-tank segment between two *full* fills, or None to skip it.

    Skips partial tanks at either end and non-positive distance/litres, exactly
    as the inline loop did."""
    if not (_is_full(prev.is_full_tank) and _is_full(cur.is_full_tank)):
        return None
    dist = Decimal(cur.odometer or 0) - Decimal(prev.odometer or 0)
    litres = Decimal(cur.litres or 0)
    if dist <= 0 or litres <= 0:
        return None
    dist_km = dist if unit == "km" else dist * MI_PER_KM
    dist_mi = dist if unit == "mi" else dist / MI_PER_KM
    seg_l_per_100km = round(float(litres / dist_km * 100), 2)
    seg_mpg = round(float(dist_mi / (litres / IMPERIAL_GALLON_L)), 1)
    return {
        "_dist_km": dist_km,  # accumulator inputs, stripped before the result
        "_litres": litres,
        "_cost": Decimal(cur.cost) if cur.cost is not None else Decimal("0"),
        "date": cur.log_date.isoformat(),
        "from_odometer": str(prev.odometer),
        "to_odometer": str(cur.odometer),
        "distance": str(dist),
        "litres": str(litres),
        "l_per_100km": seg_l_per_100km,
        "mpg": seg_mpg,
        # Single-system fields the UI displays (no mix):
        "economy": seg_mpg if imperial else seg_l_per_100km,
        "fuel": str((litres / IMPERIAL_GALLON_L).quantize(TWO_DP)) if imperial else str(litres),
        "cost": str(cur.cost) if cur.cost is not None else None,
    }


def _car_averages(tot_km: Decimal, tot_litres: Decimal) -> tuple[float | None, float | None]:
    """Average L/100km and MPG over all counted segments, or None when no distance."""
    avg_l_per_100km = round(float(tot_litres / tot_km * 100), 2) if tot_km > 0 else None
    avg_mpg = (
        round(float((tot_km / MI_PER_KM) / (tot_litres / IMPERIAL_GALLON_L)), 1)
        if tot_km > 0 and tot_litres > 0
        else None
    )
    return avg_l_per_100km, avg_mpg


def _car_result(
    *,
    unit: str,
    imperial: bool,
    refuels: list[AssetLog],
    segments: list[dict],
    tot_km: Decimal,
    tot_litres: Decimal,
    fuel_cost: Decimal,
    segment_fuel_cost: Decimal,
) -> dict:
    """Assemble the car-stats response dict from the accumulated totals/segments."""
    avg_l_per_100km, avg_mpg = _car_averages(tot_km, tot_litres)
    last = segments[-1] if segments else None
    litres_str = str(tot_litres) if tot_litres > 0 else "0"
    last_economy = None
    if last:
        last_economy = last["mpg"] if imperial else last["l_per_100km"]
    return {
        "distance_unit": unit,
        "system": "imperial" if imperial else "metric",
        "fuel_unit": "gal" if imperial else "L",
        "economy_unit": "MPG" if imperial else "L/100km",
        "refuel_count": len(refuels),
        "latest_odometer": str(refuels[-1].odometer) if refuels else None,
        # `total_fuel_cost` = money spent on ALL refuels (what the driver paid).
        # `segment_fuel_cost` = the portion of that spent on fills that anchor a
        # measured tank-to-tank segment, so it lines up with `total_litres` /
        # the economy figures. They differ when partial or unmeasured fills exist.
        "total_fuel_cost": str(fuel_cost),
        "segment_fuel_cost": str(segment_fuel_cost),
        "total_litres": litres_str,
        # Fuel total in the asset's own system (gallons for imperial).
        "total_fuel": str((tot_litres / IMPERIAL_GALLON_L).quantize(TWO_DP)) if imperial else litres_str,
        "avg_l_per_100km": avg_l_per_100km,
        "avg_mpg": avg_mpg,
        # Single-system economy the UI shows (no mix):
        "avg_economy": avg_mpg if imperial else avg_l_per_100km,
        "last_economy": last_economy,
        "last_l_per_100km": last["l_per_100km"] if last else None,
        "last_mpg": last["mpg"] if last else None,
        "segments": segments,
    }


def car_stats(db: Session, asset: Asset, logs: list[AssetLog] | None = None) -> dict:
    """Consumption + cost stats for a car, computed tank-to-tank between full fills."""
    logs = logs if logs is not None else list_logs(db, asset.id)
    unit = asset.distance_unit or "mi"
    # One consistent system, never a mix: imperial = miles + gallons + MPG;
    # metric = km + litres + L/100km. Driven by the asset's distance unit.
    imperial = unit == "mi"
    # Segment tank-to-tank in *chronological* order. Sorting by odometer would
    # let a mistyped reading or a rollover mis-segment the intervals; date
    # follows how the fills actually happened. `id` is a stable tie-break for
    # two fills on the same day. A genuinely decreasing odometer between two
    # chronological fills yields a non-positive distance, which
    # `_refuel_segment` skips (rather than emitting a bogus negative segment).
    refuels = sorted(
        [lg for lg in logs if lg.kind == "refuel" and lg.odometer is not None and lg.litres is not None],
        key=lambda lg: (lg.log_date, lg.id),
    )

    segments: list[dict] = []
    tot_km = Decimal("0")
    tot_litres = Decimal("0")
    segment_cost = Decimal("0")
    for prev, cur in zip(refuels, refuels[1:], strict=False):
        seg = _refuel_segment(prev, cur, unit, imperial)
        if seg is None:
            continue
        tot_km += seg.pop("_dist_km")
        tot_litres += seg.pop("_litres")
        segment_cost += seg.pop("_cost")
        segments.append(seg)

    fuel_cost = sum(
        (Decimal(lg.cost) for lg in logs if lg.kind == "refuel" and lg.cost is not None), Decimal("0")
    ).quantize(TWO_DP)
    return _car_result(
        unit=unit,
        imperial=imperial,
        refuels=refuels,
        segments=segments,
        tot_km=tot_km,
        tot_litres=tot_litres,
        fuel_cost=fuel_cost,
        segment_fuel_cost=segment_cost.quantize(TWO_DP),
    )


# --- Home utility readings ---------------------------------------------------


def _reading_segment(prev: AssetLog, cur: AssetLog) -> dict | None:
    """One usage segment between consecutive readings of a meter, or None for a
    meter reset/rollover (usage < 0), which must not be counted."""
    usage = Decimal(cur.reading or 0) - Decimal(prev.reading or 0)
    if usage < 0:  # meter reset / new meter — don't count
        return None
    days = (cur.log_date - prev.log_date).days
    return {
        "_usage": usage,  # accumulator inputs, stripped before the result
        "date": cur.log_date.isoformat(),
        "usage": str(usage),
        "days": days,
        "avg_per_day": round(float(usage / days), 3) if days > 0 else None,
        "cost": str(cur.cost) if cur.cost is not None else None,
    }


def _meter_stats(meter: str, rs: list[AssetLog]) -> dict:
    """Aggregate one meter's consecutive readings into a usage/cost summary."""
    unit = next((r.unit for r in rs if r.unit), None)
    segments = []
    total_usage = Decimal("0")
    total_cost = Decimal("0")
    for prev, cur in zip(rs, rs[1:], strict=False):
        seg = _reading_segment(prev, cur)
        if seg is None:
            continue
        total_usage += seg.pop("_usage")
        if cur.cost is not None:
            total_cost += Decimal(cur.cost)
        segments.append(seg)
    return {
        "meter": meter,
        "unit": unit,
        "latest_reading": str(rs[-1].reading),
        "reading_count": len(rs),
        "total_usage": str(total_usage),
        "total_cost": str(total_cost.quantize(TWO_DP)),
        "segments": segments,
    }


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
        if not lg.meter:  # filtered above; guard narrows str | None -> str
            continue
        by_meter.setdefault(lg.meter, []).append(lg)

    meters = [_meter_stats(meter, rs) for meter, rs in by_meter.items()]
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
