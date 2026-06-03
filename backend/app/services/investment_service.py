"""Investment & pension accounts: value snapshots and holdings (spec §12.4, §27).

Investment and pension accounts are regular :class:`Account` rows with
``account_type in {"investment", "pension"}``, scoped exactly like every other
account (shared vs private; #66/#82). All money is ``Decimal`` in the base
currency — a mixed-currency total would need FX conversion (out of scope here,
noted for later, mirroring savings).

An account's worth comes from holdings when it has any (market value =
Σ units × last_price), otherwise from its latest value snapshot. Unrealised gain
is reported only where a cost basis (avg_cost) is known.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings as env_settings
from app.logging import get_logger
from app.models import Account, AccountValue, Holding, HoldingPrice
from app.services import price_service, settings_service
from app.services.household_service import get_or_create_default_household

logger = get_logger(__name__)

INVESTMENT_TYPES = {"investment", "pension"}
TWO_DP = Decimal("0.01")
SIX_DP = Decimal("0.000001")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- Accounts ----------------------------------------------------------------


def list_accounts(db: Session, *, account_ids: set[int] | None = None) -> list[Account]:
    stmt = select(Account).where(
        Account.account_type.in_(INVESTMENT_TYPES), Account.is_active.is_(True)
    )
    if account_ids is not None:  # visibility scope (shared vs private; #66/#82)
        stmt = stmt.where(Account.id.in_(account_ids))
    return list(db.scalars(stmt.order_by(Account.name)).all())


def create_account(db: Session, *, name: str, institution: str | None = None,
                   currency: str | None = None, account_type: str = "investment") -> Account:
    if account_type not in INVESTMENT_TYPES:
        raise ValueError(f"account_type must be one of {sorted(INVESTMENT_TYPES)}")
    household = get_or_create_default_household(db)
    account = Account(
        household_id=household.id,
        name=name.strip(),
        institution=(institution or None),
        account_type=account_type,
        currency=(currency or settings_service.get_base_currency(db)).upper(),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def get_investment_account(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None or account.account_type not in INVESTMENT_TYPES:
        raise ValueError("Not an investment or pension account")
    return account


# --- Value snapshots ---------------------------------------------------------


def record_value(db: Session, account_id: int, *, as_of: date, value: Decimal,
                 note: str | None = None) -> AccountValue:
    account = get_investment_account(db, account_id)
    row = AccountValue(
        account_id=account.id,
        as_of_date=as_of,
        value=Decimal(value).quantize(TWO_DP),
        currency=account.currency,
        note=(note or None),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def adjust_value(db: Session, account_id: int, *, delta: Decimal,
                 note: str | None = None) -> AccountValue:
    """Record a contribution (positive delta) or withdrawal (negative) as a new
    snapshot for today at ``latest + delta`` — the +/- control."""
    current = latest_value(db, account_id) or Decimal("0")
    new_value = (current + Decimal(delta)).quantize(TWO_DP)
    return record_value(db, account_id, as_of=date.today(), value=new_value, note=note)


def value_history(db: Session, account_id: int) -> list[AccountValue]:
    return list(
        db.scalars(
            select(AccountValue)
            .where(AccountValue.account_id == account_id)
            .order_by(AccountValue.as_of_date, AccountValue.id)
        ).all()
    )


def latest_value(db: Session, account_id: int) -> Decimal | None:
    row = db.scalars(
        select(AccountValue)
        .where(AccountValue.account_id == account_id)
        .order_by(AccountValue.as_of_date.desc(), AccountValue.id.desc())
        .limit(1)
    ).first()
    return Decimal(row.value) if row else None


# --- Holdings ----------------------------------------------------------------


def list_holdings(db: Session, account_id: int) -> list[Holding]:
    return list(
        db.scalars(
            select(Holding).where(Holding.account_id == account_id).order_by(Holding.symbol)
        ).all()
    )


def get_holding(db: Session, holding_id: int) -> Holding:
    holding = db.get(Holding, holding_id)
    if holding is None:
        raise ValueError("Holding not found")
    return holding


def _record_holding_price(db: Session, holding_id: int, price: Decimal) -> None:
    """Record/refresh today's price point for a holding (one row per holding+date),
    so the portfolio value can be charted over time."""
    today = date.today()
    row = db.scalars(
        select(HoldingPrice).where(
            HoldingPrice.holding_id == holding_id, HoldingPrice.as_of_date == today
        )
    ).first()
    if row is None:
        db.add(HoldingPrice(holding_id=holding_id, as_of_date=today, price=Decimal(price)))
    else:
        row.price = Decimal(price)
    db.commit()


def create_holding(db: Session, account_id: int, *, symbol: str, units: Decimal,
                   name: str | None = None, avg_cost: Decimal | None = None,
                   last_price: Decimal | None = None) -> Holding:
    account = get_investment_account(db, account_id)
    holding = Holding(
        account_id=account.id,
        symbol=symbol.strip().upper(),
        name=(name or None),
        units=Decimal(units).quantize(SIX_DP),
        avg_cost=Decimal(avg_cost).quantize(SIX_DP) if avg_cost is not None else None,
        last_price=Decimal(last_price).quantize(SIX_DP) if last_price is not None else None,
        last_price_at=_now() if last_price is not None else None,
        currency=account.currency,
    )
    db.add(holding)
    db.commit()
    db.refresh(holding)
    if holding.last_price is not None:
        _record_holding_price(db, holding.id, holding.last_price)
    return holding


def update_holding(db: Session, holding: Holding, **fields) -> Holding:
    if fields.get("symbol") is not None:
        holding.symbol = fields["symbol"].strip().upper()
    if "name" in fields:
        holding.name = (fields["name"] or None)
    if fields.get("units") is not None:
        holding.units = Decimal(fields["units"]).quantize(SIX_DP)
    if "avg_cost" in fields:
        holding.avg_cost = (
            Decimal(fields["avg_cost"]).quantize(SIX_DP) if fields["avg_cost"] is not None else None
        )
    price_changed = False
    if "last_price" in fields:
        if fields["last_price"] is not None:
            holding.last_price = Decimal(fields["last_price"]).quantize(SIX_DP)
            holding.last_price_at = _now()
            price_changed = True
        else:
            holding.last_price = None
            holding.last_price_at = None
    db.commit()
    db.refresh(holding)
    if price_changed and holding.last_price is not None:
        _record_holding_price(db, holding.id, holding.last_price)
    return holding


def delete_holding(db: Session, holding: Holding) -> None:
    db.delete(holding)
    db.commit()


# --- Derived figures ---------------------------------------------------------


def _gain_pct(gain: Decimal | None, cost: Decimal | None) -> float | None:
    if gain is None or cost is None or cost <= 0:
        return None
    return round(float(gain / cost * 100), 1)


def holding_to_dict(db: Session, holding: Holding) -> dict:
    units = Decimal(holding.units)
    price = Decimal(holding.last_price) if holding.last_price is not None else None
    avg = Decimal(holding.avg_cost) if holding.avg_cost is not None else None
    market_value = (units * price).quantize(TWO_DP) if price is not None else None
    cost_basis = (units * avg).quantize(TWO_DP) if avg is not None else None
    gain = (
        market_value - cost_basis if market_value is not None and cost_basis is not None else None
    )
    return {
        "id": holding.id,
        "account_id": holding.account_id,
        "symbol": holding.symbol,
        "name": holding.name,
        "units": str(units),
        "avg_cost": str(avg) if avg is not None else None,
        "last_price": str(price) if price is not None else None,
        "last_price_at": holding.last_price_at.isoformat() if holding.last_price_at else None,
        "currency": holding.currency,
        "market_value": str(market_value) if market_value is not None else None,
        "cost_basis": str(cost_basis) if cost_basis is not None else None,
        "gain": str(gain) if gain is not None else None,
        "gain_pct": _gain_pct(gain, cost_basis),
    }


def account_to_dict(db: Session, account: Account) -> dict:
    holdings = list_holdings(db, account.id)
    values = value_history(db, account.id)
    has_holdings = len(holdings) > 0

    if has_holdings:
        priced = [h for h in holdings if h.last_price is not None]
        current_value = (
            sum((Decimal(h.units) * Decimal(h.last_price) for h in priced), Decimal("0")).quantize(TWO_DP)
            if priced
            else None
        )
        with_cost = [h for h in holdings if h.avg_cost is not None]
        cost_basis = (
            sum((Decimal(h.units) * Decimal(h.avg_cost) for h in with_cost), Decimal("0")).quantize(TWO_DP)
            if with_cost
            else None
        )
    else:
        current_value = latest_value(db, account.id)
        cost_basis = None

    gain = (
        current_value - cost_basis if current_value is not None and cost_basis is not None else None
    )
    return {
        "id": account.id,
        "name": account.name,
        "institution": account.institution,
        "currency": account.currency,
        "account_type": account.account_type,
        "current_value": str(current_value) if current_value is not None else None,
        "cost_basis": str(cost_basis) if cost_basis is not None else None,
        "gain": str(gain) if gain is not None else None,
        "gain_pct": _gain_pct(gain, cost_basis),
        "has_holdings": has_holdings,
        "holdings_count": len(holdings),
        "value_count": len(values),
    }


def summary(db: Session, *, account_ids: set[int] | None = None) -> dict:
    accounts = [account_to_dict(db, a) for a in list_accounts(db, account_ids=account_ids)]
    total_value = sum(
        (Decimal(a["current_value"]) for a in accounts if a["current_value"] is not None),
        Decimal("0"),
    ).quantize(TWO_DP)
    total_cost = sum(
        (Decimal(a["cost_basis"]) for a in accounts if a["cost_basis"] is not None), Decimal("0")
    ).quantize(TWO_DP)
    total_gain = sum(
        (Decimal(a["gain"]) for a in accounts if a["gain"] is not None), Decimal("0")
    ).quantize(TWO_DP)
    has_cost = any(a["cost_basis"] is not None for a in accounts)

    by_type = {"investment": Decimal("0"), "pension": Decimal("0")}
    for a in accounts:
        if a["current_value"] is not None:
            by_type[a["account_type"]] += Decimal(a["current_value"])

    return {
        "currency": settings_service.get_base_currency(db),
        "total_value": str(total_value),
        "total_cost": str(total_cost) if has_cost else None,
        "total_gain": str(total_gain) if has_cost else None,
        "total_gain_pct": _gain_pct(total_gain, total_cost) if has_cost else None,
        "by_type": {k: str(v.quantize(TWO_DP)) for k, v in by_type.items()},
        "accounts": accounts,
    }


# --- Value history & period changes (charts) --------------------------------


def _holding_price_as_of(db: Session, holding_id: int, on: date) -> Decimal | None:
    row = db.scalars(
        select(HoldingPrice)
        .where(HoldingPrice.holding_id == holding_id, HoldingPrice.as_of_date <= on)
        .order_by(HoldingPrice.as_of_date.desc(), HoldingPrice.id.desc())
        .limit(1)
    ).first()
    return Decimal(row.price) if row else None


def _account_value_as_of(db: Session, account: Account, on: date) -> Decimal | None:
    """An account's value on a date: holdings (Σ units × price-as-of) when it has
    any, else its latest value snapshot on/before that date. None when unknown."""
    holdings = list_holdings(db, account.id)
    if holdings:
        total = Decimal("0")
        priced = False
        for h in holdings:
            price = _holding_price_as_of(db, h.id, on)
            if price is not None:
                total += Decimal(h.units) * price
                priced = True
        return total.quantize(TWO_DP) if priced else None
    row = db.scalars(
        select(AccountValue)
        .where(AccountValue.account_id == account.id, AccountValue.as_of_date <= on)
        .order_by(AccountValue.as_of_date.desc(), AccountValue.id.desc())
        .limit(1)
    ).first()
    return Decimal(row.value) if row else None


def total_value_as_of(db: Session, accounts: list[Account], on: date) -> Decimal:
    total = Decimal("0")
    for account in accounts:
        value = _account_value_as_of(db, account, on)
        if value is not None:
            total += value
    return total.quantize(TWO_DP)


def _change(current: Decimal, prev: Decimal) -> dict:
    change = (current - prev).quantize(TWO_DP)
    pct = round(float(change / prev * 100), 1) if prev > 0 else None
    return {"change": str(change), "pct": pct}


def history(db: Session, *, account_ids: set[int] | None = None, days: int = 365) -> dict:
    """Portfolio value over time (points at snapshot/price dates) + day/month/year
    change. Reconstructed from value snapshots + holding price history, so changes
    reflect recorded history (a holding only contributes once it has a price point)."""
    accounts = list_accounts(db, account_ids=account_ids)
    today = date.today()
    start = today - timedelta(days=max(1, days))
    acct_ids = [a.id for a in accounts]

    dates: set[date] = {today}
    if acct_ids:
        for d in db.scalars(
            select(AccountValue.as_of_date).where(
                AccountValue.account_id.in_(acct_ids), AccountValue.as_of_date >= start
            )
        ).all():
            dates.add(d)
        for d in db.scalars(
            select(HoldingPrice.as_of_date)
            .join(Holding, Holding.id == HoldingPrice.holding_id)
            .where(Holding.account_id.in_(acct_ids), HoldingPrice.as_of_date >= start)
        ).all():
            dates.add(d)

    ordered = sorted(d for d in dates if d <= today)
    points = [
        {"date": d.isoformat(), "value": str(total_value_as_of(db, accounts, d))}
        for d in ordered
    ]
    current = total_value_as_of(db, accounts, today)
    return {
        "currency": settings_service.get_base_currency(db),
        "total_value": str(current),
        "points": points,
        "change_day": _change(current, total_value_as_of(db, accounts, today - timedelta(days=1))),
        "change_month": _change(current, total_value_as_of(db, accounts, today - timedelta(days=30))),
        "change_year": _change(current, total_value_as_of(db, accounts, today - timedelta(days=365))),
    }


# --- Price feed (optional; spec §27) -----------------------------------------


def price_status(db: Session) -> dict:
    """The configured price feed + whether a sync can actually run."""
    source = settings_service.get_investment_price_source(db)
    api_key = env_settings.investment_api_key
    return {
        "source": source,
        "api_key_present": bool(api_key),
        "ready": price_service.source_ready(source, api_key),
    }


def sync_prices(db: Session, *, account_ids: set[int] | None = None,
                source: str | None = None, api_key: str | None = None) -> dict:
    """Fetch the latest quote for every holding (in scope) and update its
    ``last_price``. A no-op when the source is ``manual`` / not configured. Each
    holding is independent — one failed lookup leaves that holding's price as-is."""
    source = source or settings_service.get_investment_price_source(db)
    api_key = api_key if api_key is not None else env_settings.investment_api_key
    result = {"source": source, "ran": False, "updated": 0, "failed": 0, "total": 0}
    if not price_service.source_ready(source, api_key):
        return result  # manual / unconfigured → nothing leaves the box
    result["ran"] = True
    for account in list_accounts(db, account_ids=account_ids):
        for holding in list_holdings(db, account.id):
            result["total"] += 1
            price = price_service.fetch_quote(holding.symbol, source, api_key)
            if price is None:
                result["failed"] += 1
                continue
            update_holding(db, holding, last_price=price)
            result["updated"] += 1
    return result


def sync_prices_safe(db: Session) -> dict:
    """Startup wrapper — acts only if a price source is configured; never raises."""
    try:
        return sync_prices(db)
    except Exception:  # pragma: no cover - a price sweep must never break startup
        logger.warning("Investment price sync failed", exc_info=True)
        return {"source": "error", "ran": False, "updated": 0, "failed": 0, "total": 0}
