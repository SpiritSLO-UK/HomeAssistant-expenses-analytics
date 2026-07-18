"""mfa_backup_codes: single-use hashed recovery codes (CR-FEAT-1)

Revision ID: d3e4f5a6b7c8
Revises: c6d7e8f9a0b1
Create Date: 2026-07-18 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd3e4f5a6b7c8'
down_revision: str | None = 'c6d7e8f9a0b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'mfa_backup_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_mfa_backup_codes_user_id', 'mfa_backup_codes', ['user_id'])
    op.create_index('ix_mfa_backup_codes_code_hash', 'mfa_backup_codes', ['code_hash'])


def downgrade() -> None:
    op.drop_index('ix_mfa_backup_codes_code_hash', table_name='mfa_backup_codes')
    op.drop_index('ix_mfa_backup_codes_user_id', table_name='mfa_backup_codes')
    op.drop_table('mfa_backup_codes')
