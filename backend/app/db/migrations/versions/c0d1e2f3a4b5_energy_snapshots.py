"""energy_snapshots table (production samples for the energy trend over time)

Periodic samples of produced energy (kWh) from the configured source, used to
chart production/saving over time. Cumulative sensors are diffed between
boundaries; interval sensors are summed.

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-06-05 16:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c0d1e2f3a4b5'
down_revision: str | None = 'b9c0d1e2f3a4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'energy_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('household_id', sa.Integer(), sa.ForeignKey('households.id', ondelete='CASCADE'), nullable=True),
        sa.Column('captured_at', sa.DateTime(), nullable=False),
        sa.Column('produced', sa.Numeric(14, 3), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
    )
    op.create_index('ix_energy_snapshots_captured_at', 'energy_snapshots', ['captured_at'])


def downgrade() -> None:
    op.drop_index('ix_energy_snapshots_captured_at', table_name='energy_snapshots')
    op.drop_table('energy_snapshots')
