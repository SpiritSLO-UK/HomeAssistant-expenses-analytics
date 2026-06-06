"""Full-text search index for transactions (performance, backlog #43).

Substring search over a large transactions table via ``description_raw ILIKE
'%term%'`` is a full table scan — fine for a household's few thousand rows, but
on very large datasets (100k–1M+) a single keystroke can take tens of seconds.

This module maintains a SQLite **FTS5** virtual table (``transactions_fts``) with
the **trigram** tokenizer, which makes case-insensitive *substring* search index-
backed and near-instant. Three triggers keep it in lock-step with the
``transactions`` table on insert/update/delete, so it never goes stale.

It is entirely **best-effort**: if the running SQLite build lacks FTS5 or the
trigram tokenizer (e.g. some SQLCipher builds), the index is silently skipped and
callers fall back to the original ILIKE search — correct, just slower. The index
is a derived cache, not part of the ORM schema, so it is created/refreshed from a
``Base.metadata`` ``after_create`` hook (which fires on every ``create_all`` —
fresh installs, upgrades and the test harness alike) rather than an Alembic
migration. Trigram MATCH needs at least 3 characters, so shorter queries also use
ILIKE.
"""

from __future__ import annotations

from sqlalchemy import bindparam, column, event, select, text
from sqlalchemy.engine import Connection

from app.db.base import Base
from app.logging import get_logger

logger = get_logger(__name__)

FTS_TABLE = "transactions_fts"
MIN_FTS_CHARS = 3  # the trigram tokenizer needs at least one 3-char gram

# Virtual table + the triggers that keep it in sync with `transactions`. All
# idempotent (IF NOT EXISTS) so the hook can run on every startup safely.
_DDL: tuple[str, ...] = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} "
    "USING fts5(description_raw, merchant_raw, tokenize='trigram')",
    f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ai AFTER INSERT ON transactions BEGIN
        INSERT INTO {FTS_TABLE}(rowid, description_raw, merchant_raw)
        VALUES (new.id, coalesce(new.description_raw, ''), coalesce(new.merchant_raw, ''));
    END""",
    f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ad AFTER DELETE ON transactions BEGIN
        DELETE FROM {FTS_TABLE} WHERE rowid = old.id;
    END""",
    f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_au AFTER UPDATE ON transactions BEGIN
        DELETE FROM {FTS_TABLE} WHERE rowid = old.id;
        INSERT INTO {FTS_TABLE}(rowid, description_raw, merchant_raw)
        VALUES (new.id, coalesce(new.description_raw, ''), coalesce(new.merchant_raw, ''));
    END""",
)

# Whether the FTS index is usable on the current engine. None until probed; set by
# ensure_search_index(). Conservatively False so callers fall back to ILIKE until
# the index is confirmed built.
_fts_available: bool | None = None


def is_available() -> bool:
    """True if the FTS index has been built and is usable on this engine."""
    return _fts_available is True


def use_fts(term: str | None) -> bool:
    """Whether a search for ``term`` should go through the FTS index. Requires the
    index to be available and the term to be long enough for the trigram tokenizer
    (shorter terms fall back to ILIKE)."""
    return bool(term) and len((term or "").strip()) >= MIN_FTS_CHARS and is_available()


def match_subquery(term: str):
    """A scalar subquery of ``transactions.id`` values whose description or merchant
    contains ``term`` (case-insensitive substring), resolved via the FTS index.

    Use as ``Transaction.id.in_(match_subquery(term))``. The term is wrapped in a
    quoted FTS string so it is treated as a literal substring (with the trigram
    tokenizer) rather than FTS query syntax; embedded double-quotes are escaped."""
    fts_q = '"' + term.strip().replace('"', '""') + '"'
    where = text(f"{FTS_TABLE} MATCH :fts_q").bindparams(bindparam("fts_q", value=fts_q))
    return select(column("rowid")).select_from(text(FTS_TABLE)).where(where).scalar_subquery()


def _resync(connection: Connection) -> None:
    """Make the FTS index match the transactions table exactly. Cheap no-op when
    the row counts already agree (the steady state, maintained by the triggers);
    a full rebuild only on first creation / after a schema reset."""
    fts_n = connection.exec_driver_sql(f"SELECT count(*) FROM {FTS_TABLE}").scalar() or 0
    txn_n = connection.exec_driver_sql("SELECT count(*) FROM transactions").scalar() or 0
    if fts_n != txn_n:
        connection.exec_driver_sql(f"DELETE FROM {FTS_TABLE}")
        connection.exec_driver_sql(
            f"INSERT INTO {FTS_TABLE}(rowid, description_raw, merchant_raw) "
            "SELECT id, coalesce(description_raw, ''), coalesce(merchant_raw, '') FROM transactions"
        )


def ensure_search_index(connection: Connection) -> bool:
    """Create the FTS index + sync triggers and backfill from existing rows, all
    idempotent and best-effort. Wrapped in a SAVEPOINT so that, on a SQLite build
    without FTS5/trigram, the failed DDL rolls back cleanly without poisoning the
    caller's transaction. Sets and returns the availability flag."""
    global _fts_available
    try:
        with connection.begin_nested():
            for ddl in _DDL:
                connection.exec_driver_sql(ddl)
            _resync(connection)
        _fts_available = True
    except Exception:  # FTS5 / trigram unavailable in this build → fall back to ILIKE
        logger.warning(
            "Full-text search index unavailable on this SQLite build; "
            "falling back to slower substring search.",
            exc_info=True,
        )
        _fts_available = False
    return _fts_available


@event.listens_for(Base.metadata, "after_create")
def _build_fts_after_create(_target, connection: Connection, **_kw) -> None:
    """Build/refresh the FTS index whenever the schema is created. This fires on
    every ``create_all`` — the startup safety net, a fresh add-on, and each test's
    schema reset — so the index tracks the table without an Alembic migration."""
    ensure_search_index(connection)
