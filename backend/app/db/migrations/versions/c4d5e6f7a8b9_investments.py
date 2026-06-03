"""account_values + holdings (investments & pensions)

Adds value-over-time snapshots (for pensions / lump-value investments) and
holdings (ticker positions with units, cost basis and last price) for the new
``investment`` / ``pension`` account types.

Revision ID: c4d5e6f7a8b9
Revises: b1c2d3e4f5a6
Create Date: 2026-06-03 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: str | None = 'b1c2d3e4f5a6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'account_values',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('as_of_date', sa.Date(), nullable=False),
        sa.Column('value', sa.Numeric(14, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='GBP'),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_account_values_account_id', 'account_values', ['account_id'])

    op.create_table(
        'holdings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=True),
        sa.Column('units', sa.Numeric(18, 6), nullable=False),
        sa.Column('avg_cost', sa.Numeric(18, 6), nullable=True),
        sa.Column('last_price', sa.Numeric(18, 6), nullable=True),
        sa.Column('last_price_at', sa.DateTime(), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='GBP'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_holdings_account_id', 'holdings', ['account_id'])


def downgrade() -> None:
    op.drop_index('ix_holdings_account_id', table_name='holdings')
    op.drop_table('holdings')
    op.drop_index('ix_account_values_account_id', table_name='account_values')
    op.drop_table('account_values')
