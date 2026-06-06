"""Full-text (FTS5/trigram) transaction search + its ILIKE fallback (backlog #43).

These run on plaintext SQLite (the test engine), which has FTS5 + trigram, so the
index path is exercised. The index is built by the `after_create` hook on every
schema reset, so it is available without any extra setup.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.db import search_index
from app.db.session import SessionLocal
from app.models import Account, Transaction


def _seed(client) -> dict[str, int]:
    """Owner + a shared account with a few transactions with known descriptions."""
    client.get("/api/users/me")  # local owner
    with SessionLocal() as db:
        acct = Account(name="Main", account_type="current_account", currency="GBP")
        db.add(acct)
        db.flush()
        rows = {
            "amazon": "AMZ*Amazon Marketplace",
            "costa": "COSTA COFFEE #4471",
            "gas": "DD BRITISH GAS ENERGY",
        }
        ids: dict[str, int] = {}
        for key, desc in rows.items():
            t = Transaction(
                account_id=acct.id, transaction_date=date(2026, 5, 2),
                description_raw=desc, merchant_raw=key.title(), amount=Decimal("-5.00"),
                currency="GBP", direction="debit", base_amount=Decimal("-5.00"), fx_rate=Decimal("1"),
            )
            db.add(t)
            db.flush()
            ids[key] = t.id
        db.commit()
    return ids


def _search_ids(client, term: str) -> set[int]:
    body = client.get("/api/transactions", params={"search": term}).json()
    return {row["id"] for row in body["items"]}


def test_fts_index_is_available_in_tests(client):
    _seed(client)
    assert search_index.is_available() is True


def test_search_matches_whole_word_case_insensitive(client):
    ids = _seed(client)
    found = _search_ids(client, "amazon")  # lower-case query, upper-case-ish data
    assert ids["amazon"] in found
    assert ids["costa"] not in found and ids["gas"] not in found


def test_search_matches_infix_substring(client):
    """'mazon' sits mid-token inside 'Amazon' — trigram FTS finds it (a prefix
    index could not), matching the original ILIKE '%term%' semantics."""
    ids = _seed(client)
    assert ids["amazon"] in _search_ids(client, "mazon")


def test_search_matches_merchant_column(client):
    ids = _seed(client)
    assert ids["gas"] in _search_ids(client, "british")  # only in description
    # 'costa' is the merchant_raw of the Costa row
    assert ids["costa"] in _search_ids(client, "costa")


def test_delete_keeps_index_in_sync(client):
    ids = _seed(client)
    assert ids["amazon"] in _search_ids(client, "amazon")
    assert client.delete(f"/api/transactions/{ids['amazon']}").status_code == 204
    assert ids["amazon"] not in _search_ids(client, "amazon")  # AFTER DELETE trigger fired


def test_update_keeps_index_in_sync(client):
    ids = _seed(client)
    with SessionLocal() as db:
        txn = db.get(Transaction, ids["costa"])
        txn.description_raw = "GREGGS BAKERY #88"
        txn.merchant_raw = "Greggs"
        db.commit()
    assert _search_ids(client, "costa") == set()  # old term gone (AFTER UPDATE trigger)
    assert ids["costa"] in _search_ids(client, "greggs")  # new term indexed


def test_short_query_falls_back_to_ilike(client):
    """A 2-char term is below the trigram minimum, so it must still work via the
    ILIKE fallback rather than erroring or returning nothing."""
    ids = _seed(client)
    assert search_index.use_fts("am") is False
    assert ids["amazon"] in _search_ids(client, "am")  # 'am' is a substring of AMZ/Amazon
