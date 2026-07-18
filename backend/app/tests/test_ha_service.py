"""Home Assistant state reader: concurrent fetch, bounded transient retry, and
unit normalisation (Wh-vs-kWh). No real HA is touched — every test injects an
``httpx.MockTransport`` client and neutralises the retry backoff so it stays fast.
"""

from __future__ import annotations

import threading
import time

import httpx
import pytest

from app.services import ha_service


@pytest.fixture(autouse=True)
def _fast_and_authed(monkeypatch):
    """Grant a fake Supervisor token and remove retry sleeps for every test."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    monkeypatch.setattr(ha_service, "_RETRY_BACKOFF", 0)


def _entity_id(request: httpx.Request) -> str:
    return request.url.path.rsplit("/", 1)[-1]


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _state(state, *, unit=None) -> httpx.Response:
    attrs = {"unit_of_measurement": unit} if unit is not None else {}
    return httpx.Response(200, json={"state": state, "attributes": attrs})


# --- token / input gating ---------------------------------------------------


def test_noop_without_token(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert ha_service.available() is False
    assert ha_service.read_states(["sensor.solar"]) == {}


def test_empty_ids_returns_empty():
    assert ha_service.read_states([]) == {}
    assert ha_service.read_states(["", None]) == {}  # type: ignore[list-item]


# --- basic reads ------------------------------------------------------------


def test_reads_multiple_entities():
    def handler(request):
        return _state({"sensor.a": "10", "sensor.b": "20"}[_entity_id(request)])

    out = ha_service.read_states(["sensor.a", "sensor.b"], client=_client(handler))
    assert out == {"sensor.a": 10.0, "sensor.b": 20.0}


def test_non_numeric_and_missing_are_skipped():
    def handler(request):
        eid = _entity_id(request)
        if eid == "sensor.ok":
            return _state("5")
        if eid == "sensor.unavail":
            return _state("unavailable")
        return httpx.Response(404)

    out = ha_service.read_states(
        ["sensor.ok", "sensor.unavail", "sensor.missing"], client=_client(handler)
    )
    assert out == {"sensor.ok": 5.0}


# --- unit normalisation (Wh vs kWh 1000×) -----------------------------------


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("kWh", 1500.0),   # already kWh -> unchanged
        ("Wh", 1.5),       # 1500 Wh -> 1.5 kWh
        ("MWh", 1_500_000.0),
        (None, 1500.0),    # attribute absent -> raw value
        ("bananas", 1500.0),  # unknown unit -> raw value
    ],
)
def test_unit_normalised_to_kwh(unit, expected):
    def handler(request):
        return _state("1500", unit=unit)

    out = ha_service.read_states(["sensor.e"], client=_client(handler))
    assert out["sensor.e"] == pytest.approx(expected)


def test_expected_unit_override():
    def handler(request):
        return _state("2", unit="kWh")  # 2 kWh -> 2000 Wh

    out = ha_service.read_states(
        ["sensor.e"], client=_client(handler), expected_unit="Wh"
    )
    assert out["sensor.e"] == pytest.approx(2000.0)


# --- transient retry --------------------------------------------------------


def test_transient_status_then_success():
    calls: dict[str, int] = {}

    def handler(request):
        eid = _entity_id(request)
        calls[eid] = calls.get(eid, 0) + 1
        if calls[eid] < 3:
            return httpx.Response(503)
        return _state("7")

    out = ha_service.read_states(["sensor.flaky"], client=_client(handler))
    assert out == {"sensor.flaky": 7.0}
    assert calls["sensor.flaky"] == 3


def test_network_error_then_success():
    calls: dict[str, int] = {}

    def handler(request):
        eid = _entity_id(request)
        calls[eid] = calls.get(eid, 0) + 1
        if calls[eid] == 1:
            raise httpx.ConnectError("boom")
        return _state("9")

    out = ha_service.read_states(["sensor.net"], client=_client(handler))
    assert out == {"sensor.net": 9.0}
    assert calls["sensor.net"] == 2


def test_permanent_status_is_not_retried():
    calls: dict[str, int] = {}

    def handler(request):
        eid = _entity_id(request)
        calls[eid] = calls.get(eid, 0) + 1
        return httpx.Response(404)

    out = ha_service.read_states(["sensor.gone"], client=_client(handler))
    assert out == {}
    assert calls["sensor.gone"] == 1  # 404 -> no retry


def test_exhausted_retries_drop_entity():
    def handler(request):
        return httpx.Response(500)

    out = ha_service.read_states(["sensor.down"], client=_client(handler))
    assert out == {}


# --- concurrency ------------------------------------------------------------


def test_entities_fetched_concurrently():
    """N slow entities complete in ~one delay, not N×, proving parallelism."""
    delay = 0.15
    n = 5

    def handler(request):
        time.sleep(delay)
        return _state("1")

    ids = [f"sensor.{i}" for i in range(n)]
    start = time.monotonic()
    out = ha_service.read_states(ids, client=_client(handler))
    elapsed = time.monotonic() - start

    assert len(out) == n
    assert elapsed < delay * n  # serial would be >= n*delay


def test_concurrency_is_bounded():
    """Never exceed the worker cap even with many entities."""
    active = 0
    peak = 0
    lock = threading.Lock()

    def handler(request):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return _state("1")

    ids = [f"sensor.{i}" for i in range(ha_service._MAX_CONCURRENCY + 4)]
    ha_service.read_states(ids, client=_client(handler))
    assert peak <= ha_service._MAX_CONCURRENCY
