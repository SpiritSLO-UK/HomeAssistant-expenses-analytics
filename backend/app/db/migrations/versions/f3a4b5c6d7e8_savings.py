"""savings_balances + savings_goals (Stage 12 savings)

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-06-01 18:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f3a4b5c6d7e8'
down_revision: str | None = 'e2f3a4b5c6d7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'savings_balances',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('as_of_date', sa.Date(), nullable=False),
        sa.Column('balance', sa.Numeric(14, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='GBP'),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_savings_balances_account_id', 'savings_balances', ['account_id'])

    op.create_table(
        'savings_goals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('household_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('target_amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('target_date', sa.Date(), nullable=True),
        sa.Column('account_id', sa.Integer(), nullable=True),
        sa.Column('current_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='GBP'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('savings_goals')
    op.drop_index('ix_savings_balances_account_id', table_name='savings_balances')
    op.drop_table('savings_balances')
