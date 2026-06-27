"""Global search across transactions / vendors / categories / projects."""

from __future__ import annotations

import pytest


def test_search_matches_transactions_and_vendor(client):
    client.post("/api/backup/demo")
    r = client.get("/api/search", params={"q": "tesco"}).json()
    assert r["query"] == "tesco"
    assert any("TESCO" in t["description"].upper() for t in r["transactions"])
    assert any(v["name"] == "Tesco" for v in r["vendors"])  # demo seeds a Tesco vendor


def test_search_matches_category_and_project(client):
    client.post("/api/backup/demo")
    cats = client.get("/api/search", params={"q": "groceries"}).json()["categories"]
    assert any(c["name"] == "Groceries" for c in cats)
    projects = client.get("/api/search", params={"q": "spain"}).json()["projects"]
    assert any("Spain" in p["name"] for p in projects)  # demo seeds a "Spain City Break" project


def test_search_by_amount(client):
    client.post("/api/backup/demo")
    # The demo mortgage is 875.00 (stored as -875.00); an amount query finds it.
    txns = client.get("/api/search", params={"q": "875.00"}).json()["transactions"]
    assert any(abs(float(t["amount"])) == pytest.approx(875.00) for t in txns)


def test_search_by_amount_strips_currency_symbols(client):
    client.post("/api/backup/demo")
    # The same mortgage as above, but typed with a currency symbol — the symbol
    # is stripped before parsing (SR-B4: not just £, also $/€).
    for q in ("£875.00", "$875.00", "€875"):
        txns = client.get("/api/search", params={"q": q}).json()["transactions"]
        assert any(abs(float(t["amount"])) == pytest.approx(875.00) for t in txns), q


def test_search_short_query_returns_empty(client):
    client.post("/api/backup/demo")
    r = client.get("/api/search", params={"q": "a"}).json()
    assert r["transactions"] == [] and r["vendors"] == [] and r["categories"] == []
