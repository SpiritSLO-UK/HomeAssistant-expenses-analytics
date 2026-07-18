"""Subscription / recurring-payment tests (spec §20 — Stage 7)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import Account, Subscription, Transaction, Vendor
from app.services import subscription_service


def _curve(rows: list[tuple[str, str, str]], desc: str) -> bytes:
    head = "Date,Description,Amount,Currency,Card,Category\n"
    body = "".join(f"{d},{desc},{amt},GBP,Visa,\n" for d, amt in rows)
    return (head + body).encode()


def _import(client, content: bytes, name: str):
    up = client.post(
        "/api/imports/upload",
        files={"file": (name, content, "text/csv")},
        data={"parser_id": "curve_csv"},
    ).json()
    return client.post(f"/api/imports/{up['import_id']}/confirm").json()


def _monthly(desc: str) -> bytes:
    # Four monthly charges, same amount -> a clean monthly subscription.
    return _curve(
        [("2026-01-05", "-9.99"), ("2026-02-05", "-9.99"),
         ("2026-03-05", "-9.99"), ("2026-04-05", "-9.99")],
        desc,
    )


def _subs(client) -> list[dict]:
    return client.get("/api/subscriptions").json()


# --- detection runs automatically on import (spec §20.1, §27.1) ---

def test_detects_monthly_subscription_on_import(client):
    _import(client, _monthly("NETFLIX"), "netflix.csv")
    subs = _subs(client)
    assert len(subs) == 1
    s = subs[0]
    assert s["name"].upper().startswith("NETFLIX")
    assert s["frequency"] == "monthly"
    assert s["amount"] == "9.99"
    assert s["monthly_amount"] == "9.99"
    assert s["occurrences"] == 4
    assert s["status"] == "active"
    assert s["confidence_score"] >= 0.6
    assert s["next_expected_date"] is not None


def test_two_charges_are_not_a_subscription(client):
    _import(client, _curve([("2026-01-05", "-9.99"), ("2026-02-05", "-9.99")], "SPOTIFY"), "s.csv")
    assert _subs(client) == []


def test_weekly_monthly_equivalent(client):
    _import(
        client,
        _curve([("2026-01-05", "-9.99"), ("2026-01-12", "-9.99"),
                ("2026-01-19", "-9.99"), ("2026-01-26", "-9.99")], "GYM WEEKLY"),
        "gym.csv",
    )
    s = _subs(client)[0]
    assert s["frequency"] == "weekly"
    assert s["monthly_amount"] == "43.29"  # 9.99 * 52 / 12


def test_variable_amount_not_detected(client):
    # Same vendor, monthly cadence, but wildly varying amounts -> not a subscription.
    _import(
        client,
        _curve([("2026-01-05", "-10.00"), ("2026-02-05", "-90.00"),
                ("2026-03-05", "-5.00"), ("2026-04-05", "-60.00")], "TESCO STORES"),
        "tesco.csv",
    )
    assert _subs(client) == []


# --- frequency-band coverage: fortnightly (~14d) + bi-monthly (~60d) (SR-C5) ---

def test_detects_fortnightly_subscription(client):
    # Charges ~14 days apart -> fortnightly, previously lost in the weekly/monthly gap.
    _import(
        client,
        _curve([("2026-01-05", "-4.99"), ("2026-01-19", "-4.99"),
                ("2026-02-02", "-4.99"), ("2026-02-16", "-4.99")], "FORTNIGHT BOX"),
        "fortnight.csv",
    )
    subs = _subs(client)
    assert len(subs) == 1
    s = subs[0]
    assert s["frequency"] == "fortnightly"
    assert s["interval_days"] == 14
    # 4.99 * 26 / 12 = 10.8116... -> 10.81
    assert s["monthly_amount"] == "10.81"
    assert s["occurrences"] == 4


def test_detects_bi_monthly_subscription(client):
    # Charges ~60 days apart -> bi-monthly, previously between monthly and quarterly.
    _import(
        client,
        _curve([("2026-01-01", "-20.00"), ("2026-03-02", "-20.00"),
                ("2026-05-01", "-20.00"), ("2026-06-30", "-20.00")], "BIMONTHLY CLUB"),
        "bimonthly.csv",
    )
    subs = _subs(client)
    assert len(subs) == 1
    s = subs[0]
    assert s["frequency"] == "bi_monthly"
    assert s["interval_days"] == 61
    assert s["monthly_amount"] == "10.00"  # 20.00 / 2 months
    assert s["occurrences"] == 4


def test_clean_monthly_series_unchanged(client):
    # Regression guard: a clean ~30d cadence still classifies as monthly.
    _import(client, _monthly("NETFLIX"), "netflix.csv")
    s = _subs(client)[0]
    assert s["frequency"] == "monthly"
    assert s["interval_days"] == 30
    assert s["monthly_amount"] == "9.99"


# --- a legit price rise keeps confidence (SR-C5) ---

def test_price_increase_keeps_confidence(client):
    # Monthly cadence, monotonically rising price (a real price increase). This
    # should stay confidently detected as active, not demoted like erratic amounts.
    _import(
        client,
        _curve([("2026-01-05", "-9.99"), ("2026-02-05", "-10.99"),
                ("2026-03-05", "-11.99"), ("2026-04-05", "-12.99")], "STREAMING UP"),
        "rising.csv",
    )
    subs = _subs(client)
    assert len(subs) == 1
    s = subs[0]
    assert s["frequency"] == "monthly"
    assert s["amount"] == "12.99"  # current price = most recent charge
    assert s["status"] == "active"
    assert s["confidence_score"] >= 0.6


def test_erratic_amount_scores_below_monotonic_rise(client):
    # Same four amounts as the rising series and the same monthly cadence, but
    # ordered so the price goes up AND down (noise, not a legit rise). It must NOT
    # get the full monotonic-increase amount credit -> strictly lower confidence.
    _import(
        client,
        _curve([("2026-01-05", "-9.99"), ("2026-02-05", "-12.99"),
                ("2026-03-05", "-10.99"), ("2026-04-05", "-11.99")], "NOISY BILL"),
        "noisy.csv",
    )
    rise = _import(
        client,
        _curve([("2026-01-06", "-9.99"), ("2026-02-06", "-10.99"),
                ("2026-03-06", "-11.99"), ("2026-04-06", "-12.99")], "RISING BILL"),
        "rise.csv",
    )
    assert rise is not None
    subs = {s["name"].split()[0].upper(): s for s in _subs(client)}
    noisy = subs["NOISY"]["confidence_score"]
    rising = subs["RISING"]["confidence_score"]
    # Both are regular monthly, so the only difference is the amount component:
    # the erratic ordering scores lower than the monotonic rise.
    assert noisy < rising


# --- user override is preserved across re-detection (spec §20.2 status) ---

def test_user_status_not_overwritten_by_redetect(client):
    _import(client, _monthly("NETFLIX"), "netflix.csv")
    sid = _subs(client)[0]["id"]
    assert client.patch(f"/api/subscriptions/{sid}", json={"status": "cancelled"}).json()["status"] == "cancelled"
    # Re-running detection must keep the user's "cancelled".
    client.post("/api/subscriptions/detect")
    assert next(s for s in _subs(client) if s["id"] == sid)["status"] == "cancelled"


def test_delete_subscription(client):
    _import(client, _monthly("NETFLIX"), "netflix.csv")
    sid = _subs(client)[0]["id"]
    assert client.delete(f"/api/subscriptions/{sid}").status_code == 204
    assert _subs(client) == []


# --- dashboard + MQTT ---

def test_dashboard_subscriptions_total(client):
    _import(client, _monthly("NETFLIX"), "netflix.csv")
    _import(
        client,
        _curve([("2026-01-08", "-12.00"), ("2026-02-08", "-12.00"),
                ("2026-03-08", "-12.00"), ("2026-04-08", "-12.00")], "DISNEY PLUS"),
        "disney.csv",
    )
    d = client.get("/api/dashboard/subscriptions").json()
    assert d["count"] == 2
    assert d["monthly_total"] == "21.99"  # 9.99 + 12.00

    # cancelling one drops it from the active total
    sid = next(s["id"] for s in _subs(client) if s["name"].upper().startswith("NETFLIX"))
    client.patch(f"/api/subscriptions/{sid}", json={"status": "cancelled"})
    assert client.get("/api/dashboard/subscriptions").json()["monthly_total"] == "12.00"


def test_subscriptions_total_mqtt_sensor(client):
    _import(client, _monthly("NETFLIX"), "netflix.csv")
    state = client.get("/api/mqtt/preview").json()["state"]
    assert "subscriptions_total" in state
    assert state["subscriptions_total"] == "9.99"


# --- perf: bounded detection scan + narrowed visibility query (results unchanged) ---

def _spend(
    db,
    *,
    when: date,
    amount: str,
    account_id: int | None = None,
    merchant_raw: str | None = None,
    merchant_id: int | None = None,
) -> Transaction:
    """Insert one money-out transaction directly (base_amount = amount, in base
    currency) for the detection/visibility service tests."""
    txn = Transaction(
        account_id=account_id,
        transaction_date=when,
        description_raw=merchant_raw or "charge",
        merchant_raw=merchant_raw,
        merchant_id=merchant_id,
        amount=Decimal(amount),
        currency="GBP",
        direction="debit",
        base_amount=Decimal(amount),
    )
    db.add(txn)
    return txn


def test_detect_window_keeps_long_history_recurrence(db):
    # A monthly recurrence spanning ~34 charges (close to, but inside, the 3-year
    # window anchored on the latest charge). Every occurrence is within the window,
    # so bounding the scan yields exactly what an unbounded all-time scan would.
    latest = date(2026, 6, 5)
    charges = 34
    for i in range(charges):
        _spend(db, when=latest - timedelta(days=30 * i), amount="-9.99", merchant_raw="NETFLIX")
    db.commit()

    result = subscription_service.detect(db)
    assert result["created"] == 1

    subs = db.scalars(select(Subscription)).all()
    assert len(subs) == 1
    sub = subs[0]
    assert sub.name.upper() == "NETFLIX"
    assert sub.frequency == "monthly"
    assert sub.occurrences == charges
    assert sub.amount == Decimal("9.99")
    assert sub.status == "active"


def test_detect_ignores_spend_far_outside_window(db):
    # A monthly recurrence that stopped ~5 years before the latest activity is well
    # outside the 3-year window and must not be (re)detected, while a recent monthly
    # recurrence that anchors the window still is.
    latest = date(2026, 6, 1)
    for i in range(4):
        _spend(db, when=date(2021, 1, 3) + timedelta(days=30 * i), amount="-20.00", merchant_raw="OLD GYM")
    for i in range(4):
        _spend(db, when=latest - timedelta(days=30 * i), amount="-12.00", merchant_raw="NEW STREAM")
    db.commit()

    subscription_service.detect(db)
    names = {s.name.upper() for s in db.scalars(select(Subscription)).all()}
    assert "NEW STREAM" in names
    assert "OLD GYM" not in names


def test_detect_no_spend_returns_zero(db):
    result = subscription_service.detect(db)
    assert result == {"created": 0, "updated": 0, "total": 0}


def test_visible_subscription_ids_scoped_to_account(db):
    # Two accounts, three subscriptions: a name-keyed sub backed only by account A,
    # a name-keyed sub backed only by account B, and a vendor-keyed sub backed by A.
    acct_a = Account(name="A")
    acct_b = Account(name="B")
    db.add_all([acct_a, acct_b])
    db.flush()
    vendor = Vendor(canonical_name="acme")
    db.add(vendor)
    db.flush()

    _spend(db, account_id=acct_a.id, when=date(2026, 1, 5), amount="-9.99", merchant_raw="NETFLIX")
    _spend(db, account_id=acct_b.id, when=date(2026, 1, 5), amount="-9.99", merchant_raw="SPOTIFY")
    _spend(db, account_id=acct_a.id, when=date(2026, 1, 5), amount="-5.00", merchant_raw="ACME", merchant_id=vendor.id)

    sub_netflix = Subscription(name="Netflix", amount=Decimal("9.99"), frequency="monthly", interval_days=30, status="active")
    sub_spotify = Subscription(name="Spotify", amount=Decimal("9.99"), frequency="monthly", interval_days=30, status="active")
    sub_acme = Subscription(name="Acme", vendor_id=vendor.id, amount=Decimal("5.00"), frequency="monthly", interval_days=30, status="active")
    db.add_all([sub_netflix, sub_spotify, sub_acme])
    db.commit()

    # None (owner/admin) is unrestricted.
    assert subscription_service.visible_subscription_ids(db, None) is None
    # Account A sees its name-keyed and vendor-keyed subs, not B's.
    assert subscription_service.visible_subscription_ids(db, {acct_a.id}) == {sub_netflix.id, sub_acme.id}
    # Account B sees only its own.
    assert subscription_service.visible_subscription_ids(db, {acct_b.id}) == {sub_spotify.id}
    # Empty scope matches nothing (never everything).
    assert subscription_service.visible_subscription_ids(db, set()) == set()
