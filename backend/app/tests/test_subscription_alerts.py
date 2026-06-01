"""Subscription renewal + missed-payment alerts (Stage 12; spec §20.3)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models import Subscription
from app.services import analytics_service, subscription_service

REF = date(2026, 6, 15)


def _sub(db, name, next_date, *, status="active", amount="9.99", interval=30):
    db.add(Subscription(
        name=name, amount=Decimal(amount), currency="GBP", frequency="monthly",
        interval_days=interval, next_expected_date=next_date,
        last_seen_date=next_date - timedelta(days=interval), status=status, occurrences=4,
    ))


def test_upcoming_and_overdue_are_separated(db):
    _sub(db, "Netflix", REF + timedelta(days=3))                 # due in 3 days → upcoming
    _sub(db, "Spotify", REF + timedelta(days=20))               # outside the 7-day window
    _sub(db, "Gym", REF - timedelta(days=10))                   # 10 days overdue
    _sub(db, "OldMag", REF - timedelta(days=40), status="cancelled")  # not active → ignored
    db.commit()

    a = subscription_service.alerts(db, REF, within_days=7)
    assert {x["name"] for x in a["upcoming"]} == {"Netflix"}
    assert {x["name"] for x in a["overdue"]} == {"Gym"}
    assert a["upcoming"][0]["days_until"] == 3
    assert a["overdue"][0]["days_overdue"] == 10
    assert a["overdue"][0]["expected_date"] == (REF - timedelta(days=10)).isoformat()


def test_due_today_counts_as_upcoming(db):
    _sub(db, "Rent", REF)
    db.commit()
    a = subscription_service.alerts(db, REF, within_days=7)
    assert a["upcoming"][0]["days_until"] == 0


def test_nothing_due_no_alerts(db):
    _sub(db, "Insurance", REF + timedelta(days=40))
    db.commit()
    a = subscription_service.alerts(db, REF, within_days=7)
    assert a["upcoming"] == [] and a["overdue"] == []


def test_overdue_subscription_appears_in_headsup(db):
    today = date.today()
    _sub(db, "Gym", today - timedelta(days=30))  # active + long overdue
    db.commit()
    items = analytics_service.outliers(db, today)["items"]
    subs = [i for i in items if i["type"] == "subscription"]
    assert len(subs) == 1
    assert subs[0]["severity"] == "warn"
    assert subs[0]["subscription_id"] is not None
