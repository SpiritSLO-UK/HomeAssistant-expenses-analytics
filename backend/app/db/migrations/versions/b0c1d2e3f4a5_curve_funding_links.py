"""curve funding links + transactions.funding_source (cross-account dedup)

Curve is an overlay card: each payment is forwarded to an underlying funding
card, so the same spend also appears on that card's own statement. We record the
funding-card label on each Curve transaction (``transactions.funding_source``)
and let the user map each label to a real account (``curve_funding_links``), so
the duplicate can be recognised across the two statements.

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-06-07 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b0c1d2e3f4a5'
down_revision: str | None = 'a9b0c1d2e3f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('transactions', sa.Column('funding_source', sa.String(length=120), nullable=True))
    op.create_index('ix_transactions_funding_source', 'transactions', ['funding_source'])

    op.create_table(
        'curve_funding_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('household_id', sa.Integer(), sa.ForeignKey('households.id', ondelete='CASCADE'), nullable=True),
        sa.Column('label', sa.String(length=120), nullable=False),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('accounts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('household_id', 'label', name='uq_curve_funding_links_household_label'),
    )


def downgrade() -> None:
    op.drop_table('curve_funding_links')
    op.drop_index('ix_transactions_funding_source', table_name='transactions')
    op.drop_column('transactions', 'funding_source')
