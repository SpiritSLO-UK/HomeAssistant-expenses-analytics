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

import csv
import time
from decimal import Decimal, InvalidOperation

from app.logging import get_logger

logger = get_logger(__name__)

STOOQ_URL = "https://stooq.com/q/l/"
ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"
_TIMEOUT = 10.0

# Bounded retry for transient upstream blips (connect/read timeouts, 429/5xx),
# mirroring the AI-provider policy (#356): small and capped so a flaky moment
# recovers without hammering the feed or stalling the sync. Non-transient errors
# (4xx, parse) fail fast and the fetch just returns None (existing contract).
_RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 0.5

# Alpha Vantage signals throttling with an HTTP-200 body carrying one of these
# keys (a human-readable message) instead of a quote — must be treated as a
# rate-limit / no-quote, never as a silent empty price.
_AV_RATE_LIMIT_KEYS = ("Note", "Information")


class _TransientPriceError(Exception):
    """A retryable price-fetch failure (timeout/connect drop or 429/5xx).
    Internal — the public fetchers only ever return None once retries run out."""


def _get_once(url: str, params: dict):
    """One HTTP GET. Returns the response; raises ``_TransientPriceError`` for
    retryable failures and lets a permanent HTTP error surface via
    ``raise_for_status`` (an ``httpx.HTTPError`` the retry loop turns into None)."""
    import httpx

    try:
        resp = httpx.get(url, params=params, timeout=_TIMEOUT)
    except httpx.TransportError as exc:  # timeouts, connect/read drops
        raise _TransientPriceError(str(exc)) from exc
    if resp.status_code in _RETRY_STATUS:
        raise _TransientPriceError(f"HTTP {resp.status_code}")
    resp.raise_for_status()
    return resp


def _fetch_with_retry(url: str, params: dict, label: str):
    """GET ``url`` with a small bounded retry on transient errors. Returns the
    response, or None once retries are exhausted / a permanent error occurs."""
    import httpx

    last: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return _get_once(url, params)
        except _TransientPriceError as exc:
            last = exc
            if attempt + 1 < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_BASE * (2**attempt))
        except httpx.HTTPError as exc:  # permanent (4xx, redirect loop, …)
            logger.warning("%s quote failed: %s", label, exc)
            return None
    logger.warning("%s quote failed after %d attempts: %s", label, _MAX_ATTEMPTS, last)
    return None


def _av_rate_limited(data: object) -> bool:
    """True when an Alpha Vantage body is a throttle notice, not a quote."""
    return isinstance(data, dict) and any(data.get(k) for k in _AV_RATE_LIMIT_KEYS)


def _to_decimal(value: object) -> Decimal | None:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    # A non-positive quote (0 / negative) is a delisting or bad row, never a real
    # price — treat it as "no quote" so we don't persist a 0 that wrecks valuation.
    return price if price > 0 else None


def _parse_stooq_close(text: str) -> Decimal | None:
    """Extract the Close price from Stooq's light-quote CSV by *header name*.

    Parsing by header (not a fixed column index) means a reordered/extra column
    or an error page can't silently read the wrong field. Raises ``ValueError``
    when the expected ``Close`` column is absent (e.g. an HTML error response);
    the caller logs it and returns None.
    """
    rows = list(csv.reader(text.strip().splitlines()))
    if len(rows) < 2:  # header only ⇒ unknown symbol
        return None
    header = [h.strip().lower() for h in rows[0]]
    try:
        close_idx = header.index("close")
    except ValueError as exc:
        raise ValueError(f"Stooq CSV missing 'Close' column (header={rows[0]})") from exc
    data = rows[1]
    if close_idx >= len(data):
        raise ValueError(f"Stooq CSV row shorter than header (row={data})")
    raw = data[close_idx].strip()
    if raw in ("N/D", ""):  # Stooq's no-data marker for an unknown/stale symbol
        return None
    return _to_decimal(raw)  # non-positive / non-numeric ⇒ None


def fetch_stooq(symbol: str) -> Decimal | None:
    """Latest close from Stooq's light-quote CSV (keyless), or None on failure.
    Retries transient network blips (timeout/connect drop, 429/5xx)."""
    resp = _fetch_with_retry(
        STOOQ_URL,
        {"s": symbol.lower(), "f": "sd2t2ohlcv", "h": "", "e": "csv"},
        f"Stooq ({symbol})",
    )
    if resp is None:
        return None
    try:
        return _parse_stooq_close(resp.text)
    except ValueError as exc:  # missing Close column / short row — never break caller
        logger.warning("Stooq quote failed for %s: %s", symbol, exc)
        return None


def fetch_alphavantage(symbol: str, api_key: str) -> Decimal | None:
    """Latest price from Alpha Vantage GLOBAL_QUOTE (keyed), or None on failure.
    Retries transient network blips; an HTTP-200 rate-limit body (``Note`` /
    ``Information``) is treated as a no-quote, not a silent zero."""
    resp = _fetch_with_retry(
        ALPHAVANTAGE_URL,
        {"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": api_key},
        f"Alpha Vantage ({symbol})",
    )
    if resp is None:
        return None
    try:
        data = resp.json()
    except ValueError as exc:  # non-JSON body (error page, etc.)
        logger.warning("Alpha Vantage quote failed for %s: %s", symbol, exc)
        return None
    if _av_rate_limited(data):
        logger.warning("Alpha Vantage rate-limited for %s (no quote returned)", symbol)
        return None
    quote = (data.get("Global Quote") if isinstance(data, dict) else None) or {}
    return _to_decimal(quote.get("05. price"))


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
