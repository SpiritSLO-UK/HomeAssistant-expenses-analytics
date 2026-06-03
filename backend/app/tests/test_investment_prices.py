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
