"""Travel / spend-abroad analytics (backlog: holidays by country/currency).

Foreign-currency spend grouped by currency, trip detection by date-gap, and
turning a trip into a project. Base currency forced to GBP so EUR/USD are foreign.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.db import session as dbsession
from app.models import Transaction
from app.services import settings_service, travel_service


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _gbp(db) -> None:
    settings_service.set_value(db, settings_service.BASE_CURRENCY, "GBP")


def _txn(db, *, currency="EUR", base="-10.00", original=None, days_ago=0, archived=False) -> Transaction:
    b = Decimal(base)
    t = Transaction(
        transaction_date=date.today() - timedelta(days=days_ago),
        description_raw="abroad",
        amount=Decimal(original) if original is not None else b,
        currency=currency,
        direction="debit",
        base_amount=b,
        archived_at=_now() if archived else None,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_by_currency_groups_and_converts(db):
    _gbp(db)
    _txn(db, currency="EUR", base="-10.00", original="-12.00")
    _txn(db, currency="USD", base="-20.00", original="-25.00")
    _txn(db, currency="GBP", base="-5.00")  # home currency → excluded
    out = travel_service.by_currency(db)
    assert out["base_currency"] == "GBP"
    codes = [r["currency"] for r in out["currencies"]]
    assert codes == ["USD", "EUR"]  # sorted by base_total desc
    usd = out["currencies"][0]
    assert usd["base_total"] == "20.00"
    assert usd["original_total"] == "25.00"
    assert usd["place"] == "United States"


def test_archived_excluded_from_by_currency(db):
    _gbp(db)
    _txn(db, currency="EUR", base="-10.00")
    _txn(db, currency="EUR", base="-99.00", archived=True)
    rows = travel_service.by_currency(db)["currencies"]
    assert len(rows) == 1
    assert rows[0]["base_total"] == "10.00"  # archived excluded


def test_detect_trips_clusters_by_gap(db):
    _gbp(db)
    _txn(db, currency="EUR", days_ago=100)
    _txn(db, currency="EUR", days_ago=99)   # same trip as the 100d one
    _txn(db, currency="USD", days_ago=10)   # 89-day gap → a new trip
    trips = travel_service.detect_trips(db, gap_days=14)
    assert len(trips) == 2
    # Newest first: the recent USD trip leads.
    assert trips[0]["currencies"] == ["USD"]
    assert trips[1]["currencies"] == ["EUR"]
    assert trips[1]["transaction_count"] == 2


def test_create_project_from_trip_assigns_txns(db):
    _gbp(db)
    _txn(db, currency="EUR", days_ago=20)
    _txn(db, currency="EUR", days_ago=18)
    trip = travel_service.detect_trips(db)[0]
    project = travel_service.create_project_from_trip(db, name="Paris", transaction_ids=trip["transaction_ids"])
    assert project.id is not None
    for tid in trip["transaction_ids"]:
        assert db.get(Transaction, tid).project_id == project.id
    assert project.start_date is not None and project.end_date is not None


# --- API ------------------------------------------------------------------

def _seed_foreign() -> list[int]:
    ids = []
    with dbsession.SessionLocal() as s:
        settings_service.set_value(s, settings_service.BASE_CURRENCY, "GBP")
        for days in (12, 10):
            t = Transaction(
                transaction_date=date.today() - timedelta(days=days),
                description_raw="abroad",
                amount=Decimal("-10.00"),
                currency="EUR",
                direction="debit",
                base_amount=Decimal("-10.00"),
            )
            s.add(t)
            s.commit()
            ids.append(t.id)
    return ids


def test_travel_api_and_create_project(client):
    client.get("/api/users/me")  # owner
    ids = _seed_foreign()

    bc = client.get("/api/travel/by-currency").json()
    assert bc["currencies"][0]["currency"] == "EUR"

    trips = client.get("/api/travel/trips").json()
    assert len(trips) == 1
    assert sorted(trips[0]["transaction_ids"]) == sorted(ids)

    resp = client.post("/api/travel/trips/project", json={"name": "Euro trip", "transaction_ids": ids})
    assert resp.status_code == 201
    assert resp.json()["name"] == "Euro trip"
    # The project now appears in the projects list.
    assert any(p["name"] == "Euro trip" for p in client.get("/api/projects").json())
