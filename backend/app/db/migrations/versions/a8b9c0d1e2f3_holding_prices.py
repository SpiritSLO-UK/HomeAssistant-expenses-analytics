"""holding_prices (price history for investment charts + period changes)

One row per (holding, date), recorded whenever a holding's price is set/updated/
synced. Enables reconstructing portfolio value over time.

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-06-03 16:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a8b9c0d1e2f3'
down_revision: str | None = 'f7a8b9c0d1e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'holding_prices',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('holding_id', sa.Integer(), nullable=False),
        sa.Column('as_of_date', sa.Date(), nullable=False),
        sa.Column('price', sa.Numeric(18, 6), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_holding_prices_holding_id', 'holding_prices', ['holding_id'])


def downgrade() -> None:
    op.drop_index('ix_holding_prices_holding_id', table_name='holding_prices')
    op.drop_table('holding_prices')
