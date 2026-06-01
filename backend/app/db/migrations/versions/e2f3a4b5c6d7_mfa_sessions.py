"""users MFA fields + user_sessions table (Stage 12-S2 app-level MFA)

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-06-01 16:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e2f3a4b5c6d7'
down_revision: str | None = 'd1e2f3a4b5c6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('mfa_secret', sa.String(length=64), nullable=True))
    op.add_column(
        'users',
        sa.Column('mfa_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        'user_sessions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('last_step_up_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_user_sessions_user_id', 'user_sessions', ['user_id'])
    op.create_index('ix_user_sessions_token_hash', 'user_sessions', ['token_hash'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_user_sessions_token_hash', table_name='user_sessions')
    op.drop_index('ix_user_sessions_user_id', table_name='user_sessions')
    op.drop_table('user_sessions')
    op.drop_column('users', 'mfa_enabled')
    op.drop_column('users', 'mfa_secret')
