"""Investment price feed: source config, sync, status (spec §27).

The actual HTTP providers (Stooq / Alpha Vantage) are never hit — the sync is
exercised by monkeypatching ``price_service.fetch_quote`` so tests stay offline.
"""

from __future__ import annotations

from decimal import Decimal

from app.services import price_service


def _investment(client) -> int:
    return client.post(
        "/api/investments/accounts", json={"name": "ISA", "account_type": "investment"}
    ).json()["id"]


def _holding(client, account_id, symbol="AAPL.US", units="10", avg_cost="100"):
    return client.post(
        f"/api/investments/accounts/{account_id}/holdings",
        json={"symbol": symbol, "units": units, "avg_cost": avg_cost},
    ).json()


def test_price_status_default_manual(client):
    s = client.get("/api/investments/price-status").json()
    assert s["source"] == "manual"
    assert s["ready"] is False


def test_set_price_source_validated(client):
    ok = client.put("/api/settings", json={"investment_price_source": "stooq"})
    assert ok.status_code == 200
    assert ok.json()["investment_price_source"] == "stooq"
    assert client.get("/api/investments/price-status").json() == {
        "source": "stooq", "api_key_present": False, "ready": True
    }
    # An unknown source is rejected.
    bad = client.put("/api/settings", json={"investment_price_source": "magic"})
    assert bad.status_code == 400


def test_sync_is_noop_when_manual(client):
    aid = _investment(client)
    _holding(client, aid)
    r = client.post("/api/investments/sync-prices").json()
    assert r["ran"] is False
    assert r["updated"] == 0


def test_sync_updates_holding_prices(client, monkeypatch):
    aid = _investment(client)
    h = _holding(client, aid, symbol="AAPL.US", units="10", avg_cost="100")
    assert h["last_price"] is None

    client.put("/api/settings", json={"investment_price_source": "stooq"})
    # Offline: pretend the provider returns a fixed quote.
    monkeypatch.setattr(price_service, "fetch_quote", lambda symbol, source, api_key=None: Decimal("130"))

    r = client.post("/api/investments/sync-prices").json()
    assert r["ran"] is True
    assert r["updated"] == 1 and r["failed"] == 0 and r["total"] == 1

    holding = client.get(f"/api/investments/accounts/{aid}/holdings").json()[0]
    assert Decimal(holding["last_price"]) == Decimal("130")
    assert Decimal(holding["market_value"]) == Decimal("1300.00")  # 10 * 130
    assert Decimal(holding["gain"]) == Decimal("300.00")  # 1300 - 1000
    assert holding["last_price_at"] is not None


def test_sync_counts_failures(client, monkeypatch):
    aid = _investment(client)
    _holding(client, aid, symbol="GOOD.US")
    _holding(client, aid, symbol="BAD.US")

    client.put("/api/settings", json={"investment_price_source": "stooq"})
    # Only one symbol resolves; the other returns None (unknown ticker).
    monkeypatch.setattr(
        price_service,
        "fetch_quote",
        lambda symbol, source, api_key=None: Decimal("50") if symbol == "GOOD.US" else None,
    )

    r = client.post("/api/investments/sync-prices").json()
    assert r["total"] == 2 and r["updated"] == 1 and r["failed"] == 1


def test_fetch_quote_manual_and_unknown_never_fetch():
    # Pure unit check: manual/unknown sources return None without any network.
    assert price_service.fetch_quote("AAPL.US", "manual") is None
    assert price_service.fetch_quote("AAPL.US", "whatever") is None
    # alphavantage with no key is a no-op too.
    assert price_service.fetch_quote("AAPL.US", "alphavantage", None) is None


def test_stooq_csv_parsing(monkeypatch):
    # Parse a representative Stooq CSV without hitting the network.
    class _Resp:
        status_code = 200
        text = "Symbol,Date,Time,Open,High,Low,Close,Volume\nAAPL.US,2026-05-31,22:00:04,191,193,189,192.25,1000\n"

        def raise_for_status(self):
            return None

    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    assert price_service.fetch_stooq("aapl.us") == Decimal("192.25")

    # "N/D" (no data) → None.
    class _ND(_Resp):
        text = "Symbol,Date,Time,Open,High,Low,Close,Volume\nXXX,N/D,N/D,N/D,N/D,N/D,N/D,N/D\n"

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _ND())
    assert price_service.fetch_stooq("xxx") is None


def _resp(text):
    class _R:
        status_code = 200

        def __init__(self, t):
            self.text = t

        def raise_for_status(self):
            return None

    return _R(text)


def test_stooq_close_parsed_by_header_not_position(monkeypatch):
    # Close appears in a different position than the usual index 6; parsing by
    # header name must still pick the right column.
    import httpx

    text = "Symbol,Date,Close,Open,High,Low,Volume\nAAPL.US,2026-05-31,192.25,191,193,189,1000\n"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp(text))
    assert price_service.fetch_stooq("aapl.us") == Decimal("192.25")


def test_stooq_missing_close_column_is_no_quote(monkeypatch):
    # An error/HTML-ish response without a Close column must not crash or read a
    # wrong field — it resolves to None (the ValueError is caught + logged).
    import httpx

    text = "Symbol,Date,Time\nAAPL.US,2026-05-31,22:00:04\n"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp(text))
    assert price_service.fetch_stooq("aapl.us") is None


def test_stooq_zero_close_is_no_quote(monkeypatch):
    # A 0 close (delisting / bad row) must not be persisted as a real quote.
    import httpx

    text = "Symbol,Date,Time,Open,High,Low,Close,Volume\nDEAD.US,2026-05-31,22:00:04,0,0,0,0,0\n"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp(text))
    assert price_service.fetch_stooq("dead.us") is None


def test_stooq_case_insensitive_header(monkeypatch):
    # Header casing/whitespace shouldn't matter.
    import httpx

    text = "symbol, date, time, open, high, low, CLOSE, volume\nAAPL.US,2026-05-31,22:00:04,191,193,189,192.25,1000\n"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp(text))
    assert price_service.fetch_stooq("aapl.us") == Decimal("192.25")


# ---- Retry / backoff ------------------------------------------------------

_STOOQ_OK = "Symbol,Date,Time,Open,High,Low,Close,Volume\nAAPL.US,2026-05-31,22:00:04,191,193,189,192.25,1000\n"


class _FakeResp:
    def __init__(self, status_code=200, text="", json_body=None):
        self.status_code = status_code
        self.text = text
        self._json = json_body

    def raise_for_status(self):
        import httpx

        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self):
        return self._json


def _sequence(responses):
    """Return a fake httpx.get that yields ``responses`` in order; a response
    that is an Exception instance is raised instead of returned."""
    calls = {"n": 0}

    def _get(*_a, **_k):
        item = responses[calls["n"]]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    return _get, calls


def _no_sleep(monkeypatch):
    monkeypatch.setattr(price_service.time, "sleep", lambda *_a, **_k: None)


def test_stooq_retries_transient_5xx_then_succeeds(monkeypatch):
    # A 503 blip must be retried, not fail the whole fetch.
    import httpx

    _no_sleep(monkeypatch)
    get, calls = _sequence([_FakeResp(status_code=503), _FakeResp(text=_STOOQ_OK)])
    monkeypatch.setattr(httpx, "get", get)
    assert price_service.fetch_stooq("aapl.us") == Decimal("192.25")
    assert calls["n"] == 2  # first 503, then success


def test_stooq_retries_transient_timeout_then_succeeds(monkeypatch):
    # A connect/read timeout (TransportError) is transient → retried.
    import httpx

    _no_sleep(monkeypatch)
    get, calls = _sequence([httpx.ConnectTimeout("boom"), _FakeResp(text=_STOOQ_OK)])
    monkeypatch.setattr(httpx, "get", get)
    assert price_service.fetch_stooq("aapl.us") == Decimal("192.25")
    assert calls["n"] == 2


def test_stooq_gives_up_after_max_attempts(monkeypatch):
    # Persistent 429 exhausts the bounded retry → None (never raises).
    import httpx

    _no_sleep(monkeypatch)
    get, calls = _sequence([_FakeResp(status_code=429)] * 5)
    monkeypatch.setattr(httpx, "get", get)
    assert price_service.fetch_stooq("aapl.us") is None
    assert calls["n"] == price_service._MAX_ATTEMPTS  # capped, not unbounded


def test_stooq_permanent_4xx_not_retried(monkeypatch):
    # A 404 is permanent: one attempt, then None.
    import httpx

    _no_sleep(monkeypatch)
    get, calls = _sequence([_FakeResp(status_code=404), _FakeResp(text=_STOOQ_OK)])
    monkeypatch.setattr(httpx, "get", get)
    assert price_service.fetch_stooq("aapl.us") is None
    assert calls["n"] == 1  # not retried


# ---- Alpha Vantage rate-limit detection -----------------------------------


def test_alphavantage_parses_global_quote(monkeypatch):
    import httpx

    _no_sleep(monkeypatch)
    body = {"Global Quote": {"05. price": "192.2500"}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(json_body=body))
    assert price_service.fetch_alphavantage("AAPL.US", "KEY") == Decimal("192.2500")


def test_alphavantage_note_ratelimit_is_no_quote(monkeypatch):
    # HTTP 200 with a "Note" throttle body must resolve to None, not a zero.
    import httpx

    _no_sleep(monkeypatch)
    body = {"Note": "Thank you for using Alpha Vantage! ... call frequency ..."}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(json_body=body))
    assert price_service.fetch_alphavantage("AAPL.US", "KEY") is None


def test_alphavantage_information_ratelimit_is_no_quote(monkeypatch):
    # The newer "Information" throttle key is detected too.
    import httpx

    _no_sleep(monkeypatch)
    body = {"Information": "Our standard API rate limit is 25 requests per day."}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(json_body=body))
    assert price_service.fetch_alphavantage("AAPL.US", "KEY") is None


def test_alphavantage_empty_quote_is_none(monkeypatch):
    # An empty Global Quote (unknown symbol) is still a clean None.
    import httpx

    _no_sleep(monkeypatch)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(json_body={"Global Quote": {}}))
    assert price_service.fetch_alphavantage("XXX", "KEY") is None
