"""users: external_id, status, last_seen_at (Stage 12 RBAC / multi-user)

Revision ID: d1e2f3a4b5c6
Revises: c8d2e3f4a5b6
Create Date: 2026-06-01 14:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: str | None = 'c8d2e3f4a5b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows are the original single-user owner → default them to approved.
    op.add_column('users', sa.Column('external_id', sa.String(length=255), nullable=True))
    op.add_column(
        'users',
        sa.Column('status', sa.String(length=32), nullable=False, server_default='approved'),
    )
    op.add_column('users', sa.Column('last_seen_at', sa.DateTime(), nullable=True))
    op.create_index('ix_users_external_id', 'users', ['external_id'])


def downgrade() -> None:
    op.drop_index('ix_users_external_id', table_name='users')
    op.drop_column('users', 'last_seen_at')
    op.drop_column('users', 'status')
    op.drop_column('users', 'external_id')
