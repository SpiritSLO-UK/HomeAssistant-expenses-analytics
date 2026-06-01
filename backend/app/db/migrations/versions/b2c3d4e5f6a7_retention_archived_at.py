"""archived_at on ai_requests, audit_logs, receipts (data retention, backlog #78)

Adds a nullable ``archived_at`` timestamp so the retention engine can mark a row
as archived (hidden from its viewer; for receipts, original file dropped) before
a later purge. NULL = active, the default for every existing row, so no backfill
and no behaviour change until a retention policy runs.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-01 21:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: str | None = 'a1b2c3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('ai_requests', sa.Column('archived_at', sa.DateTime(), nullable=True))
    op.add_column('audit_logs', sa.Column('archived_at', sa.DateTime(), nullable=True))
    op.add_column('receipts', sa.Column('archived_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('receipts', 'archived_at')
    op.drop_column('audit_logs', 'archived_at')
    op.drop_column('ai_requests', 'archived_at')
