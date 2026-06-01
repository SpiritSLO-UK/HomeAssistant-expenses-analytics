"""child_allocations + budgets.owner_user_id (Stage 12 child allowance)

Revision ID: a1b2c3d4e5f6
Revises: f3a4b5c6d7e8
Create Date: 2026-06-01 19:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = 'f3a4b5c6d7e8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('budgets', sa.Column('owner_user_id', sa.Integer(), nullable=True))

    op.create_table(
        'child_allocations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('household_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('transaction_id', sa.Integer(), nullable=True),
        sa.Column('transaction_split_id', sa.Integer(), nullable=True),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='GBP'),
        sa.Column('description', sa.String(length=300), nullable=True),
        sa.Column('as_of_date', sa.Date(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_child_allocations_user_id', 'child_allocations', ['user_id'])
    op.create_index('ix_child_allocations_transaction_id', 'child_allocations', ['transaction_id'])


def downgrade() -> None:
    op.drop_index('ix_child_allocations_transaction_id', table_name='child_allocations')
    op.drop_index('ix_child_allocations_user_id', table_name='child_allocations')
    op.drop_table('child_allocations')
    op.drop_column('budgets', 'owner_user_id')
