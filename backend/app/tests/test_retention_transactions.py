"""Transaction archive & purge (backlog #78, PR #12).

Archived transactions must vanish from every aggregate and the default list (kept,
reversible), and purge must delete them. The exclusion rides the shared
``scope.archived_condition`` helper threaded through the same functions as the
account-visibility scope, so testing a representative few proves the pattern.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db import session as dbsession
from app.models import Transaction
from app.services import analytics_service, dashboard_service, retention_service


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _add(db, *, days_ago: int = 0, amount: str = "-10.00", archived: bool = False) -> Transaction:
    amt = Decimal(amount)
    t = Transaction(
        transaction_date=date.today() - timedelta(days=days_ago),
        description_raw="seed",
        amount=amt,
        currency="GBP",
        direction="debit" if amt < 0 else "credit",
        base_amount=amt,
        archived_at=_now() if archived else None,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


# --- aggregates exclude archived -----------------------------------------

def test_archived_excluded_from_dashboard_summary_and_counts(db):
    _add(db, amount="-10.00")               # active spend
    _add(db, amount="-100.00", archived=True)  # archived spend
    s = dashboard_service.summary(db, date.today())
    assert s["spend_this_month"] == "10.00"   # the £100 archived row is excluded
    assert s["total_transactions"] == 1       # counts exclude archived too


def test_archived_excluded_from_category_breakdown(db):
    _add(db, amount="-10.00")
    _add(db, amount="-100.00", archived=True)
    rows = dashboard_service.category_breakdown(db, date.today())
    assert sum(Decimal(r["total"]) for r in rows) == Decimal("10.00")


def test_archived_excluded_from_trends(db):
    _add(db, amount="-10.00")
    _add(db, amount="-100.00", archived=True)
    series = analytics_service.monthly_series(db, date.today())
    assert series["months"][-1]["spend"] == "10.00"  # current month, archived excluded


# --- retention engine: archive then purge transactions -------------------

def test_retention_archives_then_purges_transactions(db):
    _add(db, days_ago=400)   # old
    _add(db, days_ago=10)    # recent
    retention_service.save_policy(db, retention_service.validate_policy(
        {"transactions": {"archive_after_days": 30, "purge_after_days": 365}}))
    plan = retention_service.preview(db)
    assert plan["transactions"]["archive_due"] == 1  # the 400d row (10d is < 30)
    assert plan["transactions"]["purge_due"] == 1     # the 400d row

    result = retention_service.run(db, actor="owner", purge_mode="all")
    assert result["counts"]["transactions"]["archived"] == 1
    assert result["counts"]["transactions"]["purged"] == 1
    assert result["backup_taken"] is True
    # The 400d row was archived then purged; only the recent active row remains.
    remaining = db.scalars(select(Transaction)).all()
    assert len(remaining) == 1
    assert remaining[0].archived_at is None


# --- API: list toggle + un-archive ---------------------------------------

def _seed(*, archived: bool) -> int:
    with dbsession.SessionLocal() as s:
        amt = Decimal("-10.00")
        t = Transaction(
            transaction_date=date.today(),
            description_raw="api-seed",
            amount=amt,
            currency="GBP",
            direction="debit",
            base_amount=amt,
            archived_at=_now() if archived else None,
        )
        s.add(t)
        s.commit()
        return t.id


def test_list_hides_archived_and_unarchive_restores(client):
    client.get("/api/users/me")  # bootstrap owner
    _seed(archived=False)
    archived_id = _seed(archived=True)

    # Default list hides the archived row.
    assert client.get("/api/transactions").json()["total"] == 1
    # The toggle reveals it.
    assert client.get("/api/transactions", params={"include_archived": "true"}).json()["total"] == 2

    # Un-archiving brings it back into the default list.
    assert client.post(f"/api/transactions/{archived_id}/unarchive").status_code == 200
    assert client.get("/api/transactions").json()["total"] == 2
