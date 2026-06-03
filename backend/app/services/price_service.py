"""Investment price feed (spec §27; backlog stretch: investments daily price check).

Fetches the latest quote for a holding's ticker from a pluggable source so the
portfolio's market value + gain stay current. **Off by default** (``manual``):
nothing leaves the box. Only the **ticker symbol** is ever sent — never balances,
holdings sizes or any personal data — so a price fetch is privacy-safe.

Sources:
- ``manual`` — no network; the user types prices in (default).
- ``stooq`` — free, **no API key** (Stooq light-quote CSV). Expects exchange-
  suffixed symbols, e.g. ``aapl.us``, ``vwrl.uk``.
- ``alphavantage`` — a keyed provider (Alpha Vantage ``GLOBAL_QUOTE``). Needs
  ``HAFI_INVESTMENT_API_KEY``; absent ⇒ behaves like ``manual``. It's deliberately
  structured as a provider so a real broker API (Trading 212 / Freetrade) can
  slot in behind the same interface later.

Every fetch mirrors the FX pattern: ``httpx`` with a short timeout, broad
exception handling, and **never raise into the caller** — a price lookup that
fails just leaves the existing (manual) price untouched.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.logging import get_logger

logger = get_logger(__name__)

STOOQ_URL = "https://stooq.com/q/l/"
ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"
_TIMEOUT = 10.0


def _to_decimal(value: object) -> Decimal | None:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return price if price > 0 else None


def fetch_stooq(symbol: str) -> Decimal | None:
    """Latest close from Stooq's light-quote CSV (keyless), or None on failure."""
    import httpx  # local import so the dependency is only needed when used

    try:
        resp = httpx.get(
            STOOQ_URL,
            params={"s": symbol.lower(), "f": "sd2t2ohlcv", "h": "", "e": "csv"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        lines = [ln for ln in resp.text.strip().splitlines() if ln]
        if len(lines) < 2:  # header only ⇒ unknown symbol
            return None
        # Columns: Symbol,Date,Time,Open,High,Low,Close,Volume — Close is index 6.
        cols = lines[1].split(",")
        if len(cols) < 7 or cols[6] in ("N/D", ""):
            return None
        return _to_decimal(cols[6])
    except Exception as exc:  # network/parse errors must never break the caller
        logger.warning("Stooq quote failed for %s: %s", symbol, exc)
        return None


def fetch_alphavantage(symbol: str, api_key: str) -> Decimal | None:
    """Latest price from Alpha Vantage GLOBAL_QUOTE (keyed), or None on failure."""
    import httpx

    try:
        resp = httpx.get(
            ALPHAVANTAGE_URL,
            params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": api_key},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        quote = resp.json().get("Global Quote") or {}
        return _to_decimal(quote.get("05. price"))
    except Exception as exc:
        logger.warning("Alpha Vantage quote failed for %s: %s", symbol, exc)
        return None


def source_ready(source: str, api_key: str | None) -> bool:
    """Whether a sync can run for this source (``manual`` never; a keyed source
    needs its key)."""
    if source == "stooq":
        return True
    if source == "alphavantage":
        return bool(api_key)
    return False


def fetch_quote(symbol: str, source: str, api_key: str | None = None) -> Decimal | None:
    """Latest price-per-unit for ``symbol`` from ``source``, or None on any failure
    (including ``manual``/unknown sources, which never fetch)."""
    if not symbol:
        return None
    if source == "stooq":
        return fetch_stooq(symbol)
    if source == "alphavantage":
        return fetch_alphavantage(symbol, api_key) if api_key else None
    return None
