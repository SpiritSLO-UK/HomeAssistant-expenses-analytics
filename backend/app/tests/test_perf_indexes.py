"""Guard: the hot query columns stay indexed (performance pass).

These columns are filtered / joined / grouped by every dashboard, budget,
project and vendor aggregate. SQLite doesn't auto-index foreign keys, so without
explicit indexes these were full table scans.
"""

from __future__ import annotations

from sqlalchemy import inspect

from app.db import session as dbsession

_EXPECTED = {
    "transactions": {
        "account_id", "merchant_id", "category_id", "project_id",
        "archived_at", "transaction_date",
    },
    "transaction_splits": {"transaction_id", "category_id", "project_id"},
}


def test_hot_columns_are_indexed(db):  # noqa: ARG001 (db fixture builds the schema)
    insp = inspect(dbsession.get_engine())
    for table, expected in _EXPECTED.items():
        indexed = {
            col
            for ix in insp.get_indexes(table)
            for col in ix.get("column_names", [])
            if col
        }
        missing = expected - indexed
        assert not missing, f"{table} is missing an index on {sorted(missing)}"
