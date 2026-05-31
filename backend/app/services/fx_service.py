"""Foreign-exchange conversion (backlog #29).

Stores the original amount + currency on every transaction and converts to the
household base currency. Rates are cached per (date, base, quote) so we never
refetch and **never silently rewrite an existing rate** (the user's rule). FX
lookups are manual by default; an opt-in Frankfurter online mode can fill rates
automatically (free, no key, ECB historical data — frankfurter.dev).

A foreign-currency transaction with no rate yet gets ``needs_rate=True`` and is
left out of base-currency totals until a rate is supplied/backfilled.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models import FxRate, Transaction

logger = get_logger(__name__)

FRANKFURTER_BASE = "https://api.frankfurter.dev/v1"
_CENTS = Decimal("0.01")


# --- rate cache ---

def get_cached_rate(db: Session, on: date, base: str, quote: str) -> Decimal | None:
    row = db.scalars(
        select(FxRate).where(
            FxRate.rate_date == on, FxRate.base == base, FxRate.quote == quote
        )
    ).first()
    return row.rate if row else None


def upsert_rate(db: Session, on: date, base: str, quote: str, rate: Decimal, source: str) -> FxRate:
    row = db.scalars(
        select(FxRate).where(
            FxRate.rate_date == on, FxRate.base == base, FxRate.quote == quote
        )
    ).first()
    if row is None:
        row = FxRate(rate_date=on, base=base, quote=quote, rate=rate, source=source)
        db.add(row)
    else:
        # Never silently change an existing rate's value; only update source.
        row.source = source
    return row


def set_manual_rate(db: Session, on: date, base: str, quote: str, rate: Decimal) -> FxRate:
    row = db.scalars(
        select(FxRate).where(
            FxRate.rate_date == on, FxRate.base == base, FxRate.quote == quote
        )
    ).first()
    if row is None:
        row = FxRate(rate_date=on, base=base.upper(), quote=quote.upper(), rate=rate, source="manual")
        db.add(row)
    else:
        row.rate = rate  # an explicit manual entry may correct a value
        row.source = "manual"
    db.commit()
    db.refresh(row)
    return row


def fetch_frankfurter(on: date, base: str, quote: str) -> Decimal | None:
    """Return base-per-1-quote for ``on`` from Frankfurter, or None on failure.

    Network call — only used in ``frankfurter`` FX mode (opt-in).
    """
    import httpx  # local import so the dependency is only needed when used

    url = f"{FRANKFURTER_BASE}/{on.isoformat()}"
    try:
        resp = httpx.get(url, params={"base": quote, "symbols": base}, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        value = data.get("rates", {}).get(base)
        return Decimal(str(value)) if value is not None else None
    except Exception as exc:  # network/parse errors must not break import
        logger.warning("Frankfurter lookup failed (%s->%s %s): %s", quote, base, on, exc)
        return None


def get_rate(
    db: Session, on: date, quote: str, base: str, mode: str, allow_fetch: bool = True
) -> tuple[Decimal | None, str | None]:
    """Resolve base-per-1-quote: same currency -> 1, then cache, then (if the
    mode allows) an online fetch. Returns (rate, source) or (None, None)."""
    if quote == base:
        return Decimal(1), "same"
    cached = get_cached_rate(db, on, base, quote)
    if cached is not None:
        return cached, "cache"
    if mode == "frankfurter" and allow_fetch:
        fetched = fetch_frankfurter(on, base, quote)
        if fetched is not None:
            upsert_rate(db, on, base, quote, fetched, "frankfurter")
            return fetched, "frankfurter"
    return None, None


def convert_transaction(
    db: Session, txn: Transaction, base: str, mode: str, allow_fetch: bool = True
) -> bool:
    """Set base_amount/fx_rate/fx_source/needs_rate on a transaction.

    Returns True if a base amount was computed. Never recomputes a row that
    already has a base_amount (preserves existing converted history).
    """
    if txn.base_amount is not None and not txn.needs_rate:
        return True
    if txn.currency == base:
        txn.base_amount = txn.amount
        txn.fx_rate = Decimal(1)
        txn.fx_source = "same"
        txn.needs_rate = False
        return True
    rate, source = get_rate(db, txn.transaction_date, txn.currency, base, mode, allow_fetch)
    if rate is None:
        txn.base_amount = None
        txn.fx_rate = None
        txn.fx_source = None
        txn.needs_rate = True
        return False
    txn.base_amount = (txn.amount * rate).quantize(_CENTS, rounding=ROUND_HALF_UP)
    txn.fx_rate = rate
    txn.fx_source = source
    txn.needs_rate = False
    return True


def backfill_missing(db: Session, base: str, mode: str) -> dict:
    """Try to convert all transactions still missing a base amount."""
    pending = db.scalars(
        select(Transaction).where(
            (Transaction.needs_rate.is_(True)) | (Transaction.base_amount.is_(None))
        )
    ).all()
    filled = 0
    for txn in pending:
        if convert_transaction(db, txn, base, mode, allow_fetch=(mode == "frankfurter")):
            filled += 1
    db.commit()
    return {"checked": len(pending), "filled": filled, "still_missing": len(pending) - filled}


def recompute_all(db: Session, base: str, mode: str) -> dict:
    """Recompute every transaction's base amount (e.g. after a base-currency
    change). Clears existing conversions first, then converts against ``base``."""
    txns = db.scalars(select(Transaction)).all()
    for txn in txns:
        txn.base_amount = None
        txn.needs_rate = False
    for txn in txns:
        convert_transaction(db, txn, base, mode, allow_fetch=(mode == "frankfurter"))
    db.commit()
    missing = sum(1 for t in txns if t.needs_rate)
    return {"recomputed": len(txns), "missing_rate": missing}
