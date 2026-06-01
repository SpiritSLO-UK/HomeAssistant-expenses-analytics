"""ai_requests: add transaction_id (Stage 10 cloud-approval workflow)

Revision ID: c8d2e3f4a5b6
Revises: b7c1d2e3f4a5
Create Date: 2026-06-01 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c8d2e3f4a5b6'
down_revision: str | None = 'b7c1d2e3f4a5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain column (no DB-level FK) — SQLite can't add a FK constraint via ALTER,
    # and the ORM declares the relationship anyway.
    op.add_column('ai_requests', sa.Column('transaction_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('ai_requests', 'transaction_id')
