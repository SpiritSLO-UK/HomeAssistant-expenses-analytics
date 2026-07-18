"""The SQLite engine keeps a generous connection pool so a burst of concurrent
requests (the dashboard fans out ~10 parallel card queries per load) does not
exhaust it and return 500s. Regression guard for the QueuePool-timeout fix."""

from app.db import session as db_session


def test_plaintext_engine_pool_has_headroom():
    engine = db_session._build_plaintext_engine()
    try:
        # 20 base + 30 overflow = 50 concurrent connections available, well above
        # the SQLAlchemy default of 5 + 10 that a page-load burst overran.
        assert engine.pool.size() == 20
        assert engine.pool._max_overflow == 30
    finally:
        engine.dispose()
