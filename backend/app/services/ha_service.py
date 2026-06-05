"""Read Home Assistant entity states via the Supervisor core-API proxy.

Used only by the energy-cost offset (``energy_source == "ha_api"``). It is
**read-only and opt-in**: it needs the add-on's ``homeassistant_api: true`` grant
(the Supervisor then injects ``SUPERVISOR_TOKEN``) and only ever reads the
specific entity ids the user names — never writes, never enumerates. Best-effort:
returns ``{}`` when the token/feature is absent (standalone, or the HA API isn't
granted), so the feature degrades cleanly instead of erroring.
"""

from __future__ import annotations

import os

import httpx

from app.logging import get_logger

logger = get_logger("app.ha")

# The Supervisor proxies the Home Assistant Core REST API at this host when the
# add-on declares `homeassistant_api: true`.
SUPERVISOR_CORE_API = "http://supervisor/core/api"


def _token() -> str | None:
    return os.environ.get("SUPERVISOR_TOKEN") or None


def available() -> bool:
    """True when running under the Supervisor with the HA API granted."""
    return _token() is not None


def read_states(entity_ids: list[str], *, client: httpx.Client | None = None) -> dict[str, float]:
    """Return ``{entity_id: numeric_state}`` for each readable entity.

    Skips entities that are missing, unavailable, or non-numeric (e.g. ``state``
    of ``"unavailable"``). ``client`` is injectable for tests.
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
        for eid in ids:
            try:
                resp = cli.get(f"{SUPERVISOR_CORE_API}/states/{eid}", headers=headers)
                if resp.status_code != 200:
                    logger.debug("HA state %s -> HTTP %s", eid, resp.status_code)
                    continue
                out[eid] = float(resp.json().get("state"))
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                logger.debug("HA state %s unreadable/non-numeric: %s", eid, exc)
                continue
    finally:
        if own:
            cli.close()
    return out
