"""archived_at on transactions (data retention for the ledger, backlog #78)

Adds a nullable ``archived_at`` so the retention engine can archive (hide from
aggregates + the default list, keep the row) and later purge old transactions.
NULL = active, the default for every existing row, so no backfill and no
behaviour change until a retention policy runs.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-01 22:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: str | None = 'b2c3d4e5f6a7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('transactions', sa.Column('archived_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('transactions', 'archived_at')
