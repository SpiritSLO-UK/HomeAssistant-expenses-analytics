"""composite indexes on transactions for the hot list/aggregate paths

Adds (account_id, transaction_date) and (archived_at, transaction_date) composite
indexes. The transactions list filters by account and orders/filters by date, and
aggregates exclude archived rows over a date range — single-column indexes alone
left those as partial scans. Names match the model's __table_args__ so create_all
(tests) and Alembic (runtime) agree. No behaviour change.

Revision ID: f9a0b1c2d3e4
Revises: f4a5b6c7d8e9
Create Date: 2026-06-27 00:00:00.000000
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f9a0b1c2d3e4'
down_revision: str | None = 'f4a5b6c7d8e9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (index name, table, columns) — names match the Transaction model's __table_args__.
_INDEXES = [
    ("ix_transactions_account_id_date", "transactions", ["account_id", "transaction_date"]),
    ("ix_transactions_archived_at_date", "transactions", ["archived_at", "transaction_date"]),
]


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    for name, table, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
