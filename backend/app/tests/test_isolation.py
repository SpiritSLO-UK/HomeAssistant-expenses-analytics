"""Guarantee tests run against a throwaway DB, never live data (backlog #30)."""

from __future__ import annotations

from app.config import settings
from app.db import session as dbsession


def test_database_is_temporary():
    assert "hafi-test-" in settings.database_path
    assert "hafi-test-" in str(dbsession.get_engine().url)


def test_not_pointing_at_dev_database():
    # The dev/bundled database lives at backend/data/finance.db — the test
    # suite must never resolve to it.
    normalised = settings.database_path.replace("\\", "/").lower()
    assert not normalised.endswith("backend/data/finance.db")
