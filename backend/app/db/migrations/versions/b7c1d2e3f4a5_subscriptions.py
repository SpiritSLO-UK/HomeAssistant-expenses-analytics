"""subscriptions: recurring-payment detection table (spec §20)

Revision ID: b7c1d2e3f4a5
Revises: 9640ebc219d0
Create Date: 2026-05-31 21:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7c1d2e3f4a5'
down_revision: str | None = '9640ebc219d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'subscriptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('household_id', sa.Integer(), nullable=True),
        sa.Column('vendor_id', sa.Integer(), nullable=True),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('frequency', sa.String(length=16), nullable=False),
        sa.Column('interval_days', sa.Integer(), nullable=False),
        sa.Column('next_expected_date', sa.Date(), nullable=True),
        sa.Column('last_seen_date', sa.Date(), nullable=True),
        sa.Column('confidence_score', sa.Float(), nullable=True),
        sa.Column('occurrences', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['household_id'], ['households.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_subscriptions_vendor_id', 'subscriptions', ['vendor_id'])


def downgrade() -> None:
    op.drop_index('ix_subscriptions_vendor_id', table_name='subscriptions')
    op.drop_table('subscriptions')
