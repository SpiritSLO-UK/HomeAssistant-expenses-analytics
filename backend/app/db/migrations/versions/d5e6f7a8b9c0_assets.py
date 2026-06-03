"""assets + asset_logs (car/home dashboards)

Tracked non-account things (car/home/other) with a timeline of logs. Car refuel
logs (odometer + litres + cost) yield consumption stats; home reading columns
are shipped now so the home feature needs no further migration.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-06-03 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: str | None = 'c4d5e6f7a8b9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'assets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('household_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False, server_default='car'),
        sa.Column('identifier', sa.String(length=100), nullable=True),
        sa.Column('distance_unit', sa.String(length=8), nullable=False, server_default='mi'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        'asset_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('asset_id', sa.Integer(), nullable=False),
        sa.Column('log_date', sa.Date(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False, server_default='refuel'),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.Column('cost', sa.Numeric(12, 2), nullable=True),
        sa.Column('odometer', sa.Numeric(12, 1), nullable=True),
        sa.Column('litres', sa.Numeric(10, 3), nullable=True),
        sa.Column('is_full_tank', sa.Boolean(), nullable=True, server_default=sa.true()),
        sa.Column('fuel_type', sa.String(length=20), nullable=True),
        sa.Column('meter', sa.String(length=40), nullable=True),
        sa.Column('reading', sa.Numeric(14, 3), nullable=True),
        sa.Column('unit', sa.String(length=16), nullable=True),
        sa.Column('transaction_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_asset_logs_asset_id', 'asset_logs', ['asset_id'])


def downgrade() -> None:
    op.drop_index('ix_asset_logs_asset_id', table_name='asset_logs')
    op.drop_table('asset_logs')
    op.drop_table('assets')
