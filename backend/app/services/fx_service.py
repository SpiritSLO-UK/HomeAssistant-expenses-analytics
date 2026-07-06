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
    base, quote = base.upper(), quote.upper()
    row = db.scalars(
        select(FxRate).where(
            FxRate.rate_date == on, FxRate.base == base, FxRate.quote == quote
        )
    ).first()
    return row.rate if row else None


def upsert_rate(db: Session, on: date, base: str, quote: str, rate: Decimal, source: str) -> FxRate:
    # Normalise the currency codes so every caller (parser, online fetch, manual)
    # keys the same row — otherwise a lowercase code makes a duplicate that
    # set_manual_rate's uppercase lookup would miss (SR-A6).
    base, quote = base.upper(), quote.upper()
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
    base, quote = base.upper(), quote.upper()
    row = db.scalars(
        select(FxRate).where(
            FxRate.rate_date == on, FxRate.base == base, FxRate.quote == quote
        )
    ).first()
    if row is None:
        row = FxRate(rate_date=on, base=base, quote=quote, rate=rate, source="manual")
        db.add(row)
    else:
        row.rate = rate  # an explicit manual entry may correct a value
        row.source = "manual"
    db.commit()
    db.refresh(row)
    return row


def fetch_frankfurter(on: date, base: str, quote: str) -> Decimal | None:
    """Return base-per-1-quote for ``on`` from Frankfurter, or None on failure.

    Network call — only used in ``frankfurter`` FX mode (opt-in). Thin wrapper
    over ``fetch_frankfurter_batch``; a backfill should call the batch helper
    directly so it issues one request per (date, base) rather than one per
    (date, quote).
    """
    return fetch_frankfurter_batch(on, base, [quote]).get(quote.upper())


def fetch_frankfurter_batch(on: date, base: str, quotes: list[str]) -> dict[str, Decimal]:
    """Fetch base-per-1-quote for several ``quotes`` on ``on`` in ONE request.

    Frankfurter's date endpoint takes ``base`` + a comma-joined ``symbols`` list
    and returns quote-per-1-base for each symbol; we invert each into the
    base-per-1-quote convention this module stores. This lets a backfill of N
    foreign currencies for one date cost a single HTTP call instead of N.

    A missing symbol (or a zero rate — guarded so we never divide by zero) is
    simply omitted from the result. Any network/parse failure returns an empty
    dict so the caller sees "no rates for this group" and can surface it, rather
    than crashing an import.

    Network call — only used in ``frankfurter`` FX mode (opt-in).
    """
    import httpx  # local import so the dependency is only needed when used

    base = base.upper()
    # De-dupe, drop same-as-base, preserve order for a stable request/log.
    wanted = [q for q in dict.fromkeys(q.upper() for q in quotes) if q != base]
    if not wanted:
        return {}
    url = f"{FRANKFURTER_BASE}/{on.isoformat()}"
    try:
        resp = httpx.get(url, params={"base": base, "symbols": ",".join(wanted)}, timeout=10.0)
        resp.raise_for_status()
        rates = resp.json().get("rates", {})
    except Exception as exc:  # network/parse errors must not break import
        logger.warning("Frankfurter batch lookup failed (%s->%s %s): %s", base, wanted, on, exc)
        return {}
    out: dict[str, Decimal] = {}
    for quote in wanted:
        value = rates.get(quote)  # quote-per-1-base
        if value is None:
            continue
        quote_per_base = Decimal(str(value))
        if quote_per_base == 0:  # guard divide-by-zero on a bad/zero rate
            continue
        out[quote] = Decimal(1) / quote_per_base  # -> base-per-1-quote
    return out


def get_rate(
    db: Session, on: date, quote: str, base: str, mode: str, allow_fetch: bool = True
) -> tuple[Decimal | None, str | None]:
    """Resolve base-per-1-quote: same currency -> 1, then cache, then a cached
    inverse (1/rate), then (if the mode allows) an online fetch. Returns
    (rate, source) or (None, None)."""
    if quote == base:
        return Decimal(1), "same"
    cached = get_cached_rate(db, on, base, quote)
    if cached is not None:
        return cached, "cache"
    # Derive from a cached inverse rather than fetching again: if we already hold
    # base-per-1-quote we can serve quote-per-1-base as 1/rate (guard zero).
    inverse = get_cached_rate(db, on, quote, base)
    if inverse is not None and inverse != 0:
        return Decimal(1) / inverse, "inverse"
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


def convert_amount(
    db: Session,
    amount: Decimal,
    currency: str,
    base: str,
    on: date,
    *,
    mode: str | None = None,
    allow_fetch: bool = False,
) -> Decimal | None:
    """Convert ``amount`` in ``currency`` to ``base`` using the rate for ``on``.

    Returns the converted amount, or ``None`` when no rate is available — the caller
    decides whether to skip it or surface it (mirrors a transaction's ``needs_rate``,
    so a mixed-currency total never silently adds unconverted figures 1:1). Uses
    cached rates only by default (``allow_fetch=False``), so it's safe to call from
    aggregate read paths without triggering a network fetch per row. Same-currency is
    a no-op, so single-currency households are unaffected.
    """
    currency = (currency or base).upper()
    base = base.upper()
    if currency == base:
        return amount
    if mode is None:
        from app.services import settings_service

        mode = settings_service.get_fx_mode(db)
    rate, _ = get_rate(db, on, currency, base, mode, allow_fetch)
    if rate is None:
        return None
    return (amount * rate).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _prefetch_rates(db: Session, base: str, needed: dict[date, set[str]]) -> int:
    """Batch-fetch every (date -> {quotes}) that isn't already cached, one HTTP
    call per date. Caches new rates (never overwriting an existing one) and
    returns the number of (date, quote) rates that FAILED to fetch, so a caller
    can log/surface a persistent Frankfurter outage instead of it being silently
    swallowed row-by-row.
    """
    fetch_failures = 0
    for on, quotes in needed.items():
        # Only ask for what we don't already hold directly or via a cached inverse.
        to_fetch = sorted(
            q for q in quotes
            if get_cached_rate(db, on, base, q) is None
            and get_cached_rate(db, on, q, base) is None
        )
        if not to_fetch:
            continue
        rates = fetch_frankfurter_batch(on, base, to_fetch)
        for quote in to_fetch:
            rate = rates.get(quote)
            if rate is None:
                fetch_failures += 1
                continue
            upsert_rate(db, on, base, quote, rate, "frankfurter")  # never overwrites
    # Flush so the just-cached rates are visible to the subsequent cache lookups
    # (the session runs with autoflush disabled).
    db.flush()
    return fetch_failures


def backfill_missing(db: Session, base: str, mode: str) -> dict:
    """Try to convert all transactions still missing a base amount.

    In Frankfurter mode this first batches the online lookups — grouping the
    pending rows by date and fetching all needed currencies for that date in a
    single request — so backfilling N foreign rows over K dates costs at most K
    HTTP calls, not N. Rows then convert against the freshly-cached rates with no
    further network calls. ``fetch_failures`` counts rates the online source
    could not supply, so a persistent outage is a visible number, not silence.
    """
    base = base.upper()
    pending = db.scalars(
        select(Transaction).where(
            (Transaction.needs_rate.is_(True)) | (Transaction.base_amount.is_(None))
        )
    ).all()

    fetch_failures = 0
    if mode == "frankfurter":
        needed: dict[date, set[str]] = {}
        for txn in pending:
            quote = (txn.currency or base).upper()
            if quote != base:
                needed.setdefault(txn.transaction_date, set()).add(quote)
        fetch_failures = _prefetch_rates(db, base, needed)

    # Rates are now cached (or derivable via inverse); convert without any
    # per-row network call.
    filled = 0
    for txn in pending:
        if convert_transaction(db, txn, base, mode, allow_fetch=False):
            filled += 1
    db.commit()
    return {
        "checked": len(pending),
        "filled": filled,
        "still_missing": len(pending) - filled,
        "fetch_failures": fetch_failures,
    }


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
