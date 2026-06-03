"""performance indexes on transactions + transaction_splits

Adds single-column indexes on the columns the dashboard / budget / project /
vendor aggregates filter, join and group by. SQLite doesn't auto-index foreign
keys, so these were full scans on larger ledgers. No behaviour change.

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-06-03 14:00:00.000000
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e6f7a8b9c0d1'
down_revision: str | None = 'd5e6f7a8b9c0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (index name, table, column) — names match SQLAlchemy's index=True convention so
# the migration and Base.metadata.create_all (used in tests) agree.
_INDEXES = [
    ("ix_transactions_account_id", "transactions", "account_id"),
    ("ix_transactions_merchant_id", "transactions", "merchant_id"),
    ("ix_transactions_category_id", "transactions", "category_id"),
    ("ix_transactions_project_id", "transactions", "project_id"),
    ("ix_transactions_archived_at", "transactions", "archived_at"),
    ("ix_transaction_splits_transaction_id", "transaction_splits", "transaction_id"),
    ("ix_transaction_splits_category_id", "transaction_splits", "category_id"),
    ("ix_transaction_splits_project_id", "transaction_splits", "project_id"),
]


def upgrade() -> None:
    for name, table, column in _INDEXES:
        op.create_index(name, table, [column])


def downgrade() -> None:
    for name, table, _column in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
