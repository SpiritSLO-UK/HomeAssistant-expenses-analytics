"""Read Home Assistant entity states via the Supervisor core-API proxy.

Used only by the energy-cost offset (``energy_source == "ha_api"``). It is
**read-only and opt-in**: it needs the add-on's ``homeassistant_api: true`` grant
(the Supervisor then injects ``SUPERVISOR_TOKEN``) and only ever reads the
specific entity ids the user names — never writes, never enumerates. Best-effort:
returns ``{}`` when the token/feature is absent (standalone, or the HA API isn't
granted), so the feature degrades cleanly instead of erroring.

Entities are fetched **concurrently** (bounded thread pool) so N entities take
~one timeout rather than N×, and each fetch gets a small bounded retry so a
transient network blip doesn't silently drop an entity. Readings are normalised
to the expected energy unit (``kWh``) using each entity's
``unit_of_measurement`` attribute, so a sensor reporting ``Wh`` isn't treated as
``kWh`` (a 1000× error); unknown/absent units fall back to the raw value.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

import httpx

from app.logging import get_logger

logger = get_logger("app.ha")

# The Supervisor proxies the Home Assistant Core REST API at this host when the
# add-on declares `homeassistant_api: true`.
SUPERVISOR_CORE_API = "http://supervisor/core/api"

# Concurrency + transient-retry tuning. Backoff is intentionally tiny; tests set
# ``_RETRY_BACKOFF = 0`` (or monkeypatch ``time.sleep``) to stay fast.
_MAX_CONCURRENCY = 8
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = 0.25  # seconds, grows linearly per retry

# Factors that convert a reading *into* kWh. Used to reconcile the common
# Wh-vs-kWh (1000×) sensor mismatch. Keys are lower-cased units.
_ENERGY_UNIT_TO_KWH: dict[str, Decimal] = {
    "wh": Decimal("0.001"),
    "kwh": Decimal("1"),
    "mwh": Decimal("1000"),
    "gwh": Decimal("1000000"),
}


def _token() -> str | None:
    return os.environ.get("SUPERVISOR_TOKEN") or None


def available() -> bool:
    """True when running under the Supervisor with the HA API granted."""
    return _token() is not None


def _normalise(value: float, unit: str | None, expected_unit: str) -> float:
    """Scale ``value`` from ``unit`` to ``expected_unit`` when both are known
    energy units, else return it unchanged (conservative fallback)."""
    if not unit:
        return value
    factor = _ENERGY_UNIT_TO_KWH.get(unit.strip().lower())
    target = _ENERGY_UNIT_TO_KWH.get((expected_unit or "").strip().lower())
    if factor is None or target is None or factor == target:
        return value
    return float(Decimal(str(value)) * factor / target)


def _parse(resp: httpx.Response, eid: str, expected_unit: str) -> float | None:
    """Extract a numeric, unit-normalised state, or ``None`` if non-numeric."""
    try:
        data = resp.json()
        value = float(data.get("state"))
    except (ValueError, TypeError, KeyError) as exc:
        logger.debug("HA state %s non-numeric: %s", eid, exc)
        return None
    unit = (data.get("attributes") or {}).get("unit_of_measurement")
    return _normalise(value, unit, expected_unit)


def _is_transient(status: int) -> bool:
    """Server-side / rate-limit statuses worth a retry."""
    return status >= 500 or status in (408, 429)


def _fetch_state(
    cli: httpx.Client, eid: str, headers: dict[str, str], expected_unit: str
) -> float | None:
    """Fetch one entity's numeric state with a small bounded retry on transient
    errors. Returns ``None`` (and logs at debug) when unreadable."""
    url = f"{SUPERVISOR_CORE_API}/states/{eid}"
    reason = "no attempt"
    for attempt in range(_RETRY_ATTEMPTS):
        if attempt:
            time.sleep(_RETRY_BACKOFF * attempt)
        try:
            resp = cli.get(url, headers=headers)
        except httpx.HTTPError as exc:
            reason = f"error {exc}"
            continue
        if resp.status_code == 200:
            return _parse(resp, eid, expected_unit)
        reason = f"HTTP {resp.status_code}"
        if not _is_transient(resp.status_code):
            break
    logger.debug("HA state %s unreadable (%s)", eid, reason)
    return None


def read_states(
    entity_ids: list[str],
    *,
    client: httpx.Client | None = None,
    expected_unit: str = "kWh",
) -> dict[str, float]:
    """Return ``{entity_id: numeric_state}`` for each readable entity.

    Entities are fetched concurrently, each with a bounded retry. Readings are
    normalised to ``expected_unit`` (default ``kWh``) from the entity's
    ``unit_of_measurement``. Skips entities that are missing, unavailable, or
    non-numeric. ``client`` is injectable for tests.
    """
    token = _token()
    ids = [e for e in (entity_ids or []) if e]
    if not token or not ids:
        return {}

    own = client is None
    cli = client or httpx.Client(timeout=10.0)
    headers = {"Authorization": f"Bearer {token}"}
    out: dict[str, float] = {}
    try:
        workers = min(len(ids), _MAX_CONCURRENCY)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_fetch_state, cli, eid, headers, expected_unit): eid
                for eid in ids
            }
            for fut in as_completed(futures):
                value = fut.result()
                if value is not None:
                    out[futures[fut]] = value
    finally:
        if own:
            cli.close()
    return out
